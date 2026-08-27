"""S23: SSH key provisioning (`ssh.mode: agent`) through the real
`workspace_open` tool, against a real Docker daemon and the real S7
default image (which already has `openssh-client` installed, per S24's
own tests). D3 was decided manual: the operator provisions the deploy
key by hand and configures `repositories[].ssh_key`; this suite provides
that key itself, via a real `ssh-keygen`, exactly as an operator would.
"""

import contextlib
import os
import signal
import subprocess
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


@pytest.fixture(autouse=True)
def _cleanup_spawned_agents(minimal_config: ServerConfig):
    """Every test in this file that calls `workspace_open` under
    `ssh.mode: agent` spawns a real, host-side `ssh-agent` process — a
    process, not a Docker resource, so the `docker_client` fixture's
    container-teardown does not (and should not) touch it. Kill whatever
    this test actually started, looked up from the same state db it used,
    regardless of test outcome."""
    yield
    db = open_db(minimal_config.state_db_path)
    try:
        manager = SSHAgentManager(
            db, runtime_dir=minimal_config.state_db_path.parent / "ssh-agents"
        )
        for record in manager.list_all():
            with contextlib.suppress(ProcessLookupError):
                os.kill(record.pid, signal.SIGTERM)
    finally:
        db.close()


def _real_keypair(tmp_path: Path, name: str = "example.ed25519") -> Path:
    key_path = tmp_path / name
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)
    return key_path


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path, key_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "ssh": minimal_config.ssh.model_copy(update={"keys_dir": key_path.parent}),
            "repositories": [
                RepositoryConfig(
                    id="example", path=repo_path, github="owner/example", ssh_key=key_path.name
                )
            ],
        }
    )


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_agent_socket_is_mounted_and_ssh_auth_sock_is_set(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    key_path = _real_keypair(tmp_path)
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example", key_path)
    server = create_server(config)
    workspace_id = await _open(server)

    env_result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "echo $SSH_AUTH_SOCK"}
    )
    assert env_result.structured_content["stdout_tail"].strip() == "/run/mcp/ssh/agent.sock"

    identities = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["ssh-add", "-l"]}
    )
    content = identities.structured_content
    assert content["exit_code"] == 0, content
    assert "no identities" not in content["stdout_tail"].lower()


async def test_file_mode_does_not_set_ssh_auth_sock(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The agent mount/env is specific to `ssh.mode: agent` (§19.2) —
    `file` mode (S24) has no agent lifecycle at all."""
    key_path = _real_keypair(tmp_path)
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example", key_path)
    config = config.model_copy(update={"ssh": config.ssh.model_copy(update={"mode": "file"})})
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": 'echo "[$SSH_AUTH_SOCK]"'}
    )
    assert result.structured_content["stdout_tail"].strip() == "[]"


async def test_at_013_no_private_key_material_is_discoverable_in_the_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    key_path = _real_keypair(tmp_path)
    # a distinctive substring from the real private key's own base64
    # payload — not the whole file, so the search is unambiguous even if
    # something else in the container happens to contain PEM boilerplate.
    key_content = key_path.read_text()
    distinctive_fragment = key_content.splitlines()[1]  # first real payload line
    assert len(distinctive_fragment) > 20  # sanity: this really is key material

    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example", key_path)
    server = create_server(config)
    workspace_id = await _open(server)

    # `-xdev`/`find`'s own filesystem-boundary equivalent: stay on the
    # container's own root filesystem, not descending into /proc's or
    # /sys's pseudo-files (unbounded, slow, and irrelevant — nothing gets
    # bind-mounted there) — a plain unrestricted `/` walk timed out.
    search = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": (
                f"grep -r -l --exclude-dir=proc --exclude-dir=sys "
                f"'{distinctive_fragment}' / 2>/dev/null; true"
            ),
            "timeout_seconds": 60,
        },
    )
    assert search.structured_content["stdout_tail"].strip() == ""

    # the keys_dir itself is never mounted anywhere in the container —
    # confirmed by name, not only by content, as a second, independent
    # check (a key renamed or re-encoded before being read would still
    # be caught by this one even if the content search above were not).
    find_result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": f"find / -xdev -name '{key_path.name}' 2>/dev/null; true",
            "timeout_seconds": 60,
        },
    )
    assert find_result.structured_content["stdout_tail"].strip() == ""


async def test_at_036_key_unloaded_on_stop_and_no_agent_after_delete(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    key_path = _real_keypair(tmp_path)
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example", key_path)
    server = create_server(config)
    workspace_id = await _open(server)

    # The same db/runtime_dir `workspace_open` used internally — there is
    # no `workspace_stop`/`workspace_delete` MCP tool yet (§34.3's own
    # deferral list), so this reaches the manager directly, the same way
    # `tests/workspace/test_execution_tools.py` already does for `stop`.
    db = open_db(config.state_db_path)
    ssh_agent_manager = SSHAgentManager(db, runtime_dir=config.state_db_path.parent / "ssh-agents")
    manager = DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agent_manager)

    agent_before = ssh_agent_manager.get(workspace_id)
    assert agent_before is not None

    manager.stop(workspace_id)

    agent_after_stop = ssh_agent_manager.get(workspace_id)
    assert agent_after_stop is not None
    assert agent_after_stop.pid == agent_before.pid  # AT-036: agent itself still running
    identities = subprocess.run(
        ["ssh-add", "-l"],
        env={"SSH_AUTH_SOCK": str(agent_after_stop.socket_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert "no identities" in identities.stdout.lower()  # the key came out

    manager.delete(workspace_id)

    assert ssh_agent_manager.get(workspace_id) is None  # AT-036: no agent after delete
    try:
        os.kill(agent_before.pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive
