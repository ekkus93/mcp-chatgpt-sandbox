"""`exec` — synchronous command execution (§14, §16, S12) — plus
`exec_start` / `exec_status` / `exec_output` / `exec_cancel` — background
jobs (§15.3, §63, §64, S15/S16).

Real timeout enforcement lives at this async layer, not inside
`ExecutionService`: `asyncio.wait_for` is what can actually interrupt a
blocked read; the service module explains why in its own docstring.

The async job tools query `JobStore` for identity and terminal-state
fields; a running job's live byte counts come from `AsyncJobRunner`'s
in-memory `LiveJob` (cheap counters only), while actual output content —
for both a running and a finished job — is read straight from its
capped, host-side log files (§15.6, `execution/log_capture.py`'s
`read_log_bytes`), which is what makes it available even for a job this
process didn't start (e.g. after a restart, before its logs are pruned).
"""

from __future__ import annotations

import asyncio
import secrets
import shlex
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
from dev_workspace_mcp.execution.async_runner import AsyncJobRunner
from dev_workspace_mcp.execution.log_capture import read_log_bytes
from dev_workspace_mcp.execution.service import ExecutionService, tail_text
from dev_workspace_mcp.jobs.retention import prune_job_logs
from dev_workspace_mcp.jobs.store import JobRecord, JobStore
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    get_authorized,
    touch_last_used,
)

#: How long to wait after TERM before escalating to KILL on a timed-out command.
KILL_GRACE_PERIOD_SECONDS = 2.0


def _new_job_id() -> str:
    #: Same `secrets.token_hex(4)` convention as ephemeral workspace ids
    #: (workspace/manager.py).
    return f"job_{secrets.token_hex(4)}"


def _describe_command(argv: list[str] | None, shell: str | None) -> str:
    """A human-readable rendering for `JobRecord.command` (§15.2) — the
    original request, not the argv `_build_cmd` actually execs (which
    wraps shell form in `["bash", "-c", ...]`)."""
    if shell is not None:
        return shell
    assert argv is not None  # `_build_cmd` already enforced exactly one of the two
    return shlex.join(argv)


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float:
    """Fallback for a job with no live entry (see module docstring) —
    computed from JobStore's own timestamps rather than a monotonic
    clock, since no process in this daemon instance ever timed it."""
    if started_at is None:
        return 0.0
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(finished_at) if finished_at is not None else datetime.now(UTC)
    return (end - start).total_seconds()


@dataclass
class ExecOutput:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_total_bytes: int
    stderr_total_bytes: int


@dataclass
class ExecStartOutput:
    job_id: str
    state: str
    started_at: str | None


@dataclass
class ExecStatusOutput:
    job_id: str
    state: str
    exit_code: int | None
    elapsed_seconds: float
    stdout_tail: str
    stderr_tail: str
    stdout_total_bytes: int
    stderr_total_bytes: int
    output_truncated: bool


@dataclass
class ExecOutputOutput:
    job_id: str
    stream: str
    content: str
    stream_total_bytes: int
    truncated: bool
    logs_pruned: bool


