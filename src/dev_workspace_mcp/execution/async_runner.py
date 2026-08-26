"""Background execution of `exec_start` jobs (§15.3, S15).

`JobStore` (S14) is what makes a job's *identity* survive an MCP
reconnect (§23): any request can look one up by id. It does not, by
itself, make a *running* job's output reachable — nothing in SQLite
holds the actual bytes a process has produced so far. `AsyncJobRunner`
is the piece that does: one instance per server process, holding exactly
the jobs this process has started since its own last restart in an
in-memory `LiveJob` per job id.

That buffer is deliberately unbounded and unpersisted — a stand-in for
§15.6's capped, host-side log files (S16), not a finished implementation
of them. `output_truncated` therefore stays `False` throughout S15; a job
this process never started (most commonly one from before a restart,
already `lost` by S14's `reconcile_jobs`) has no `LiveJob` and its output
is simply gone, consistent with what "lost" means.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.jobs.store import JobState, JobStore

#: Between TERM and KILL, whether canceling (`exec_cancel`) or timing out
#: (`exec_start`'s `timeout_seconds`) — §15.3's "graceful termination
#: first, then force after a grace period". Same value as the synchronous
#: `exec` tool's `KILL_GRACE_PERIOD_SECONDS` (tools/execution.py); kept as
#: a separate constant rather than imported from there so this module has
#: no dependency on the tools layer.
KILL_GRACE_PERIOD_SECONDS = 2.0


@dataclass
class LiveJob:
    """This process's only source of a job's actual output content, and
    of a *running* job's live progress. See the module docstring for what
    is and isn't scoped to S15."""

    exec_id: str
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    started_monotonic: float = field(default_factory=time.monotonic)
    finished_monotonic: float | None = None
    cancel_requested: bool = False

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
    ) -> None:
        """Creates and starts the exec, records the job as `running` with
        a real pid, and launches the background task that drains its
        output. Returns once the process has actually started — so the
        caller's response can truthfully report `state: "running"` per
        §63's example — not once it finishes."""
        self._job_store.mark_starting(job_id)
        exec_id, stream, pid = await asyncio.to_thread(
            self._execution.start_and_open_stream,
            container_id,
            cmd,
            cwd=cwd,
            user=user,
            env=env,
        )
        live = LiveJob(exec_id=exec_id)
        self._live[job_id] = live
        self._job_store.mark_running(job_id, pid=pid)
        task = asyncio.create_task(self._run(job_id, live, stream, timeout_seconds))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run(
        self,
        job_id: str,
        live: LiveJob,
        stream: object,
        timeout_seconds: float | None,
    ) -> None:
        def on_stdout(chunk: bytes) -> None:
            live.stdout.extend(chunk)

        def on_stderr(chunk: bytes) -> None:
            live.stderr.extend(chunk)

        drain = asyncio.ensure_future(
            asyncio.to_thread(
                self._execution.drain_stream,
                live.exec_id,
                stream,  # type: ignore[arg-type]
                on_stdout=on_stdout,
                on_stderr=on_stderr,
            )
        )

        timed_out = False
        exit_code: int | None
        try:
            if timeout_seconds is not None:
                _done, pending = await asyncio.wait({drain}, timeout=timeout_seconds)
                if drain in pending:
                    timed_out = True
                    await self._terminate(live.exec_id)
                    exit_code = await drain  # killing it lets drain_stream's loop end
                else:
                    exit_code = drain.result()
            else:
                exit_code = await drain
        except Exception:
            exit_code = None

        live.finished_monotonic = time.monotonic()

        if live.cancel_requested:
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
            stdout_total_bytes=len(live.stdout),
            stderr_total_bytes=len(live.stderr),
            output_truncated=False,
        )

    async def _terminate(self, exec_id: str) -> None:
        """TERM, wait a grace period, then KILL if still running (§15.3).
        Safe to call on an exec that has already exited: `kill` is a
        no-op when `exec_inspect` reports it isn't running."""
        await asyncio.to_thread(self._execution.kill, exec_id)
        await asyncio.sleep(KILL_GRACE_PERIOD_SECONDS)
        await asyncio.to_thread(self._execution.kill, exec_id, grace_period_reached=True)

    async def cancel_job(self, job_id: str) -> None:
        """§15.3's `exec_cancel`. Idempotent no-op for a job this process
        is not actively running — already finished, or unknown to this
        process (e.g. a `lost` job from before a restart) — since there
        is nothing left here to terminate."""
        live = self._live.get(job_id)
        if live is None or live.finished_monotonic is not None:
            return
        live.cancel_requested = True
        await self._terminate(live.exec_id)
