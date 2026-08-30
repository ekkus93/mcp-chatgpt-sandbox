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
`read_log_bytes`/`read_log_tail`, seeking rather than slurping since H29),
which is what makes it available even for a job this process didn't
start (e.g. after a restart, before its logs are pruned).
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import secrets
import shlex
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mcp.server import MCPServer

import docker
from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import AuditConfig, ContainerConfig, ExecutionConfig, SSHConfig
from dev_workspace_mcp.errors import (
    CommandTimeoutError,
    ContainerRuntimeFailedError,
    InvalidArgumentError,
    RepositoryNotAllowedError,
    WorkspaceNotRunningError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.async_runner import AsyncJobRunner
from dev_workspace_mcp.execution.log_capture import read_log_bytes, read_log_tail, stat_log_bytes
from dev_workspace_mcp.execution.service import ExecutionService, tail_text
from dev_workspace_mcp.jobs.retention import prune_job_logs
from dev_workspace_mcp.jobs.store import JobRecord, JobStore
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceRecord,
    WorkspaceState,
    get_authorized,
    touch_last_used,
)
from dev_workspace_mcp.workspace.ssh_agent import resolve_ssh_key_path, ssh_key_fingerprint

#: How long to wait after TERM before escalating to KILL on a timed-out
#: command. Shared by `tools/workspace.py`'s `run_setup_steps` too
#: (G2R-016, `docs/GAP2_SPEC.md`) — a real cross-import, not a duplicated
#: constant kept in sync by hand: the two used to be separate constants
#: (`_SETUP_KILL_GRACE_PERIOD_SECONDS` there), which is exactly the shape
#: that let the OSError-handling `kill_after_timeout` below closes also
#: exist in only one of the two call sites for as long as it did.
KILL_GRACE_PERIOD_SECONDS = 2.0


async def kill_after_timeout(
    execution_service: ExecutionService,
    exec_id: str,
    *,
    workspace_id: str,
    timeout_seconds: float,
    grace_period_seconds: float = KILL_GRACE_PERIOD_SECONDS,
) -> None:
    """G2R-016 (`docs/GAP2_SPEC.md`): TERM, a grace period, then KILL —
    shared between `run_exec` below and `tools/workspace.py`'s
    `run_setup_steps`, extracted rather than left independently
    hand-copied so the two cannot diverge again the way they already
    had (`run_exec` handled a failed kill; the setup-step copy did not,
    letting `PermissionError`/`DockerException` escape past
    `@translate_workspace_errors` as an unstructured crash).

    `ExecutionService.kill`'s own docstring is explicit that both
    `PermissionError` (HR-020 — the daemon's own uid lacks permission to
    signal this pid) and a `docker.errors.DockerException` from
    `exec_inspect` (RR-013 — the container was removed out from under a
    still-running exec) are deliberately allowed to propagate, not
    swallowed as if the signal had succeeded. Caught here as
    `(OSError, docker.errors.DockerException)`, not `OSError` alone:
    `docker.errors.APIError` — the common case — chains through
    `HTTPError`/`RequestException` to `OSError`, so a narrower
    `except OSError` already covered it, but `kill`'s own docstring
    promises *any* `DockerException` from `exec_inspect`, and a
    non-`APIError` one (confirmed by checking `docker.errors`'
    hierarchy directly, not assumed) would still escape a
    plain `except OSError`."""
    try:
        await asyncio.to_thread(execution_service.kill, exec_id)
        await asyncio.sleep(grace_period_seconds)
        await asyncio.to_thread(execution_service.kill, exec_id, grace_period_reached=True)
    except (OSError, docker.errors.DockerException) as exc:
        raise ContainerRuntimeFailedError(
            f"command exceeded {timeout_seconds}s and could not be terminated: {exc}",
            details={"workspace_id": workspace_id, "timeout_seconds": timeout_seconds},
            recoverable=False,
        ) from exc


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
    """§15.6's uncapped total — how much the job actually produced. NOT
    what `byte_start`/`byte_end` index into once a stream has ever been
    truncated on disk (see `stored_bytes`) — the two used to be silently
    conflated (H29, `docs/HARDENING_TODO.md`, HD3)."""
    stored_bytes: int
    """What's actually retrievable from this endpoint — `byte_start`/
    `byte_end` index into this, not `stream_total_bytes`."""
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


