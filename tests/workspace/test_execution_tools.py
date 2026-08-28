"""S12: the `exec` tool, against a real Docker daemon and the real S7
default image (§14, §16)."""

import asyncio
import os
from pathlib import Path

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import ExecutionConfig, RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import execution as execution_tools
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
    *,
    execution: dict[str, object] | None = None,
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    update: dict[str, object] = {
        "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
        "repositories": [RepositoryConfig(id="example", path=repo_path, github="owner/example")],
    }
    if execution is not None:
        update["execution"] = ExecutionConfig.model_validate(execution)
    return minimal_config.model_copy(update=update)


async def _open_workspace(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    workspace_id: str = result.structured_content["workspace_id"]
    return workspace_id


async def test_exec_argv_runs_and_returns_exit_code_and_output(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["echo", "hello"]}
    )
    assert result.structured_content["exit_code"] == 0
    assert "hello" in result.structured_content["stdout_tail"]


async def test_exec_shell_form_runs(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "echo one && echo two"}
    )
    assert "one" in result.structured_content["stdout_tail"]
    assert "two" in result.structured_content["stdout_tail"]


async def test_exec_requires_exactly_one_of_argv_or_shell(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool("exec", {"workspace_id": workspace_id})
    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "argv": ["echo", "x"], "shell": "echo x"},
        )


async def test_exec_rejects_cwd_outside_the_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec", {"workspace_id": workspace_id, "argv": ["pwd"], "cwd": "/etc"}
        )


async def test_at_h030_exec_rejects_a_traversal_cwd_that_string_prefix_matching_missed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H30 (`docs/HARDENING_TODO.md`, HR-010): the old containment check
    was a raw string prefix (`cwd.startswith(workspace_path + "/")`) —
    `"/workspace/../etc"` textually starts with `"/workspace/"` and
    passed it outright, even though it resolves to `/etc`, entirely
    outside the workspace. Normalized before the check now."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "argv": ["pwd"], "cwd": "/workspace/../etc"},
        )


async def test_at_h030_exec_normalizes_a_legitimate_dotted_cwd(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The fix must not have broken a legitimate `cwd` that merely
    contains `.`/`..` segments but still resolves inside the workspace —
    validated separately from the rejection case above so a regression
    here is unambiguous."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "argv": ["pwd"], "cwd": "/workspace/../workspace"},
    )
    assert result.structured_content["stdout_tail"].strip() == "/workspace"


async def test_exec_runs_as_the_checkout_owner_not_root(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["id", "-u"]})
    assert result.structured_content["stdout_tail"].strip() == str(os.getuid())


async def test_exec_sudo_works(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool("exec", {"workspace_id": workspace_id, "shell": "sudo whoami"})
    assert result.structured_content["stdout_tail"].strip() == "root"


async def test_exec_passes_through_env(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "echo $MY_VAR",
            "env": {"MY_VAR": "hello-env"},
        },
    )
    assert "hello-env" in result.structured_content["stdout_tail"]


async def test_exec_writes_are_visible_on_the_host(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": "echo from-exec > /workspace/out.txt"},
    )
    assert (repo_path / "out.txt").read_text().strip() == "from-exec"


async def test_exec_real_timeout_actually_kills_the_process(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Not just that the call raises — that the sleeping process is
    actually gone afterward, proving `asyncio.wait_for` giving up is not
    the same as the command stopping."""
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"sync_timeout_seconds": 1},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="COMMAND_TIMEOUT"):
        await server.call_tool("exec", {"workspace_id": workspace_id, "shell": "sleep 60"})

    # Give the kill signal a moment to land, then confirm no `sleep 60`
    # survives inside the container. The classic `[s]leep` bracket idiom:
    # matches the real process without matching its own grep invocation
    # (whose argv also contains the literal text "sleep 60"), and "60"
    # specifically excludes the container's own long-running PID 1
    # (`sleep infinity`, the Dockerfile's default CMD).
    await asyncio.sleep(4)
    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "ps aux | grep '[s]leep 60' || echo NONE"}
    )
    assert "NONE" in check.structured_content["stdout_tail"]


async def test_at_h030_a_client_timeout_above_the_ceiling_is_clamped(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H30 (`docs/HARDENING_TODO.md`, HR-010): `execution.sync_timeout_seconds`
    is a real ceiling, not merely a default used only when the client
    omits `timeout_seconds` — a client asking for far more (100s) than
    the configured ceiling (1s) against a command that would otherwise
    run for 60s SHALL still time out around the ceiling, not the
    requested value."""
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"sync_timeout_seconds": 1},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started_at = asyncio.get_event_loop().time()
    with pytest.raises(ToolError, match="COMMAND_TIMEOUT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "shell": "sleep 60", "timeout_seconds": 100},
        )
    elapsed = asyncio.get_event_loop().time() - started_at
    assert elapsed < 30, f"took {elapsed}s — the 100s request was honored, not clamped to 1s"


