"""S11: `workspace_open`, `workspace_info`, `server_info` end to end —
the point where S5-S8 come together into a real running container
(§8.3, §25, AT-032)."""

import subprocess
from pathlib import Path

import docker
import pytest
from mcp.server import MCPServer

from mcp_chatgpt_sandbox.config.loader import ensure_known_hosts_file_exists
from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.jobs.store import JobStore
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.schema_export import CONTRACT_VERSION
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools import workspace as workspace_tools
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


def _server_with_no_docker(config: ServerConfig) -> MCPServer:
    """Registers the workspace tools with `docker_client=None`, matching
    §73's degraded-startup path — without going through `create_server`,
    which would itself try (and, in this sandbox, succeed) to reach the
    real Docker daemon."""
    server = MCPServer(name="t")
    db = open_db(config.state_db_path)
    registry = RepositoryRegistry(config)
    workspace_tools.register(
        server,
        db=db,
        registry=registry,
        docker_client=None,
        docker_config=config.docker,
        container_config=config.container,
        execution_config=config.execution,
        resources_config=config.resources,
        ssh_config=config.ssh,
        workspace_config=config.workspace,
        github_config=config.github,
        git_policy_config=config.git_policy,
        audit_config=config.audit,
        repository_roots=config.repository_roots,
        ssh_agent_runtime_dir=config.state_db_path.parent / "ssh-agents",
    )
    return server


async def test_workspace_open_creates_a_running_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    info = result.structured_content
    assert info["container_state"] == "running"
    assert info["repository_id"] == "example"
    assert info["workspace_id"] == "ws_example"


async def test_rr027_open_workspace_is_directly_callable_without_a_server(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """R27 (`docs/REMEDIATION_TODO.md`): the actual point of extracting
    `open_workspace` to module level — no `MCPServer`, `create_server`, or
    `server.call_tool` anywhere in this test, only the tool body's real
    dependencies constructed directly, proving it no longer needs a
    running server to exercise (unlike every other test in this file,
    which deliberately keeps testing the full `server.call_tool` path
    too — this is additional coverage, not a replacement for it)."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    # `create_server` normally does this before ever registering tools
    # (§19.3: the known_hosts bind mount needs a real file to exist, not
    # a directory Docker silently substitutes for a missing one) —
    # calling `open_workspace` directly, bypassing `create_server`
    # entirely, means this test has to establish that precondition
    # itself, the same as any other real caller not going through it would.
    ensure_known_hosts_file_exists(config)
    db = open_db(config.state_db_path)
    registry = RepositoryRegistry(config)
    manager = DockerWorkspaceManager(docker_client, db)
    execution_service = ExecutionService(docker_client)
    job_store = JobStore(db)

    info = await workspace_tools.open_workspace(
        "example",
        "persistent",
        manager=manager,
        docker_client=docker_client,
        execution_service=execution_service,
        registry=registry,
        db=db,
        job_store=job_store,
        docker_config=config.docker,
        container_config=config.container,
        execution_config=config.execution,
        resources_config=config.resources,
        ssh_config=config.ssh,
        workspace_config=config.workspace,
        github_config=config.github,
        git_policy_config=config.git_policy,
        audit_config=config.audit,
        repository_roots=config.repository_roots,
        ssh_agent_manager=None,
    )

    assert info.container_state == "running"
    assert info.repository_id == "example"
    assert info.workspace_id == "ws_example"


async def test_at_032_reported_modes_match_configuration(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    info = result.structured_content

    assert info["git_dir_mode"] == config.container.git_dir_mode
    assert info["ssh_key_mode"] == config.ssh.mode
    assert info["ssh_key_scope"] == config.ssh.scope
    assert info["remote_protection"]["status"] == "unverified"  # no API credential configured


async def test_workspace_open_reuses_the_persistent_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    first = await server.call_tool("workspace_open", {"repository_id": "example"})
    second = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert first.structured_content["workspace_id"] == second.structured_content["workspace_id"]


async def test_workspace_info_matches_workspace_open(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
    assert info.structured_content == opened.structured_content


async def test_workspace_info_on_a_stopped_workspace_has_no_git_state(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H36 (`docs/HARDENING_TODO.md`, 5/8): before this migration,
    `workspace_info`'s git block was Docker-free — it read the bind-mounted
    working tree directly, host-side, regardless of whether the workspace's
    container was running. Now that the read happens in-container, a
    stopped workspace (Docker itself still reachable — this is a
    per-workspace state, not the degraded-daemon case
    `test_workspace_info_fails_clearly_when_docker_unreachable` covers) has
    nothing to read from: the git block SHALL report all-`None`, not raise
    and not fall back to a stale value, while every other field
    (`container_state` in particular) still reports accurately."""
    # A real `git init`, not `_configured()`'s stub `.git` directory —
    # needs a real branch/commit for `opened`'s own git block to be
    # non-`None`, so the stopped case's all-`None` result is unambiguous.
    repo_path = tmp_path / "work" / "example"
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]
    assert opened.structured_content["git"]["branch"] == "main"  # sanity: running, has git state

    db = open_db(config.state_db_path)
    DockerWorkspaceManager(docker_client, db).stop(workspace_id)

    info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
    content = info.structured_content
    assert content["container_state"] == "stopped"
    assert content["git"] == {
        "branch": None,
        "head_sha": None,
        "clean": None,
        "upstream": None,
    }