def _validate_cwd(cwd: str, workspace_path: str) -> str:
    """Returns the normalized `cwd` — H30 (`docs/HARDENING_TODO.md`,
    HR-010): normalized *before* the containment test, and what's
    actually used for the exec call afterward, not just what's checked.
    A real regression: the old string-prefix check
    (`cwd.startswith(workspace_path + "/")`) accepted
    `"/workspace/../etc"` outright — it textually starts with
    `"/workspace/"` — even though it resolves to a path entirely outside
    the workspace. `posixpath`, not `os.path`: this is a *container*-side
    path, evaluated for a Linux container regardless of this daemon's own
    host OS."""
    normalized = posixpath.normpath(cwd)
    if normalized != workspace_path and not normalized.startswith(workspace_path.rstrip("/") + "/"):
        raise InvalidArgumentError(
            f"cwd must be {workspace_path!r} or a path beneath it, not {cwd!r} (§14.2)",
            details={"cwd": cwd, "workspace_path": workspace_path},
        )
    return normalized


def _resolve_timeout(timeout_seconds: float | None, *, ceiling: float) -> float:
    """§14.4 (`docs/HARDENING_SPEC.md` §9 amendment, H30/HR-010): a
    client-supplied timeout SHALL be clamped to the configured maximum
    (`execution.sync_timeout_seconds`/`default_job_timeout_seconds`), not
    honored unbounded — that config value is a real ceiling, not merely
    a default used only when the client omits the parameter."""
    if timeout_seconds is None:
        return ceiling
    if timeout_seconds <= 0:
        raise InvalidArgumentError(
            f"timeout_seconds must be > 0, not {timeout_seconds}",
            details={"timeout_seconds": timeout_seconds},
        )
    return min(timeout_seconds, ceiling)


def _audit_command_started(
    audit_config: AuditConfig,
    *,
    workspace_id: str,
    repository_id: str,
    argv: list[str] | None,
    shell: str | None,
    cwd: str,
) -> None:
    """§24's "command started" event (HR-028/AT-021): without this,
    nothing about a command's own invocation ever reached the audit log
    at all — AT-021 ("run a command containing a configured secret and
    verify audit output does not expose the secret value") had no audit
    output to inspect in the first place. `metadata` carries exactly the
    request shape a client gave (`argv`/`shell`/`cwd`), passed through
    `write_audit_event`'s existing `redact_secrets` (§24.1) — a
    GitHub-token-shaped value or `scheme://user:pass@host` embedded
    anywhere in `argv`/`shell` is caught by the same pattern matching
    `to_tool_error` already applies to a client-facing error message."""
    write_audit_event(
        audit_config,
        operation="exec",
        result="success",
        workspace_id=workspace_id,
        repository_id=repository_id,
        metadata={"argv": argv, "shell": shell, "cwd": cwd},
    )


def _audit_ssh_auth_if_applicable(
    audit_config: AuditConfig,
    ssh_config: SSHConfig,
    registry: RepositoryRegistry,
    record: WorkspaceRecord,
) -> None:
    """AT-037: under `ssh.mode: agent`, an `exec` against a workspace
    with a real ssh-agent mount (§19.2: persistent-mode workspaces whose
    repository resolves a key — the same condition `workspace_open`'s
    own mount-building code gates on; an ephemeral workspace or one with
    no key configured never gets an agent socket at all, so there is
    nothing to audit) produces an `ssh_auth` record carrying the key's
    fingerprint. Every check below is a silent skip, not an error: this
    is an audit *side effect* of a command that has already been
    authorized to run, not a second authorization gate — a repository
    that stopped resolving, or a key that fails to fingerprint, should
    never be what makes `exec` itself fail."""
    if ssh_config.mode != "agent" or record.mode != "persistent":
        return
    with contextlib.suppress(RepositoryNotAllowedError):
        repository = registry.get(record.repository_id)
        key_path = resolve_ssh_key_path(ssh_config, repository)
        if key_path is None:
            return
        fingerprint = ssh_key_fingerprint(key_path)
        if fingerprint is None:
            return
        write_audit_event(
            audit_config,
            operation="ssh_auth",
            result="success",
            workspace_id=record.id,
            repository_id=record.repository_id,
            metadata={"fingerprint": fingerprint},
        )


