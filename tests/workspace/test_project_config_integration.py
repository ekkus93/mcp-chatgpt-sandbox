"""S30: `.mcp-workspace.yml` (§9, §10), named cache volumes (§11), and
image provenance (§9.2) through the real `workspace_open` tool, against a
real Docker daemon and the real S7 default image.

`.devcontainer/devcontainer.json` *support* (§30) is still not
implemented — see `project_config.py`'s own module docstring — so the
precedence chain this task implements still has two effective levels:
`.mcp-workspace.yml`'s `image`, then `docker.default_image`. H32
(`docs/HARDENING_SPEC.md` §9's §30 row) adds the one piece that *is*
tested here: reporting that a present `devcontainer.json` is being
silently ignored, rather than giving no indication at all.
"""

import subprocess
from pathlib import Path

import pytest
import yaml
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.caches import cache_volume_name
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import persistent_workspace_id
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


def _init_repo(
    repo_path: Path, *, project_config: dict | None = None, devcontainer: bool = False
) -> None:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    if project_config is not None:
        (repo_path / ".mcp-workspace.yml").write_text(yaml.safe_dump(project_config))
    if devcontainer:
        devcontainer_dir = repo_path / ".devcontainer"
        devcontainer_dir.mkdir()
        (devcontainer_dir / "devcontainer.json").write_text('{"image": "mcr.microsoft.com/x"}\n')
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example"):
    return await server.call_tool("workspace_open", {"repository_id": repository_id})


# --- image precedence and environment ----------------------------------------------


async def test_project_config_image_overrides_the_default(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path, project_config={"version": 1, "image": default_image_tag})
    # a bogus daemon default the project config must NOT actually use
    config = _configured(minimal_config, "this-image-does-not-exist:latest", repo_path)
    server = create_server(config)

    result = await _open(server)

    assert result.structured_content["image"] == default_image_tag


async def test_environment_from_project_config_reaches_the_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    # Deliberately NOT `MCP_`-prefixed: that namespace is reserved for the
    # daemon's own control variables (HR-006, see this file's own
    # `test_at_hat005_*` tests below) and is rejected at project-config
    # load time regardless of whether the specific name collides with one
    # the daemon currently uses.
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path, project_config={"version": 1, "environment": {"PROJECT_TEST_VAR": "hello-s30"}}
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "echo $PROJECT_TEST_VAR"}
    )
    assert result.structured_content["stdout_tail"].strip() == "hello-s30"


# --- HR-006 (docs/HARDENING_SPEC.md §6): environment cannot escalate identity -------


async def test_at_hat005_project_environment_cannot_run_the_container_as_root(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A real regression, reproduced exactly this way during the
    2026-08-27 review: `.mcp-workspace.yml` setting `environment:
    {MCP_UID: "0"}` used to reach the container unfiltered and, merged
    last in `build_workspace_run_kwargs`, silently override the daemon-
    computed identity — PID 1 ran as root on a daemon with no
    user-namespace remapping, and a container write to the bind mount
    produced a root-owned host file. `workspace_open` now fails at
    project-config load time, before any container is created."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path, project_config={"version": 1, "environment": {"MCP_UID": "0"}})
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    with pytest.raises(ToolError, match="daemon-controlled variable"):
        await _open(server)

    # no container was created at all for this attempt.
    assert docker_client.containers.list(all=True, filters={"name": "mcp-dev-ws-example"}) == []


async def test_at_g2r001_project_environment_cannot_set_an_exported_shell_function(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-001 (`docs/GAP2_SPEC.md`): the reproduced live bypass, from
    the daemon's own client-facing entry point rather than
    `load_project_config` directly — `test_at_g2r001_*` in
    `tests/test_project_config.py` covers the validator in isolation;
    this is the end-to-end half the checklist asks for. `workspace_open`
    fails at project-config load time, exactly as `test_at_hat005_*`
    above proves for the sibling HR-006 check, and — the property that
    actually matters here — no container is ever created for the
    attempt: this key never reaches the entrypoint at all, which is
    what makes it a fix rather than a mitigation."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "environment": {"BASH_FUNC_id%%": "() { echo 1234; }"}},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    with pytest.raises(ToolError, match="exported shell function"):
        await _open(server)

    # no container was created at all for this attempt.
    assert docker_client.containers.list(all=True, filters={"name": "mcp-dev-ws-example"}) == []


async def test_at_g2r002_project_environment_cannot_redirect_gits_configuration(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-002 (`docs/GAP2_SPEC.md`): the end-to-end half, alongside
    `test_at_g2r001_*` above — `GAP2_SPEC.md`'s own G2AT-002 says "the
    same, for GIT_CONFIG_SYSTEM," which post-dates G2AT-001 being split
    into a validator half and this end-to-end half, so this removes the
    ambiguity that split left rather than assuming the validator test
    alone was always sufficient."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "environment": {"GIT_CONFIG_SYSTEM": "/tmp/evil.cfg"}},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    with pytest.raises(ToolError, match="interpreter or command resolution"):
        await _open(server)

    assert docker_client.containers.list(all=True, filters={"name": "mcp-dev-ws-example"}) == []


async def test_no_project_config_falls_back_to_the_default_image(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path)  # no .mcp-workspace.yml at all
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    result = await _open(server)

    assert result.structured_content["image"] == default_image_tag


# --- setup steps: run once, on genuine creation -------------------------------------


async def test_setup_steps_run_on_creation_and_write_a_real_file(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "setup": ["echo setup-ran > /tmp/setup-marker"]},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "cat /tmp/setup-marker"}
    )
    assert result.structured_content["stdout_tail"].strip() == "setup-ran"


async def test_setup_steps_do_not_rerun_on_a_reopened_persistent_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={
            "version": 1,
            # appends, so a second run would be observable as two lines
            "setup": ["echo ran >> /tmp/setup-count"],
        },
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    first = await _open(server)
    workspace_id = first.structured_content["workspace_id"]
    second = await _open(server)
    assert second.structured_content["workspace_id"] == workspace_id  # same, reused workspace

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "wc -l < /tmp/setup-count"}
    )
    assert result.structured_content["stdout_tail"].strip() == "1"


