"""Background execution of `exec_start` jobs (§15.3, S15/S16).

`JobStore` (S14) is what makes a job's *identity* survive an MCP
reconnect (§23): any request can look one up by id. It does not, by
itself, make a *running* job's output reachable — nothing in SQLite
holds the actual bytes a process has produced so far. `AsyncJobRunner`
is the piece that does: one instance per server process, holding exactly
the jobs this process has started since its own last restart in an
in-memory `LiveJob` per job id.

A `LiveJob` wraps a `StreamCapture` per stream (§15.6, S16) — capped,
host-side log files, not an unbounded in-memory buffer — so `AsyncJobRunner`
itself only needs cheap counters (`total_bytes`/`truncated`) for a
*running* job's live stats; actual content, for both a running and a
finished job, is read straight from the log files (`execution/log_capture.py`'s
`read_log_bytes`), which is what makes it survive this process's own
lifetime once written. A job this process never started (most commonly
one from before a restart, already `lost` by S14's `reconcile_jobs`) has
no `LiveJob` — its live stats are unavailable, but its log files, if not
yet pruned, are still readable directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker

from mcp_chatgpt_sandbox.errors import ContainerRuntimeFailedError
from mcp_chatgpt_sandbox.execution.log_capture import StreamCapture
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.jobs.store import JobState, JobStore

_logger = logging.getLogger(__name__)

#: Between TERM and KILL, whether canceling (`exec_cancel`) or timing out
#: (`exec_start`'s `timeout_seconds`) — §15.3's "graceful termination
#: first, then force after a grace period". Same value as the synchronous
#: `exec` tool's `KILL_GRACE_PERIOD_SECONDS` (tools/execution_support.py); kept as
#: a separate constant rather than imported from there so this module has
#: no dependency on the tools layer.
KILL_GRACE_PERIOD_SECONDS = 2.0

#: HR-020: once termination has been attempted (whether it succeeded or
#: not — `_terminate_and_track_failure` force-closes the stream on
#: failure specifically so this bound is rarely the thing that actually
#: ends the wait), how much longer `_run` waits for `drain_stream` to
#: return before giving up on it — bounding the wait so a stream that
#: never reaches EOF cannot hold its `asyncio.to_thread` executor slot
#: forever.
_DRAIN_GRACE_SECONDS = 10.0


@dataclass
class LiveJob:
    """This process's only source of a *running* job's live stats
    (`StreamCapture.total_bytes`/`.truncated`, cheap counters — not the
    content itself, which lives in the capture's log file). See the
    module docstring for what is and isn't scoped to S15/S16."""

    exec_id: str
    stdout_capture: StreamCapture
    stderr_capture: StreamCapture
    stream: object
    """The open `CancellableStream` `drain_stream` is blocking on, kept
    here (not just as a local in `_run`) so `cancel_job` — a separate
    call path — can also force-close it once termination has been
    attempted (RR-013 — regardless of whether it succeeded; see
    `_terminate_and_track_failure`)."""
    started_monotonic: float = field(default_factory=time.monotonic)
    finished_monotonic: float | None = None
    cancel_requested: bool = False
    termination_failed: bool = False
    """HR-020: set once `_terminate` itself fails to signal the process
    (most commonly `PermissionError`) — checked ahead of
    `cancel_requested`/timeout in `_run`'s own terminal-state decision,
    since a failed termination attempt is neither a successful cancel
    nor a successful timeout, regardless of which call path (the
    internal timeout branch, or a separate `cancel_job` call) attempted
    it."""
    termination_in_progress: bool = False
    """True while a cancellation/timeout termination attempt is unresolved.

    RR-013 regression: the stream can reach EOF after TERM but before the
    follow-up KILL/inspect step completes. Without this gate `_run` could
    observe `cancel_requested=True`, finalize the job as `canceled`, and
    only afterward have `exec_cancel` discover that termination actually
    failed. The terminal state must wait for the termination verdict.
    """
    termination_done: asyncio.Event = field(default_factory=asyncio.Event)

    def elapsed_seconds(self) -> float:
        end = self.finished_monotonic if self.finished_monotonic is not None else time.monotonic()
        return end - self.started_monotonic


class AsyncJobRunner:
    def __init__(self, execution_service: ExecutionService, job_store: JobStore) -> None:
        self._execution = execution_service
        self._job_store = job_store
        self._live: dict[str, LiveJob] = {}
        #: A task with no other strong reference is eligible for garbage
        #: collection at any await point, per asyncio's own docs — which
        #: would silently abandon a running job mid-stream. Held here
        #: until it finishes; `add_done_callback` drops it after.
        self._background_tasks: set[asyncio.Task[None]] = set()

    def live_job(self, job_id: str) -> LiveJob | None:
        return self._live.get(job_id)

    async def start_job(
        self,
        job_id: str,
        *,
        container_id: str,
        cmd: list[str],
        cwd: str,
        user: str,
        env: dict[str, str] | None,
        timeout_seconds: float | None,
        stdout_log_path: Path,
        stderr_log_path: Path,
        max_output_bytes: int,
        tail_keep_bytes: int,
    ) -> None:
        """Creates and starts the exec, records the job as `running` with
        a real pid, and launches the background task that drains its
        output. Returns once the process has actually started — so the
        caller's response can truthfully report `state: "running"` per
        §63's example — not once it finishes.

        HR-021 (`docs/HARDENING_SPEC.md` §9): if `start_and_open_stream`
        itself fails (the container disappeared, a Docker API error), the
        job used to stay in `starting` forever — a state
        `NON_TERMINAL_STATES` counts as active, pinning the workspace
        against idle-stop until the next daemon restart. Marked terminal
        (`FAILED`, no output ever produced) here before re-raising, so
        the caller still sees the real error while the job itself stops
        blocking idle-stop."""
        self._job_store.mark_starting(job_id)
        try:
            exec_id, stream, pid = await asyncio.to_thread(
                self._execution.start_and_open_stream,
                container_id,
                cmd,
                cwd=cwd,
                user=user,
                env=env,
            )
        except docker.errors.DockerException as exc:
            self._job_store.mark_finished(
                job_id,
                state=JobState.FAILED,
                exit_code=None,
                stdout_total_bytes=0,
                stderr_total_bytes=0,
                output_truncated=False,
            )
            raise ContainerRuntimeFailedError(
                f"failed to start exec for job {job_id!r}: {exc}",
                details={"job_id": job_id, "container_id": container_id},
            ) from exc

        live = LiveJob(
            exec_id=exec_id,
            stdout_capture=StreamCapture(
                stdout_log_path, max_output_bytes=max_output_bytes, tail_keep_bytes=tail_keep_bytes
            ),
            stderr_capture=StreamCapture(
                stderr_log_path, max_output_bytes=max_output_bytes, tail_keep_bytes=tail_keep_bytes
            ),
            stream=stream,
        )
        self._live[job_id] = live
        self._job_store.mark_running(job_id, pid=pid)
        task = asyncio.create_task(self._run(job_id, live, timeout_seconds))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def adopt_running(
        self,
        job_id: str,
        *,
        exec_id: str,
        stream: object,
        drain: asyncio.Future[int],
        stdout_capture: StreamCapture,
        stderr_capture: StreamCapture,
        pid: int | None,
        started_at: str,
        started_monotonic: float,
        timeout_seconds: float | None,
    ) -> None:
        """JPO-004 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.2): takes
        over an exec that is *already running and already being drained*
        — §14.4's promotion of a synchronous `exec` — and finishes it as
        an ordinary job (I6): registered in `_live`, so `exec_status`,
        `exec_cancel` and the timeout all work exactly as they do for a
        job `start_job` began.

        `drain` is the caller's own in-flight `drain_stream` future, and
        `_run` awaits *it* rather than starting a second reader. Why not
        simply open a fresh reader the way `start_job` does: a Docker
        exec's attach stream is single-consumer — `exec_start` cannot be
        called on the same exec twice — and the caller's reader is
        already mid-stream, so a second one would at best see nothing
        and at worst split the output between two readers.

        D4-A: `started_at`/`started_monotonic` are when the *process*
        started, not now, and `timeout_seconds` is the whole job's
        allowance measured from then — promotion does not hand a command
        a fresh clock. Synchronous and not `async`, unlike `start_job`:
        nothing here blocks, and the caller needs the job to be `running`
        and registered with no await point in between at which a
        concurrent `exec_cancel` could find it half-adopted."""
        live = LiveJob(
            exec_id=exec_id,
            stdout_capture=stdout_capture,
            stderr_capture=stderr_capture,
            stream=stream,
            started_monotonic=started_monotonic,
        )
        self._job_store.mark_starting(job_id)
        self._job_store.mark_running(job_id, pid=pid, started_at=started_at)
        self._live[job_id] = live
        remaining = (
            None
            if timeout_seconds is None
            else max(0.0, timeout_seconds - (time.monotonic() - started_monotonic))
        )
        task = asyncio.create_task(self._run(job_id, live, remaining, drain=drain))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run(
        self,
        job_id: str,
        live: LiveJob,
        timeout_seconds: float | None,
        *,
        drain: asyncio.Future[int] | None = None,
    ) -> None:
        if drain is None:
            drain = asyncio.ensure_future(
                asyncio.to_thread(
                    self._execution.drain_stream,
                    live.exec_id,
                    live.stream,  # type: ignore[arg-type]
                    on_stdout=live.stdout_capture.write,
                    on_stderr=live.stderr_capture.write,
                )
            )

        timed_out = False
        exit_code: int | None
        try:
            if timeout_seconds is not None:
                _done, pending = await asyncio.wait({drain}, timeout=timeout_seconds)
                if drain in pending:
                    timed_out = True
                    with contextlib.suppress(ContainerRuntimeFailedError):
                        await self._terminate_and_track_failure(live)
                    # HR-020/RR-013: bounded, not an unconditional `await
                    # drain` — `_terminate_and_track_failure` already
                    # force-closed the stream unconditionally (success or
                    # failure), so `drain_stream`'s blocked read SHALL
                    # have already returned; this wait is a bound against
                    # anything else unexpected, not the primary mechanism.
                    try:
                        exit_code = await asyncio.wait_for(drain, timeout=_DRAIN_GRACE_SECONDS)
                    except TimeoutError:
                        exit_code = None
                else:
                    exit_code = drain.result()
            else:
                exit_code = await drain
        except Exception:
            # H31 (`docs/HARDENING_TODO.md`, HR-031): the exact gap named
            # in the review — this used to swallow *any* unexpected
            # exception from the drain path (a `drain.result()`
            # re-raise, an unforeseen `asyncio.wait` failure) into a
            # bare `exit_code = None`, indistinguishable from an
            # ordinary failed command and, before this task, entirely
            # unlogged: a failure here was invisible. `logger.exception`
            # captures the traceback; nothing here is client-controlled
            # text needing HR-013 redaction (this is `job_id`, a
            # daemon-generated identifier, and Python's own exception
            # repr) — unlike the audit-event/policy-denial logging
            # below, which does route through it.
            _logger.exception("job %s: drain failed unexpectedly", job_id)
            exit_code = None

        # RR-013: an external `exec_cancel` can still be deciding whether
        # termination succeeded after the process stream has already
        # closed. Do not commit a terminal state until that decision is
        # complete; otherwise a late second-signal/inspect failure is
        # silently overwritten by `canceled`.
        if live.termination_in_progress:
            await live.termination_done.wait()

        live.finished_monotonic = time.monotonic()
        live.stdout_capture.finish()
        live.stderr_capture.finish()

        # HR-020: checked first — a failed termination is neither a
        # successful cancel nor a successful timeout, regardless of
        # which call path (below, or a separate `cancel_job` call)
        # attempted it.
        if live.termination_failed:
            state = JobState.FAILED
        elif live.cancel_requested:
            state = JobState.CANCELED
        elif timed_out:
            state = JobState.TIMED_OUT
        elif exit_code == 0:
            state = JobState.SUCCEEDED
        else:
            state = JobState.FAILED

        self._job_store.mark_finished(
            job_id,
            state=state,
            exit_code=exit_code,
            stdout_total_bytes=live.stdout_capture.total_bytes,
            stderr_total_bytes=live.stderr_capture.total_bytes,
            output_truncated=live.stdout_capture.truncated or live.stderr_capture.truncated,
        )
        # HR-020's own bullet: `_live` held every job ever started for the
        # rest of the process's lifetime — unbounded growth. Nothing past
        # this point needs the live entry; JobStore (just updated above)
        # is the durable record.
        self._live.pop(job_id, None)

    async def _terminate(self, exec_id: str) -> None:
        """TERM, wait a grace period, then KILL if still running (§15.3).
        Safe to call on an exec that has already exited: `kill` is a
        no-op when `exec_inspect` reports it isn't running.

        HR-020: `kill` can raise `PermissionError` (the daemon's own uid
        lacks permission to signal this pid/process group) — re-raised
        here as a structured `ContainerRuntimeFailedError` rather than
        left as a raw `OSError` a broad `except Exception` elsewhere
        could quietly absorb. RR-013 (`docs/REMEDIATION_SPEC.md` §10):
        `kill` itself no longer swallows an `exec_inspect` failure (a
        real `docker.errors.DockerException`, e.g. the container
        disappearing out from under a still-running exec) — caught here
        alongside `OSError` for the same structured-error treatment,
        rather than silently reported as a successful termination."""
        try:
            await asyncio.to_thread(self._execution.kill, exec_id)
            await asyncio.sleep(KILL_GRACE_PERIOD_SECONDS)
            await asyncio.to_thread(self._execution.kill, exec_id, grace_period_reached=True)
        except (OSError, docker.errors.DockerException) as exc:
            raise ContainerRuntimeFailedError(
                f"failed to signal process for exec {exec_id!r}: {exc}",
                details={"exec_id": exec_id},
                recoverable=False,
            ) from exc

    async def _terminate_and_track_failure(self, live: LiveJob) -> None:
        """Shared by `_run`'s own timeout handling and `cancel_job`: on a
        failed `_terminate`, marks `live.termination_failed` (read by
        `_run`'s terminal-state decision, from whichever call path
        actually reaches it). Re-raises the original error so a caller
        with a client-facing response (`exec_cancel`, notably) can report
        it as a structured failure.

        RR-013 (`docs/REMEDIATION_SPEC.md` §10): `live.stream`'s
        underlying socket is force-closed in a `finally`, unconditionally
        — not only when `_terminate` itself reports failure, which is
        what the code did before this task. A *successful* `_terminate`
        only confirms the signal was delivered, not that the exec's
        stream will actually reach EOF promptly afterward (a Docker
        daemon/network quirk can still leave it blocked); `asyncio.wait_for`
        elsewhere cancels the *awaiting* future, never the underlying
        `asyncio.to_thread` worker still blocked on that same read — so
        closing the stream here, regardless of outcome, is the only thing
        that reliably frees it rather than leaking the executor slot for
        the rest of the process's lifetime. `CancellableStream.close()`
        is explicitly documented as safe to call from another thread.

        The `termination_in_progress`/`termination_done` pair closes a
        race between this coroutine and `_run`: EOF after TERM is not
        proof the complete TERM→grace→KILL/inspect sequence succeeded.
        `_run` waits for this method's final verdict before persisting a
        terminal job state.
        """
        live.termination_in_progress = True
        live.termination_done.clear()
        try:
            await self._terminate(live.exec_id)
        except ContainerRuntimeFailedError:
            live.termination_failed = True
            raise
        finally:
            with contextlib.suppress(Exception):
                live.stream.close()  # type: ignore[attr-defined]
            live.termination_in_progress = False
            live.termination_done.set()

    async def cancel_job(self, job_id: str) -> None:
        """§15.3's `exec_cancel`. Idempotent no-op for a job this process
        is not actively running — already finished, or unknown to this
        process (e.g. a `lost` job from before a restart) — since there
        is nothing left here to terminate.

        HAT-017: if termination itself fails, this SHALL raise (the tool
        layer reports it as a structured error) and SHALL NOT mark the
        job `canceled` — `_terminate_and_track_failure` sets
        `live.termination_failed`, which `_run`'s own terminal-state
        decision checks ahead of `cancel_requested`, however long `_run`
        itself takes to actually observe the process ending."""
        live = self._live.get(job_id)
        if live is None or live.finished_monotonic is not None:
            return
        live.cancel_requested = True
        await self._terminate_and_track_failure(live)