def reauthorized_job(
    job_id: str,
    *,
    job_store: JobStore,
    manager: DockerWorkspaceManager | None,
    registry: RepositoryRegistry,
) -> JobRecord:
    """RR-010/RR-011 (`docs/REMEDIATION_SPEC.md` §5): `exec`/`exec_start`
    both call `get_authorized`, but every job-*scoped* tool
    (`exec_status`/`exec_output`/`exec_cancel`) took only `job_id` and
    never touched the registry at all — a client holding a `job_id` from
    before deauthorization kept reading (or cancelling) that job
    indefinitely. Reproduced empirically: `exec_status` on a job started
    before removing its repository from configuration still returned the
    job's live state. `job_store.get` already returns `workspace_id`, so
    the re-check itself is the same one line `exec`/`exec_start` already
    use. Module-level, not a `register()` closure (R27,
    `docs/REMEDIATION_TODO.md`), so it can be unit-tested by constructing
    its dependencies directly rather than a whole running server.

    `manager` is `None` when Docker was unreachable at startup (§73) —
    `DockerWorkspaceManager.get` is itself a side-effect-free DB read (no
    live Docker call needed), but there is no instance to call it on in
    that state. Fails closed with `ContainerRuntimeFailedError`, matching
    `exec`/`exec_start`'s own degraded-mode behavior, rather than a raw
    `AttributeError` on `None.get(...)` or silently skipping the
    re-check."""
    record = job_store.get(job_id)
    if manager is None:
        raise ContainerRuntimeFailedError(
            "Docker is not reachable; the server is running in a "
            "degraded state (§73) and cannot verify job authorization"
        )
    get_authorized(manager, record.workspace_id, registry)
    return record


def status_output(
    record: JobRecord, *, runner: AsyncJobRunner | None, execution_config: ExecutionConfig
) -> ExecStatusOutput:
    """Module-level for the same reason `reauthorized_job` above is."""
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
            read_log_tail(record.stdout_log_path, max_lines=execution_config.response_tail_lines),
            execution_config.response_tail_lines,
        ),
        stderr_tail=tail_text(
            read_log_tail(record.stderr_log_path, max_lines=execution_config.response_tail_lines),
            execution_config.response_tail_lines,
        ),
        stdout_total_bytes=stdout_total,
        stderr_total_bytes=stderr_total,
        output_truncated=truncated,
    )