async def test_workspace_open_reports_git_state(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    # A real `git init`, not `_configured()`'s stub `.git` directory —
    # `git checkout`/`commit` need an actual repository structure.
    repo_path = tmp_path / "work" / "example"
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True)
    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    git_state = result.structured_content["git"]
    assert git_state["branch"] == "main"
    assert git_state["clean"] is True
    assert git_state["head_sha"] is not None


async def test_at_g2r021_git_branch_is_reported_for_a_non_utf8_branch_name(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-021 (`docs/GAP2_SPEC.md`): a real regression —
    `read_basic_git_state`'s shared `_run` helper left `strict_utf8` at
    `run_git_in_container`'s own default (`True`) by omission. This
    task's write-up originally claimed the trigger was a non-UTF-8
    *file* path making `git.clean` go `null` — checked empirically and
    that specific claim did not hold: `git status --porcelain` (no
    `-z`, unlike `git/status.py`'s own call) C-quotes any non-ASCII byte
    in a path as a literal octal escape by git's own default
    `core.quotePath`, which this codebase never overrides, so the
    quoted output stays valid UTF-8 regardless of the real filename's
    bytes and `decode_utf8_lossy` never trips for an untracked file
    here. The genuine, reproducible trigger is a non-UTF-8 **branch
    name** instead: `git rev-parse --abbrev-ref HEAD` emits it
    unquoted, raw (confirmed directly against a real `git` binary) —
    checking one out used to make `branch` go `None` instead of
    reporting the name, the same "diagnostic field silently
    unavailable" shape, just from a different one of `_run`'s callers
    than originally claimed."""
    import os

    repo_path = tmp_path / "work" / "example"
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    hostile_branch = os.fsdecode(b"bad-\xff\xfe-branch")
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "-q", "-b", hostile_branch], check=True
    )
    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})

    git_state = result.structured_content["git"]
    assert git_state["branch"] is not None  # SHALL NOT be None
    assert "bad-" in git_state["branch"]
    assert git_state["branch"].encode("utf-8")  # SHALL NOT raise UnicodeEncodeError
    assert git_state["clean"] is True


async def test_server_info_reports_real_state(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    await server.call_tool("workspace_open", {"repository_id": "example"})

    result = await server.call_tool("server_info", {})
    info = result.structured_content
    assert info["docker_available"] is True
    assert info["active_workspaces"] >= 1
    assert info["configured_repositories"] == 1
    assert info["capabilities"]["exec.jobs"] is True
    assert info["running_jobs"] == 0
    assert info["contract_version"] == CONTRACT_VERSION


# --- degraded mode (§73) — docker_client is None -----------------------------------


async def test_workspace_open_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config = minimal_config.model_copy(
        update={
            "repositories": [
                RepositoryConfig(
                    id="example", path=tmp_path / "work" / "example", github="owner/example"
                )
            ]
        }
    )
    (tmp_path / "work" / "example" / ".git").mkdir(parents=True)
    server = _server_with_no_docker(config)

    with pytest.raises(Exception, match="Docker is not reachable"):
        await server.call_tool("workspace_open", {"repository_id": "example"})


async def test_workspace_info_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    server = _server_with_no_docker(minimal_config)
    with pytest.raises(Exception, match="Docker is not reachable"):
        await server.call_tool("workspace_info", {"workspace_id": "ws_example"})


async def test_server_info_does_not_raise_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    """Unlike `workspace_open`/`workspace_info`, `server_info` degrades
    gracefully rather than raising — its whole purpose is diagnostic
    reporting, including reporting that Docker is unavailable."""
    server = _server_with_no_docker(minimal_config)
    result = await server.call_tool("server_info", {})
    assert result.structured_content["docker_available"] is False
    assert result.structured_content["docker_version"] is None
