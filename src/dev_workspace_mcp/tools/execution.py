"""`exec` — synchronous command execution (§14, §16, S12).

Real timeout enforcement lives at this async layer, not inside
`ExecutionService`: `asyncio.wait_for` is what can actually interrupt a
blocked read; the service module explains why in its own docstring.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

from mcp.server import MCPServer

import docker
from dev_workspace_mcp.config.models import ContainerConfig, ExecutionConfig
from dev_workspace_mcp.errors import (
    CommandTimeoutError,
    ContainerRuntimeFailedError,
    InvalidArgumentError,
    WorkspaceNotRunningError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceState

#: How long to wait after TERM before escalating to KILL on a timed-out command.
KILL_GRACE_PERIOD_SECONDS = 2.0


@dataclass
class ExecOutput:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_total_bytes: int
    stderr_total_bytes: int


def _build_cmd(argv: list[str] | None, shell: str | None) -> list[str]:
    if (argv is None) == (shell is None):
        raise InvalidArgumentError(
            "exactly one of argv or shell must be given (§14.1)",
            details={"argv_given": argv is not None, "shell_given": shell is not None},
        )
    if argv is not None:
        if not argv:
            raise InvalidArgumentError("argv must not be empty")
        return argv
    assert shell is not None
    return ["bash", "-c", shell]


def _validate_cwd(cwd: str, workspace_path: str) -> None:
    if cwd != workspace_path and not cwd.startswith(workspace_path.rstrip("/") + "/"):
        raise InvalidArgumentError(
            f"cwd must be {workspace_path!r} or a path beneath it, not {cwd!r} (§14.2)",
            details={"cwd": cwd, "workspace_path": workspace_path},
        )


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> None:
    manager = DockerWorkspaceManager(docker_client, db) if docker_client is not None else None
    service = ExecutionService(docker_client) if docker_client is not None else None

    @server.tool(name="exec", description="Run a command synchronously in a workspace (§14).")
    @translate_workspace_errors
    async def exec_tool(
        workspace_id: str,
        argv: list[str] | None = None,
        shell: str | None = None,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecOutput:
        if manager is None or service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot execute commands"
            )

        record = manager.get(workspace_id)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING

        cmd = _build_cmd(argv, shell)
        _validate_cwd(cwd, record.workspace_path)
        user = resolve_exec_user(container_config)
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else execution_config.sync_timeout_seconds
        )

        container_id = record.container_id
        exec_id = await asyncio.to_thread(
            service.start, container_id, cmd, cwd=cwd, user=user, env=env
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    service.consume,
                    exec_id,
                    max_output_bytes=execution_config.max_output_bytes,
                    response_tail_lines=execution_config.response_tail_lines,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            await asyncio.to_thread(service.kill, exec_id)
            await asyncio.sleep(KILL_GRACE_PERIOD_SECONDS)
            await asyncio.to_thread(service.kill, exec_id, grace_period_reached=True)
            raise CommandTimeoutError(
                f"command exceeded {timeout}s and was terminated",
                details={"workspace_id": workspace_id, "timeout_seconds": timeout},
            ) from None

        return ExecOutput(
            exit_code=result.exit_code,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            stdout_total_bytes=result.stdout_total_bytes,
            stderr_total_bytes=result.stderr_total_bytes,
        )