async def test_at_h030_exec_rejects_a_non_positive_timeout(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "argv": ["true"], "timeout_seconds": 0},
        )
    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "argv": ["true"], "timeout_seconds": -5},
        )


async def test_exec_output_truncation(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"max_output_bytes": 100},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": "head -c 10000 /dev/zero | tr '\\0' 'x'"},
    )
    assert result.structured_content["stdout_truncated"] is True
    assert result.structured_content["stdout_total_bytes"] >= 10000


async def test_exec_on_a_stopped_workspace_fails_with_workspace_not_running(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    db = open_db(config.state_db_path)
    DockerWorkspaceManager(docker_client, db).stop(workspace_id)

    with pytest.raises(ToolError, match="WORKSPACE_NOT_RUNNING"):
        await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["true"]})


async def test_exec_fails_clearly_when_docker_unreachable(minimal_config: ServerConfig) -> None:
    """§73's degraded state: exec is one of the tools that cannot possibly
    work without Docker, and should say so plainly rather than hang or
    crash with an unrelated error."""
    server = MCPServer(name="t")
    db = open_db(minimal_config.state_db_path)
    execution_tools.register(
        server,
        db=db,
        docker_client=None,
        registry=RepositoryRegistry(minimal_config),
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        state_dir=minimal_config.state_db_path.parent,
        audit_config=minimal_config.audit,
        ssh_config=minimal_config.ssh,
    )
    with pytest.raises(ToolError, match="Docker is not reachable"):
        await server.call_tool("exec", {"workspace_id": "ws_example", "argv": ["true"]})


async def test_rr010_exec_status_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    """RR-010 (`docs/REMEDIATION_SPEC.md` §5): `exec_status`'s new
    authorization re-check needs `manager` (`DockerWorkspaceManager.get`
    is a side-effect-free DB read, but there is no *instance* to call it
    on at all when Docker is unreachable — `manager` is only ever
    constructed when `docker_client is not None`). Fails closed the same
    way `exec`/`exec_start` already do, rather than a raw `AttributeError`
    on `None.get(...)` or silently skipping the check. A job row (and its
    `repository`/`workspace` foreign-key parents — `tests/jobs/conftest.py`'s
    own established pattern) is inserted directly into the store —
    `exec_start` itself requires Docker, so it can't be used to create
    one here."""
    from dev_workspace_mcp.jobs.store import JobStore

    server = MCPServer(name="t")
    db = open_db(minimal_config.state_db_path)
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('example', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_example', 'example', 'persistent', '/workspace', 'running',
                '2026-01-01', '2026-01-01')
        """
    )
    db.commit()
    execution_tools.register(
        server,
        db=db,
        docker_client=None,
        registry=RepositoryRegistry(minimal_config),
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        state_dir=minimal_config.state_db_path.parent,
        audit_config=minimal_config.audit,
        ssh_config=minimal_config.ssh,
    )
    JobStore(db).create(
        job_id="job_example",
        workspace_id="ws_example",
        command="true",
        cwd="/workspace",
        stdout_log_path="/tmp/does-not-matter-stdout.log",
        stderr_log_path="/tmp/does-not-matter-stderr.log",
    )

    with pytest.raises(ToolError, match="Docker is not reachable"):
        await server.call_tool("exec_status", {"job_id": "job_example"})