async def test_a_failing_setup_step_raises_a_clear_error(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path, project_config={"version": 1, "setup": ["false"]})
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await _open(server)


async def test_at_g2r012_a_failing_setup_step_is_retried_on_the_next_open(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-012 (`docs/GAP2_SPEC.md`): the old `was_created` check only
    ever compared "did the DB row already exist" — true exactly once,
    on genuine creation — so a setup step failing transiently (a flaky
    network call, a package registry hiccup) left the row in place with
    setup **permanently** skipped on every subsequent `workspace_open`,
    even though the container itself survives a failed step and stays
    running. The first line of `setup` below runs (and is observable)
    on *every* attempt; the second always fails, so both opens raise —
    what this proves is that the first line ran twice, not once."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={
            "version": 1,
            "setup": ["echo attempt >> /tmp/setup-attempts", "false"],
        },
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await _open(server)
    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await _open(server)  # SHALL retry setup, not silently skip it forever

    workspace_id = persistent_workspace_id("example")
    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "wc -l < /tmp/setup-attempts"}
    )
    assert result.structured_content["stdout_tail"].strip() == "2"


async def test_at_g2r012_setup_reruns_for_a_container_recreated_behind_the_same_row(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-012's other half: a container removed out of band (`docker
    system prune`, or here, a direct `container.remove(force=True)`
    standing in for it) makes `ensure_workspace`'s HR-017 branch
    (`manager_lifecycle.py`) recreate a genuinely empty container behind
    the *same*, unchanged workspace row — the old check never re-ran
    setup for it, since the row was never new. `setup_completed_
    container_id` no longer matches the replacement's id, so this must
    re-run — appends, so a second run is observable as two lines."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "setup": ["echo ran >> /tmp/setup-count"]},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    first = await _open(server)
    workspace_id = first.structured_content["workspace_id"]
    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    container_id = manager.get(workspace_id).container_id
    assert container_id is not None
    docker_client.containers.get(container_id).remove(force=True)

    second = await _open(server)
    assert second.structured_content["workspace_id"] == workspace_id  # same row, reused
    assert manager.get(workspace_id).container_id != container_id  # genuinely new container

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "cat /tmp/setup-count"}
    )
    assert result.structured_content["stdout_tail"].strip() == "ran"


# --- named cache volumes -------------------------------------------------------------


async def test_cache_volume_is_mounted_writable_and_owned_by_the_container_user(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo"}]},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    # writable without sudo — proves the daemon uid, not root, owns it
    write_result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "echo cached > ~/.cargo/marker && cat ~/.cargo/marker",
        },
    )
    assert write_result.structured_content["exit_code"] == 0, write_result.structured_content
    assert write_result.structured_content["stdout_tail"].strip() == "cached"

    # a real, separate mount point — not just a directory under $HOME
    mount_result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "mount | grep -c ' /home/mcpuser/.cargo '"}
    )
    assert mount_result.structured_content["stdout_tail"].strip() == "1"

    # H23 (`docs/HARDENING_TODO.md`, HR-009): hash-derived, not the plain
    # `mcp-cache-example-cargo` this asserted before that task.
    volume_names = [v.name for v in docker_client.volumes.list()]
    assert cache_volume_name(repository_id="example", cache_name="cargo") in volume_names


async def test_cache_volume_persists_across_a_stop_and_start_cycle(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(
        repo_path,
        project_config={"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo"}]},
    )
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]
    await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "echo persisted > ~/.cargo/marker"}
    )

    db = open_db(config.state_db_path)
    ssh_agent_manager = (
        SSHAgentManager(db, runtime_dir=config.state_db_path.parent / "ssh-agents")
        if config.ssh.mode == "agent"
        else None
    )
    manager = DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agent_manager)
    manager.stop(workspace_id)
    manager.start(workspace_id)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "cat ~/.cargo/marker"}
    )
    assert result.structured_content["stdout_tail"].strip() == "persisted"


async def test_two_repositories_get_independent_cache_volumes(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "work" / "repo-a"
    repo_b = tmp_path / "work" / "repo-b"
    _init_repo(
        repo_a, project_config={"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo"}]}
    )
    _init_repo(
        repo_b, project_config={"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo"}]}
    )
    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="repo-a", path=repo_a, github="owner/repo-a"),
                RepositoryConfig(id="repo-b", path=repo_b, github="owner/repo-b"),
            ],
        }
    )
    server = create_server(config)
    opened_a = await _open(server, "repo-a")
    opened_b = await _open(server, "repo-b")

    await server.call_tool(
        "exec",
        {
            "workspace_id": opened_a.structured_content["workspace_id"],
            "shell": "echo repo-a > ~/.cargo/marker",
        },
    )
    result_b = await server.call_tool(
        "exec",
        {
            "workspace_id": opened_b.structured_content["workspace_id"],
            "shell": "cat ~/.cargo/marker 2>/dev/null; true",
        },
    )
    assert (
        result_b.structured_content["stdout_tail"].strip() == ""
    )  # repo-b never saw repo-a's write


# --- image provenance (§9.2) --------------------------------------------------------


async def test_image_provenance_is_recorded_and_reported(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    # H32: `version` is validated now (`test_at_h032_an_unsupported_version_is_rejected`
    # in `tests/test_project_config.py`) — `1` is the only value this
    # daemon accepts, not an arbitrary probe value.
    _init_repo(repo_path, project_config={"version": 1})
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    opened = await _open(server)
    content = opened.structured_content
    assert content["image"] == default_image_tag
    assert content["image_digest"]  # a real image id, non-empty
    assert content["config_version"] == 1
    assert content["created_at"]

    info = await server.call_tool("workspace_info", {"workspace_id": content["workspace_id"]})
    assert info.structured_content["image_digest"] == content["image_digest"]
    assert info.structured_content["config_version"] == 1


# --- AT-004: persistent package install survives a reopen --------------------------


async def test_at_004_persistent_package_install_survives_stop_and_reopen(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The spec names `cargo-nextest`; this substitutes a small, always-
    available real Debian package (`tree`) for the same reason S24's
    AT-034 substituted a generic local `sshd` for `github.com`'s — the
    mechanism under test is "a tool installed after creation survives a
    stop and reopen," not any particular package."""
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path)
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    install = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "sudo apt-get update -qq && sudo apt-get install -y -qq tree",
            "timeout_seconds": 120,
        },
    )
    assert install.structured_content["exit_code"] == 0, install.structured_content

    db = open_db(config.state_db_path)
    ssh_agent_manager = (
        SSHAgentManager(db, runtime_dir=config.state_db_path.parent / "ssh-agents")
        if config.ssh.mode == "agent"
        else None
    )
    manager = DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agent_manager)
    manager.stop(workspace_id)

    # "restart the MCP session, reopen the same persistent workspace" —
    # a fresh `create_server` call, the same repository id, same config.
    reopened_server = create_server(config)
    reopened = await _open(reopened_server)
    assert reopened.structured_content["workspace_id"] == workspace_id

    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "tree --version"}
    )
    assert check.structured_content["exit_code"] == 0, check.structured_content


# --- H32: a present devcontainer.json is reported, not silently ignored ------------


async def test_at_h032_a_present_devcontainer_json_is_reported_as_ignored(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path, devcontainer=True)
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]
    info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})

    assert opened.structured_content["devcontainer_ignored"] is True
    assert info.structured_content["devcontainer_ignored"] is True


async def test_no_devcontainer_json_is_not_reported_as_ignored(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path)
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)

    opened = await _open(server)

    assert opened.structured_content["devcontainer_ignored"] is False
