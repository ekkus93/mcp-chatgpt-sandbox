"""§14.4's promotion of a synchronous `exec` to a background job
(JPO-006, `docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.3).

Only a call that set `promote_on_timeout` (D1-A) comes through here.
Everything else still takes `run_exec`'s original `start` + `consume` +
kill-on-timeout path untouched (I1) — which is why this is a second path
beside that one rather than a rewrite of it: `exec` is the most-used
tool on the surface, and the way to be sure a new feature cannot change
what existing callers get is for them not to execute any of it.

The command is never restarted (I2). It is started through
`start_and_open_stream` and drained through a `PromotableSink` from its
first byte; on overrun the sink is switched to the job's log files and
the *same* in-flight drain is handed to `AsyncJobRunner.adopt_running`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import docker

from mcp_chatgpt_sandbox.audit import write_audit_event
from mcp_chatgpt_sandbox.config.models import AuditConfig, ExecutionConfig
from mcp_chatgpt_sandbox.errors import CommandTimeoutError, ContainerRuntimeFailedError
from mcp_chatgpt_sandbox.execution.async_runner import AsyncJobRunner
from mcp_chatgpt_sandbox.execution.promotable_sink import PromotableSink
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.jobs.retention import prune_job_logs
from mcp_chatgpt_sandbox.jobs.store import NON_TERMINAL_STATES, JobState, JobStore
from mcp_chatgpt_sandbox.operations.pressure import classify_container_command_failure
from mcp_chatgpt_sandbox.tools.execution_models import ExecOutput
from mcp_chatgpt_sandbox.tools.execution_support import kill_after_timeout, new_job_id

_logger = logging.getLogger(__name__)

#: How long to wait, after a failed promotion has killed the command, for
#: the drain thread to notice — a bound, not the mechanism: the stream is
#: force-closed first, which is what actually unblocks the read.
_DRAIN_GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class PromotionDeps:
    """What promotion needs beyond what `run_exec` already has — the
    job-side collaborators `exec_start` uses. `None` in `run_exec` means
    "this server cannot promote", and the flag is then refused rather
    than silently ignored."""

    runner: AsyncJobRunner
    job_store: JobStore
    state_dir: Path


async def run_promotable_exec(
    *,
    workspace_id: str,
    repository_id: str,
    container_id: str,
    cmd: list[str],
    command_description: str,
    cwd: str,
    user: str,
    env: dict[str, str] | None,
    sync_timeout: float,
    service: ExecutionService,
    deps: PromotionDeps,
    execution_config: ExecutionConfig,
    audit_config: AuditConfig,
    audit_started: Callable[[], None],
) -> ExecOutput:
    started_at = datetime.now(UTC).isoformat()
    started_monotonic = time.monotonic()
    try:
        exec_id, stream, pid = await asyncio.to_thread(
            service.start_and_open_stream, container_id, cmd, cwd=cwd, user=user, env=env
        )
    except docker.errors.DockerException as exc:
        raise ContainerRuntimeFailedError(
            f"failed to start command in workspace {workspace_id!r}: {exc}",
            details={"workspace_id": workspace_id},
        ) from exc
    audit_started()

    sink = PromotableSink(
        max_output_bytes=execution_config.max_output_bytes,
        tail_keep_bytes=execution_config.tail_keep_bytes,
    )
    drain: asyncio.Future[int] = asyncio.ensure_future(
        asyncio.to_thread(
            service.drain_stream,
            exec_id,
            stream,
            on_stdout=sink.on_stdout,
            on_stderr=sink.on_stderr,
        )
    )
    await asyncio.wait({drain}, timeout=sync_timeout)

    # I4: decided by asking the future, *after* the wait, whether it has
    # finished — not by which of "done"/"timeout" the wait reported. The
    # obvious alternative, `asyncio.wait_for(drain, sync_timeout)` and
    # promoting in the `except TimeoutError`, has two faults here: it
    # cancels `drain` on timeout, which is the very future promotion needs
    # to keep; and a command that exits as the deadline fires can lose
    # that race and be reported as an overrun, creating a job record for
    # a process that is already gone. From here to `adopt_running` there
    # is no `await`, so the answer cannot change under us: a future's
    # completion is only ever delivered by the event loop.
    if drain.done():
        try:
            exit_code = drain.result()
        except docker.errors.DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed while running command in workspace {workspace_id!r}: {exc}",
                details={"workspace_id": workspace_id},
            ) from exc
        result = sink.result(exit_code, response_tail_lines=execution_config.response_tail_lines)
        return ExecOutput.completed(
            exit_code=result.exit_code,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            stdout_total_bytes=result.stdout_total_bytes,
            stderr_total_bytes=result.stderr_total_bytes,
            pressure=classify_container_command_failure(
                exit_code=result.exit_code, stderr=result.stderr_tail
            ),
        )

    job_id = new_job_id()
    job_dir = deps.state_dir / "jobs" / job_id
    stdout_log_path, stderr_log_path = job_dir / "stdout.log", job_dir / "stderr.log"
    try:
        deps.job_store.create(
            job_id=job_id,
            workspace_id=workspace_id,
            command=command_description,
            cwd=cwd,
            stdout_log_path=str(stdout_log_path),
            stderr_log_path=str(stderr_log_path),
        )
        sink.promote(stdout_log_path=stdout_log_path, stderr_log_path=stderr_log_path)
        deps.runner.adopt_running(
            job_id,
            exec_id=exec_id,
            stream=stream,
            drain=drain,
            stdout_capture=sink.stdout_capture,
            stderr_capture=sink.stderr_capture,
            pid=pid,
            started_at=started_at,
            started_monotonic=started_monotonic,
            timeout_seconds=execution_config.default_job_timeout_seconds,
        )
    except Exception:
        # I5, fail closed. Deliberately `Exception`, not a list of the
        # failures foreseen (a full disk under `state_dir`, a locked or
        # corrupt state database): whatever went wrong, the alternative
        # to handling it here is a command still running in the container
        # that no job record owns, no `exec_cancel` can reach and no
        # timeout will ever stop. The caller gets exactly what an overrun
        # gave before promotion existed — the process killed and
        # `COMMAND_TIMEOUT` — rather than a job handle for a job that
        # does not exist. Logged, since the client-facing error says
        # nothing about *why* promotion failed.
        _logger.exception("exec in workspace %s: promotion to a job failed", workspace_id)
        try:
            await kill_after_timeout(
                service, exec_id, workspace_id=workspace_id, timeout_seconds=sync_timeout
            )
        finally:
            with contextlib.suppress(Exception):
                stream.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(drain, timeout=_DRAIN_GRACE_SECONDS)
            _fail_half_created_job(deps.job_store, job_id, sink)
        raise CommandTimeoutError(
            f"command exceeded {sync_timeout}s and was terminated",
            details={"workspace_id": workspace_id, "timeout_seconds": sync_timeout},
        ) from None

    write_audit_event(
        audit_config,
        operation="exec_promoted_to_job",
        result="success",
        workspace_id=workspace_id,
        repository_id=repository_id,
        metadata={"job_id": job_id, "sync_timeout_seconds": sync_timeout},
    )
    # Inline, not `asyncio.to_thread`, for the reason `run_exec_start`
    # gives: `job_store`'s sqlite connection is thread-affine.
    prune_job_logs(
        deps.job_store,
        job_retention_days=execution_config.job_retention_days,
        job_log_max_total_bytes=execution_config.job_log_max_total_bytes,
    )
    promoted = deps.job_store.get(job_id)
    return ExecOutput.promoted(
        job_id=job_id, state=promoted.state.value, started_at=promoted.started_at
    )


def _fail_half_created_job(job_store: JobStore, job_id: str, sink: PromotableSink) -> None:
    """A record that got as far as existing SHALL NOT be left non-terminal
    (I5): `NON_TERMINAL_STATES` is what idle-stop reads as "this workspace
    is busy", so an orphan would pin the workspace up until the next
    daemon restart reconciled it to `lost` — HR-021's defect, by another
    route.

    PJPO-001 (`docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1, G1):
    the record is made terminal *first*, and on its own, whenever the job
    store can still be written — whatever the filesystem is doing. The
    first version finished both log captures before `mark_finished`,
    inside one shared `suppress`: when promotion had failed on a full
    disk, `finish()` raised that same ENOSPC again, the suppress swallowed
    it, and `mark_finished` never ran — the very orphan this function
    exists to prevent, left behind on exactly the path it guards. The
    byte counts and `truncated` it records are in-memory counters, so
    going first loses nothing. What stays best-effort: a job store that
    is itself what failed; restart reconciliation is the backstop there.
    Each capture is then finished in its own scope, so one broken log
    file cannot stop the other from being closed."""
    with contextlib.suppress(Exception):
        record = job_store.get(job_id)
        if record.state in NON_TERMINAL_STATES:
            job_store.mark_finished(
                job_id,
                state=JobState.FAILED,
                exit_code=None,
                stdout_total_bytes=sink.stdout_capture.total_bytes,
                stderr_total_bytes=sink.stderr_capture.total_bytes,
                output_truncated=sink.stdout_capture.truncated or sink.stderr_capture.truncated,
            )
    for capture in (sink.stdout_capture, sink.stderr_capture):
        with contextlib.suppress(Exception):
            capture.finish()
