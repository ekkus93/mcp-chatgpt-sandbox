"""Persistence for the `job` table (§67).

This is bookkeeping only — starting a real process, streaming its output,
and killing it belong to the execution service (S15, S16). Because a job's
identity and metadata live here rather than in-memory on some request
handler, a job survives an MCP reconnect and outlives the request that
created it (§23): any later request can look it up by id regardless of
which MCP session, or even which daemon process instance (after a clean
restart plus `reconcile_jobs`, see `reconcile.py`), asks about it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from dev_workspace_mcp.errors import JobNotFoundError


class JobState(StrEnum):
    """§15.1's states, in transition order. `TERMINAL_STATES` below is
    every state a job never leaves once reached."""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    LOST = "lost"


TERMINAL_STATES: frozenset[JobState] = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELED,
        JobState.TIMED_OUT,
        JobState.LOST,
    }
)

#: States a job can be in before it has (or ever will have) a real
#: process backing it, per §15.4's reconciliation rule — see reconcile.py.
NON_TERMINAL_STATES: tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.STARTING,
    JobState.RUNNING,
)


@dataclass(frozen=True, slots=True)
class JobRecord:
    """§15.2/§67's fields, exactly. `stdout_log_path`/`stderr_log_path`
    point at §15.6's host-side log files; they are set at creation time
    (the path is deterministic from `id`) even though nothing is written
    there until the execution service (S16) exists."""

    id: str
    workspace_id: str
    command: str
    cwd: str
    state: JobState
    pid: int | None
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    stdout_log_path: str | None
    stderr_log_path: str | None
    stdout_total_bytes: int
    stderr_total_bytes: int
    output_truncated: bool
    logs_pruned: bool


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _record_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        command=row["command"],
        cwd=row["cwd"],
        state=JobState(row["state"]),
        pid=row["pid"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        exit_code=row["exit_code"],
        stdout_log_path=row["stdout_log_path"],
        stderr_log_path=row["stderr_log_path"],
        stdout_total_bytes=row["stdout_total_bytes"],
        stderr_total_bytes=row["stderr_total_bytes"],
        output_truncated=bool(row["output_truncated"]),
        logs_pruned=bool(row["logs_pruned"]),
    )


class JobStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    # --- creation and lookup -------------------------------------------

    def create(
        self,
        *,
        job_id: str,
        workspace_id: str,
        command: str,
        cwd: str,
        stdout_log_path: str,
        stderr_log_path: str,
    ) -> JobRecord:
        """Inserts a new job in `queued` state (§15.1). Nothing about
        starting the underlying process happens here."""
        with self._db:
            self._db.execute(
                """
                INSERT INTO job (
                    id, workspace_id, command, cwd, state,
                    stdout_log_path, stderr_log_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    workspace_id,
                    command,
                    cwd,
                    JobState.QUEUED.value,
                    stdout_log_path,
                    stderr_log_path,
                ),
            )
        return self._get_or_raise(job_id)

    def _load(self, job_id: str) -> JobRecord | None:
        row = self._db.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
        return _record_from_row(row) if row is not None else None

    def _get_or_raise(self, job_id: str) -> JobRecord:
        record = self._load(job_id)
        if record is None:
            raise JobNotFoundError(f"job not found: {job_id!r}")
        return record

    def get(self, job_id: str) -> JobRecord:
        """Side-effect-free lookup, for `exec_status` (S15). Raises
        `JobNotFoundError` — every caller needs a job to exist to do
        anything useful with the result, so a `None` return would just
        move the same check into every call site."""
        return self._get_or_raise(job_id)

    def list_by_workspace(self, workspace_id: str) -> list[JobRecord]:
        rows = self._db.execute(
            "SELECT * FROM job WHERE workspace_id = ? ORDER BY rowid",
            (workspace_id,),
        ).fetchall()
        return [_record_from_row(row) for row in rows]

    def list_by_state(self, *states: JobState) -> list[JobRecord]:
        placeholders = ",".join("?" for _ in states)
        rows = self._db.execute(
            f"SELECT * FROM job WHERE state IN ({placeholders}) ORDER BY rowid",  # noqa: S608
            tuple(state.value for state in states),
        ).fetchall()
        return [_record_from_row(row) for row in rows]

    # --- persistence -----------------------------------------------------

    def _save(self, record: JobRecord) -> JobRecord:
        with self._db:
            self._db.execute(
                """
                UPDATE job
                SET state = ?, pid = ?, started_at = ?, finished_at = ?,
                    exit_code = ?, stdout_total_bytes = ?, stderr_total_bytes = ?,
                    output_truncated = ?, logs_pruned = ?
                WHERE id = ?
                """,
                (
                    record.state.value,
                    record.pid,
                    record.started_at,
                    record.finished_at,
                    record.exit_code,
                    record.stdout_total_bytes,
                    record.stderr_total_bytes,
                    int(record.output_truncated),
                    int(record.logs_pruned),
                    record.id,
                ),
            )
        return record

    # --- lifecycle transitions (§15.1) ------------------------------------
    #
    # Each is a named method rather than an open `update(**fields)`, for the
    # same reason WorkspaceState's transitions are named methods on
    # DockerWorkspaceManager (S6): the set of legal transitions is small and
    # known, and spelling them out here is what makes an illegal one (e.g.
    # `queued` -> `succeeded` with no process ever having run) visible as a
    # call that simply does not exist.

    def mark_starting(self, job_id: str) -> JobRecord:
        record = self._get_or_raise(job_id)
        return self._save(replace(record, state=JobState.STARTING))

    def mark_running(self, job_id: str, *, pid: int) -> JobRecord:
        record = self._get_or_raise(job_id)
        return self._save(replace(record, state=JobState.RUNNING, pid=pid, started_at=_now()))

    def update_output_counts(
        self, job_id: str, *, stdout_total_bytes: int, stderr_total_bytes: int, output_truncated: bool
    ) -> JobRecord:
        """Called periodically while a job runs (S16), so `exec_status`
        reflects progress on a job nobody has finished yet."""
        record = self._get_or_raise(job_id)
        return self._save(
            replace(
                record,
                stdout_total_bytes=stdout_total_bytes,
                stderr_total_bytes=stderr_total_bytes,
                output_truncated=output_truncated,
            )
        )

    def mark_finished(
        self,
        job_id: str,
        *,
        state: JobState,
        exit_code: int | None,
        stdout_total_bytes: int,
        stderr_total_bytes: int,
        output_truncated: bool,
    ) -> JobRecord:
        """`succeeded` / `failed` / `canceled` / `timed_out` — every
        terminal state that resulted from a process the daemon actually
        watched to completion. `lost` is deliberately not accepted here;
        it only ever comes from `mark_lost`, below."""
        if state not in TERMINAL_STATES or state is JobState.LOST:
            raise ValueError(f"mark_finished: not a watched-to-completion terminal state: {state!r}")
        record = self._get_or_raise(job_id)
        return self._save(
            replace(
                record,
                state=state,
                finished_at=_now(),
                exit_code=exit_code,
                stdout_total_bytes=stdout_total_bytes,
                stderr_total_bytes=stderr_total_bytes,
                output_truncated=output_truncated,
            )
        )

    def mark_lost(self, job_id: str) -> JobRecord:
        """§15.4: the daemon cannot account for this job's outcome — most
        commonly because it was still `queued`/`starting`/`running` when
        the daemon process itself restarted (see `reconcile.py`). Leaves
        `exit_code` and the byte counters untouched, since whatever value
        they already hold is the last known-good state, not a guess."""
        record = self._get_or_raise(job_id)
        if record.state in TERMINAL_STATES:
            return record
        return self._save(replace(record, state=JobState.LOST, finished_at=_now()))