@dataclass
class ExecCancelOutput:
    job_id: str
    state: str


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
    registry: RepositoryRegistry,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    state_dir: Path,
) -> None:
    manager = DockerWorkspaceManager(docker_client, db) if docker_client is not None else None
    service = ExecutionService(docker_client) if docker_client is not None else None
    job_store = JobStore(db)
    runner = AsyncJobRunner(service, job_store) if service is not None else None

    def _status_output(record: JobRecord) -> ExecStatusOutput:
        live = runner.live_job(record.id) if runner is not None else None
        if live is not None:
            elapsed = live.elapsed_seconds()
            stdout_total = live.stdout_capture.total_bytes
            stderr_total = live.stderr_capture.total_bytes
            truncated = live.stdout_capture.truncated or live.stderr_capture.truncated
        else:
            elapsed = _elapsed_seconds(record.started_at, record.finished_at)
            stdout_total = record.stdout_total_bytes
            stderr_total = record.stderr_total_bytes
            truncated = record.output_truncated
        return ExecStatusOutput(
            job_id=record.id,
            state=record.state.value,
            exit_code=record.exit_code,
            elapsed_seconds=elapsed,
            stdout_tail=tail_text(
                read_log_bytes(record.stdout_log_path), execution_config.response_tail_lines
            ),
            stderr_tail=tail_text(
                read_log_bytes(record.stderr_log_path), execution_config.response_tail_lines
            ),
            stdout_total_bytes=stdout_total,
            stderr_total_bytes=stderr_total,
            output_truncated=truncated,
        )

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

        record = get_authorized(manager, workspace_id, registry)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING
        touch_last_used(db, workspace_id)  # §8.1: this is real usage, not idle

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

    @server.tool(name="exec_start", description="Start a command as a background job (§15.3, §63).")
    @translate_workspace_errors
    async def exec_start_tool(
        workspace_id: str,
        argv: list[str] | None = None,
        shell: str | None = None,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecStartOutput:
        if manager is None or service is None or docker_client is None or runner is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot start jobs"
            )

        record = get_authorized(manager, workspace_id, registry)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING
        touch_last_used(db, workspace_id)  # §8.1: this is real usage, not idle

        cmd = _build_cmd(argv, shell)
        _validate_cwd(cwd, record.workspace_path)
        user = resolve_exec_user(container_config)
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else execution_config.default_job_timeout_seconds
        )

        job_id = _new_job_id()
        job_dir = state_dir / "jobs" / job_id
        stdout_log_path = job_dir / "stdout.log"
        stderr_log_path = job_dir / "stderr.log"
        job_store.create(
            job_id=job_id,
            workspace_id=workspace_id,
            command=_describe_command(argv, shell),
            cwd=cwd,
            stdout_log_path=str(stdout_log_path),
            stderr_log_path=str(stderr_log_path),
        )
        await runner.start_job(
            job_id,
            container_id=record.container_id,
            cmd=cmd,
            cwd=cwd,
            user=user,
            env=env,
            timeout_seconds=timeout,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            max_output_bytes=execution_config.max_output_bytes,
            tail_keep_bytes=execution_config.tail_keep_bytes,
        )
        started = job_store.get(job_id)

        # §15.6's retention "on a schedule and on demand" — no scheduler
        # exists elsewhere in the daemon, so this opportunistic call on
        # every new job is the on-demand half, standing in for a real
        # schedule (see jobs/retention.py's module docstring). Not run
        # via `asyncio.to_thread`: `job_store` shares this request's
        # `sqlite3.Connection`, which is thread-affine (`check_same_thread`
        # defaults to `True`) — using it from a worker thread raises.
        # `prune_job_logs` itself is cheap (small metadata reads, the
        # occasional `stat`/`unlink`), so running it inline is fine.
        prune_job_logs(
            job_store,
            job_retention_days=execution_config.job_retention_days,
            job_log_max_total_bytes=execution_config.job_log_max_total_bytes,
        )

        return ExecStartOutput(
            job_id=job_id, state=started.state.value, started_at=started.started_at
        )

    @server.tool(
        name="exec_status",
        description="Report a job's current state and recent output (§15.3, §64).",
    )
    @translate_workspace_errors
    async def exec_status_tool(job_id: str) -> ExecStatusOutput:
        return _status_output(job_store.get(job_id))

    @server.tool(
        name="exec_output",
        description="Retrieve a job's captured output by offset, range, or line window (§15.3).",
    )
    @translate_workspace_errors
    async def exec_output_tool(
        job_id: str,
        stream: str = "stdout",
        byte_start: int | None = None,
        byte_end: int | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        max_bytes: int | None = None,
    ) -> ExecOutputOutput:
        if stream not in ("stdout", "stderr"):
            raise InvalidArgumentError(
                f"stream must be 'stdout' or 'stderr', not {stream!r}",
                details={"stream": stream},
            )
        has_byte_range = byte_start is not None or byte_end is not None
        has_line_range = line_start is not None or line_end is not None
        if has_byte_range and has_line_range:
            raise InvalidArgumentError(
                "byte_start/byte_end and line_start/line_end are mutually exclusive"
            )

        record = job_store.get(job_id)  # existence check; raises JobNotFoundError
        live = runner.live_job(job_id) if runner is not None else None
        if live is not None:
            stream_total_bytes = (
                live.stdout_capture.total_bytes
                if stream == "stdout"
                else live.stderr_capture.total_bytes
            )
        else:
            stream_total_bytes = (
                record.stdout_total_bytes if stream == "stdout" else record.stderr_total_bytes
            )
        log_path = record.stdout_log_path if stream == "stdout" else record.stderr_log_path
        data = read_log_bytes(log_path)  # bounded on disk (§15.6); may be < stream_total_bytes

        if has_line_range:
            lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
            text = "".join(lines[line_start or 0 : line_end])
        elif has_byte_range:
            text = data[byte_start or 0 : byte_end].decode("utf-8", errors="replace")
        else:
            text = data.decode("utf-8", errors="replace")

        # Default limit: the whole capture (§15.6 already bounded what's on
        # disk to max_output_bytes + tail_keep_bytes + a small marker), not
        # max_output_bytes alone — that would re-truncate the response
        # right where the omission marker and kept tail live, hiding the
        # one part of a capped log that says something was capped.
        limit = execution_config.max_output_bytes + execution_config.tail_keep_bytes
        if max_bytes is not None:
            limit = min(max_bytes, limit)
        encoded = text.encode("utf-8")
        truncated = len(encoded) > limit
        if truncated:
            text = encoded[:limit].decode("utf-8", errors="ignore")

        return ExecOutputOutput(
            job_id=job_id,
            stream=stream,
            content=text,
            stream_total_bytes=stream_total_bytes,
            truncated=truncated,
            logs_pruned=record.logs_pruned,
        )

    @server.tool(name="exec_cancel", description="Request cancellation of a running job (§15.3).")
    @translate_workspace_errors
    async def exec_cancel_tool(job_id: str) -> ExecCancelOutput:
        job_store.get(job_id)  # existence check; raises JobNotFoundError
        if runner is not None:
            await runner.cancel_job(job_id)
        record = job_store.get(job_id)
        return ExecCancelOutput(job_id=job_id, state=record.state.value)