async def run_exec(
    workspace_id: str,
    argv: list[str] | None,
    shell: str | None,
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: float | None,
    *,
    manager: DockerWorkspaceManager,
    service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    audit_config: AuditConfig,
    ssh_config: SSHConfig,
) -> ExecOutput:
    """§14: the `exec` tool's full body — module-level, not a
    `register()` closure (R27, `docs/REMEDIATION_TODO.md`), so it can be
    unit-tested by constructing its dependencies directly rather than a
    whole running server. `register()`'s `exec_tool` is a thin wrapper:
    check Docker reachability, then call this."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    touch_last_used(db, workspace_id)  # §8.1: this is real usage, not idle

    cmd = _build_cmd(argv, shell)
    cwd = _validate_cwd(cwd, record.workspace_path)
    user = resolve_exec_user(container_config)
    timeout = _resolve_timeout(timeout_seconds, ceiling=execution_config.sync_timeout_seconds)

    container_id = record.container_id
    # HR-022 (`docs/HARDENING_SPEC.md` §10): `docker.errors.DockerException`
    # from either call — the container disappearing out from under
    # this exec, a Docker API error — used to escape uncaught, past
    # `@translate_workspace_errors` (which only shapes `WorkspaceError`
    # subclasses), reaching the client as an unstructured crash.
    try:
        exec_id = await asyncio.to_thread(
            service.start, container_id, cmd, cwd=cwd, user=user, env=env
        )
    except docker.errors.DockerException as exc:
        raise ContainerRuntimeFailedError(
            f"failed to start command in workspace {workspace_id!r}: {exc}",
            details={"workspace_id": workspace_id},
        ) from exc
    _audit_command_started(
        audit_config,
        workspace_id=workspace_id,
        repository_id=record.repository_id,
        argv=argv,
        shell=shell,
        cwd=cwd,
    )
    _audit_ssh_auth_if_applicable(audit_config, ssh_config, registry, record)

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
    except docker.errors.DockerException as exc:
        raise ContainerRuntimeFailedError(
            f"failed while running command in workspace {workspace_id!r}: {exc}",
            details={"workspace_id": workspace_id},
        ) from exc
    except TimeoutError:
        # HR-020 / G2R-016: `kill_after_timeout` is what handles a
        # failed kill (`PermissionError`/`DockerException`) as a
        # structured error rather than an unhandled crash — see its own
        # docstring. `consume`'s own blocked read (unlike `exec_start`'s
        # split start/drain) has no stream reference available here to
        # force-close on a failed kill — a stream that never EOFs
        # can still hold this call's `asyncio.to_thread` executor
        # slot indefinitely; scoped out of this task, which the
        # HARDENING_TODO.md write-up documents explicitly.
        await kill_after_timeout(
            service, exec_id, workspace_id=workspace_id, timeout_seconds=timeout
        )
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


async def run_exec_start(
    workspace_id: str,
    argv: list[str] | None,
    shell: str | None,
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: float | None,
    *,
    manager: DockerWorkspaceManager,
    runner: AsyncJobRunner,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    job_store: JobStore,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    audit_config: AuditConfig,
    ssh_config: SSHConfig,
    state_dir: Path,
) -> ExecStartOutput:
    """§15.3: the `exec_start` tool's full body — module-level for the
    same reason `run_exec` above is; see its docstring."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    touch_last_used(db, workspace_id)  # §8.1: this is real usage, not idle

    cmd = _build_cmd(argv, shell)
    cwd = _validate_cwd(cwd, record.workspace_path)
    user = resolve_exec_user(container_config)
    timeout = _resolve_timeout(
        timeout_seconds, ceiling=execution_config.default_job_timeout_seconds
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
    _audit_command_started(
        audit_config,
        workspace_id=workspace_id,
        repository_id=record.repository_id,
        argv=argv,
        shell=shell,
        cwd=cwd,
    )
    _audit_ssh_auth_if_applicable(audit_config, ssh_config, registry, record)

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

    return ExecStartOutput(job_id=job_id, state=started.state.value, started_at=started.started_at)


def run_exec_output(
    job_id: str,
    stream: str,
    byte_start: int | None,
    byte_end: int | None,
    line_start: int | None,
    line_end: int | None,
    max_bytes: int | None,
    *,
    job_store: JobStore,
    manager: DockerWorkspaceManager | None,
    registry: RepositoryRegistry,
    runner: AsyncJobRunner | None,
    execution_config: ExecutionConfig,
) -> ExecOutputOutput:
    """§15.3: the `exec_output` tool's full body — module-level for the
    same reason `run_exec` above is; see its docstring."""
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
    # H29 (HR-030/HD3): a negative value used to silently slice from
    # the end (Python's own slicing semantics) instead of the
    # documented "position N from the start" — reject outright
    # rather than accept a meaning nobody asked for.
    for name, value in (
        ("byte_start", byte_start),
        ("byte_end", byte_end),
        ("line_start", line_start),
        ("line_end", line_end),
        ("max_bytes", max_bytes),
    ):
        if value is not None and value < 0:
            raise InvalidArgumentError(f"{name} must be >= 0, not {value}", details={name: value})

    record = reauthorized_job(
        job_id, job_store=job_store, manager=manager, registry=registry
    )  # existence + RR-010 authorization check
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

    # H29: `stored_bytes` — what byte_start/byte_end actually index
    # into — is not `stream_total_bytes` (§15.6's uncapped production
    # total) once a stream has ever been truncated on disk; the two
    # used to be silently conflated, misleading a client paging by
    # byte offset. `os.stat`, not a read.
    stored_bytes = stat_log_bytes(log_path)

    # H29/HD3: the full on-disk capture stays reachable, just only
    # through an explicit max_bytes/ranged request, not by default —
    # the old default (max_output_bytes + tail_keep_bytes, up to
    # 10.25 MiB) defeated §15.5's whole stated purpose.
    max_limit = execution_config.max_output_bytes + execution_config.tail_keep_bytes
    limit = (
        min(max_bytes, max_limit)
        if max_bytes is not None
        else (execution_config.default_output_response_bytes)
    )

    if has_line_range:
        # Line boundaries aren't determinable without scanning — the
        # one case that still reads the whole on-disk capture rather
        # than a seek-bounded window.
        data = read_log_bytes(log_path)
        lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
        text = "".join(lines[line_start or 0 : line_end])
        encoded = text.encode("utf-8")
        truncated = len(encoded) > limit
        if truncated:
            text = encoded[:limit].decode("utf-8", errors="ignore")
    else:
        # H29: seeks rather than slurping — reads only the requested
        # window from disk, not the whole (possibly multi-megabyte)
        # capture just to slice it in Python afterward.
        start = byte_start or 0
        requested = (byte_end - start) if byte_end is not None else None
        capped = max(min(requested, limit) if requested is not None else limit, 0)
        # +1: a peek past the cap to detect "there's more retrievable
        # data" without reading it — `truncated` means exactly that,
        # not "we reached the end of the file."
        chunk = read_log_bytes(log_path, offset=start, max_bytes=capped + 1)
        truncated = len(chunk) > capped
        if truncated:
            text = chunk[:capped].decode("utf-8", errors="ignore")
        else:
            text = chunk.decode("utf-8", errors="replace")

    return ExecOutputOutput(
        job_id=job_id,
        stream=stream,
        content=text,
        stream_total_bytes=stream_total_bytes,
        stored_bytes=stored_bytes,
        truncated=truncated,
        logs_pruned=record.logs_pruned,
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
    audit_config: AuditConfig,
    ssh_config: SSHConfig,
) -> None:
    manager = DockerWorkspaceManager(docker_client, db) if docker_client is not None else None
    service = ExecutionService(docker_client) if docker_client is not None else None
    job_store = JobStore(db)
    runner = AsyncJobRunner(service, job_store) if service is not None else None

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
        return await run_exec(
            workspace_id,
            argv,
            shell,
            cwd,
            env,
            timeout_seconds,
            manager=manager,
            service=service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
            audit_config=audit_config,
            ssh_config=ssh_config,
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
        return await run_exec_start(
            workspace_id,
            argv,
            shell,
            cwd,
            env,
            timeout_seconds,
            manager=manager,
            runner=runner,
            registry=registry,
            db=db,
            job_store=job_store,
            container_config=container_config,
            execution_config=execution_config,
            audit_config=audit_config,
            ssh_config=ssh_config,
            state_dir=state_dir,
        )

    @server.tool(
        name="exec_status",
        description="Report a job's current state and recent output (§15.3, §64).",
    )
    @translate_workspace_errors
    async def exec_status_tool(job_id: str) -> ExecStatusOutput:
        record = reauthorized_job(job_id, job_store=job_store, manager=manager, registry=registry)
        return status_output(record, runner=runner, execution_config=execution_config)

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
        return run_exec_output(
            job_id,
            stream,
            byte_start,
            byte_end,
            line_start,
            line_end,
            max_bytes,
            job_store=job_store,
            manager=manager,
            registry=registry,
            runner=runner,
            execution_config=execution_config,
        )

    @server.tool(name="exec_cancel", description="Request cancellation of a running job (§15.3).")
    @translate_workspace_errors
    async def exec_cancel_tool(job_id: str) -> ExecCancelOutput:
        reauthorized_job(
            job_id, job_store=job_store, manager=manager, registry=registry
        )  # existence + RR-011 authorization check
        if runner is not None:
            await runner.cancel_job(job_id)
        record = job_store.get(job_id)
        return ExecCancelOutput(job_id=job_id, state=record.state.value)
