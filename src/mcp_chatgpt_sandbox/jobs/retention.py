"""Job log retention (§15.6) plus durable post-delete cleanup debt.

Two independent triggers prune a completed job's on-disk log files
(never its `JobStore` row, and never a running job's logs — only
`TERMINAL_STATES` are ever candidates):

- age: a job finished more than `job_retention_days` ago;
- total size: the sum of tracked job logs plus any still-pending
  post-workspace-delete cleanup debt exceeds `job_log_max_total_bytes`,
  oldest-finished-first, until it doesn't or no further tracked job can
  be pruned.

A pruned job stays queryable — `exec_status` still reports its final
state, exit code, and byte counts — with `logs_pruned: true` and empty
content from `exec_output`, since its files are gone.

G3R-007 / G3AT-007 (`docs/GAP3_SPEC.md`): workspace deletion can remove
`job` rows before their host log directories are gone. A `job_log_cleanup`
tombstone is created in the same DB transaction as row deletion and remains
until the directory is proven absent, so failed cleanup stays durable,
retryable, and included in retention accounting.

G3R-014 / G3AT-014: the server-lifecycle scheduler runs the same cleanup and
retention paths without client traffic. `tools/execution.py` also invokes
`prune_job_logs` on `exec_start` as defense in depth. Production maintenance
derives the trusted state root from the main SQLite database path; in-memory
tests may pass it explicitly.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp_chatgpt_sandbox.jobs.store import TERMINAL_STATES, JobStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobLogCleanupDebt:
    """Durable linkage for one row-less job log directory (G3R-007)."""

    job_id: str
    workspace_id: str
    created_at: str
    last_attempt_at: str | None
    last_error: str | None
    bytes_hint: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cleanup_debt_from_row(row: sqlite3.Row) -> JobLogCleanupDebt:
    return JobLogCleanupDebt(
        job_id=row["job_id"],
        workspace_id=row["workspace_id"],
        created_at=row["created_at"],
        last_attempt_at=row["last_attempt_at"],
        last_error=_safe_persisted_cleanup_error(row["last_error"]),
        bytes_hint=row["bytes_hint"],
    )


def get_job_log_cleanup_debt(
    connection: sqlite3.Connection, job_id: str
) -> JobLogCleanupDebt | None:
    row = connection.execute(
        """
        SELECT job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint
        FROM job_log_cleanup
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    return None if row is None else _cleanup_debt_from_row(row)


def list_job_log_cleanup_debt(connection: sqlite3.Connection) -> list[JobLogCleanupDebt]:
    rows = connection.execute(
        """
        SELECT job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint
        FROM job_log_cleanup
        ORDER BY created_at, job_id
        """
    ).fetchall()
    return [_cleanup_debt_from_row(row) for row in rows]


def _job_log_dir(state_dir: Path, job_id: str) -> Path:
    """Construct the only host path cleanup is allowed to remove.

    Do not trust a persisted stdout/stderr path. The cleanup table stores
    identity, not a deletion target; the target is always reconstructed
    beneath the caller-supplied state root. A corrupted/malicious job id
    that is not exactly one path segment is refused and left as visible
    debt rather than normalized into a path outside `state_dir/jobs`.
    """
    if not job_id or job_id in {".", ".."} or Path(job_id).name != job_id or "/" in job_id:
        raise ValueError(f"unsafe job id for log cleanup: {job_id!r}")
    return state_dir / "jobs" / job_id


def _directory_file_bytes(path: Path) -> int:
    """Count file bytes below `path` without following symlinks."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    stack.append(Path(entry.path))
                else:
                    total += info.st_size
    return total


_SAFE_DURABLE_CLEANUP_ERROR_RE = re.compile(
    r"^(?:cleanup_failed|identity_unprovable): "
    r"[A-Za-z_][A-Za-z0-9_]*(?:: errno=[0-9]+)?$"
)


def _safe_error(exc: BaseException) -> str:
    """Return only exception class plus optional errno; never raw exception text."""
    if isinstance(exc, OSError) and exc.errno is not None:
        return f"{type(exc).__name__}: errno={exc.errno}"
    return type(exc).__name__


def _safe_persisted_cleanup_error(value: object) -> str | None:
    """Fail closed when reading legacy free-form durable cleanup diagnostics."""
    if value is None:
        return None
    if (
        isinstance(value, str)
        and len(value) <= 128
        and _SAFE_DURABLE_CLEANUP_ERROR_RE.fullmatch(value)
    ):
        return value
    return "cleanup_failure_recorded"


def _update_cleanup_failure(
    connection: sqlite3.Connection,
    debt: JobLogCleanupDebt,
    *,
    reason: str,
    error: BaseException,
    bytes_hint: int,
) -> None:
    """Persist only bounded classification/class/errno on the existing tombstone."""
    diagnostic = f"{reason}: {_safe_error(error)}"[:128]
    try:
        with connection:
            connection.execute(
                """
                UPDATE job_log_cleanup
                SET last_attempt_at = ?, last_error = ?, bytes_hint = ?
                WHERE job_id = ?
                """,
                (_now(), diagnostic, max(bytes_hint, 0), debt.job_id),
            )
    except sqlite3.Error as db_exc:
        # The tombstone was inserted before metadata deletion. If this
        # diagnostic UPDATE itself fails, do not turn an already-completed
        # workspace deletion into failure: the original tombstone remains
        # durable/retryable even though its latest reason could not be saved.
        _logger.error(
            "job-log cleanup: failed to update cleanup debt for %s after %s: %s",
            debt.job_id,
            diagnostic,
            _safe_error(db_exc),
        )


def _clear_cleanup_debt(connection: sqlite3.Connection, job_id: str) -> None:
    try:
        with connection:
            connection.execute("DELETE FROM job_log_cleanup WHERE job_id = ?", (job_id,))
    except sqlite3.Error as exc:
        # Files are already proven absent at this point. Leaving a stale
        # tombstone is conservative: a later maintenance pass sees the
        # directory missing and retries this DB clear. Never erase the only
        # durable linkage before filesystem cleanup has succeeded.
        _logger.error(
            "job-log cleanup: failed to clear cleanup debt for %s: %s",
            job_id,
            _safe_error(exc),
        )


def attempt_job_log_cleanup(
    connection: sqlite3.Connection,
    *,
    state_dir: Path,
    debt: JobLogCleanupDebt,
) -> bool:
    """Attempt one debt item; return True only when the directory is gone.

    Filesystem failures are best-effort with respect to the already-complete
    workspace deletion, but not forgotten: the tombstone survives and gets
    a timestamp/reason/byte hint. Invalid path identity is treated the same
    way while performing no filesystem mutation at all.
    """
    try:
        job_dir = _job_log_dir(state_dir, debt.job_id)
    except ValueError as exc:
        _update_cleanup_failure(
            connection,
            debt,
            reason="identity_unprovable",
            error=exc,
            bytes_hint=debt.bytes_hint,
        )
        _logger.error(
            "job-log cleanup: identity_unprovable for job %s: %s",
            debt.job_id,
            _safe_error(exc),
        )
        return False

    bytes_hint = debt.bytes_hint
    try:
        bytes_hint = _directory_file_bytes(job_dir)
    except FileNotFoundError:
        _clear_cleanup_debt(connection, debt.job_id)
        return True
    except OSError:
        # The existing hint is intentionally retained if a permission or
        # transient filesystem problem prevents fresh accounting.
        pass

    try:
        shutil.rmtree(job_dir)
    except FileNotFoundError:
        _clear_cleanup_debt(connection, debt.job_id)
        return True
    except OSError as exc:
        _update_cleanup_failure(
            connection,
            debt,
            reason="cleanup_failed",
            error=exc,
            bytes_hint=bytes_hint,
        )
        _logger.warning(
            "job-log cleanup: cleanup_failed for job %s: %s",
            debt.job_id,
            _safe_error(exc),
        )
        return False

    _clear_cleanup_debt(connection, debt.job_id)
    return True


def retry_job_log_cleanup_debt(
    connection: sqlite3.Connection,
    *,
    state_dir: Path,
) -> list[str]:
    """Retry every durable cleanup tombstone once, isolating failures."""
    cleaned: list[str] = []
    for debt in list_job_log_cleanup_debt(connection):
        if attempt_job_log_cleanup(connection, state_dir=state_dir, debt=debt):
            cleaned.append(debt.job_id)
    return cleaned


def _state_dir_from_connection(connection: sqlite3.Connection) -> Path | None:
    """Resolve the configured state root from SQLite's own main DB path.

    Production opens `<state_dir>/state.db` (or another configured filename)
    through `open_db`, so the database parent is the same trusted state root
    that owns `jobs/`. In-memory test databases have no filesystem path and
    therefore require an explicit `state_dir`; returning `None` there keeps
    path construction fail-closed rather than guessing a working directory.
    """
    rows = connection.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row[1] != "main":
            continue
        filename = row[2]
        if not filename:
            return None
        return Path(filename).resolve().parent
    return None


def pending_job_log_cleanup_bytes(
    connection: sqlite3.Connection,
    *,
    state_dir: Path | None,
) -> int:
    """Bytes still represented by cleanup debt for retention accounting.

    With a state root, accessible directories are measured from disk and a
    prior hint is used only when the directory cannot be inspected. Without
    a state root we cannot safely reconstruct a host path, so we use the
    persisted hints rather than pretending pending debt consumes zero bytes.
    """
    total = 0
    for debt in list_job_log_cleanup_debt(connection):
        if state_dir is None:
            total += debt.bytes_hint
            continue
        try:
            job_dir = _job_log_dir(state_dir, debt.job_id)
            total += _directory_file_bytes(job_dir)
        except FileNotFoundError:
            # A retry pass normally clears this first. Treat a concurrent
            # disappearance as zero rather than following any other path.
            continue
        except (OSError, ValueError):
            total += debt.bytes_hint
    return total


def prune_job_logs(
    job_store: JobStore,
    *,
    job_retention_days: int,
    job_log_max_total_bytes: int,
    state_dir: Path | None = None,
) -> list[str]:
    """Prune completed job logs and retry/account cleanup debt.

    Returns ids of still-queryable `job` rows whose normal retention logs
    were pruned. Cleanup-debt ids are intentionally not mixed into that
    return value because those jobs no longer have rows/API identity.
    """
    effective_state_dir = state_dir
    if effective_state_dir is None:
        try:
            effective_state_dir = _state_dir_from_connection(job_store.connection)
        except sqlite3.Error as exc:
            _logger.warning(
                "prune_job_logs: failed to resolve cleanup state directory: %s",
                _safe_error(exc),
            )

    cleanup_bytes = 0
    if effective_state_dir is not None:
        try:
            retry_job_log_cleanup_debt(job_store.connection, state_dir=effective_state_dir)
        except sqlite3.Error as exc:
            # Retention is opportunistic and must not fail the unrelated
            # exec_start that triggered it. A DB-read failure also means the
            # debt remains in the database; it is not converted to success.
            _logger.warning(
                "prune_job_logs: failed to read/retry cleanup debt: %s",
                _safe_error(exc),
            )
    try:
        cleanup_bytes = pending_job_log_cleanup_bytes(
            job_store.connection, state_dir=effective_state_dir
        )
    except sqlite3.Error as exc:
        _logger.warning(
            "prune_job_logs: failed to account cleanup debt: %s",
            _safe_error(exc),
        )

    candidates = [
        job
        for job in job_store.list_by_state(*TERMINAL_STATES)
        if not job.logs_pruned and job.finished_at is not None
    ]
    # Oldest-finished-first: age-based pruning naturally wants this
    # order, and so does the size cap once age alone isn't enough.
    candidates.sort(key=lambda job: job.finished_at or "")

    sizes = {
        job.id: _log_size(job.stdout_log_path) + _log_size(job.stderr_log_path)
        for job in candidates
    }
    total_bytes = cleanup_bytes + sum(sizes.values())
    cutoff = datetime.now(UTC) - timedelta(days=job_retention_days)

    pruned: list[str] = []
    for job in candidates:
        assert job.finished_at is not None  # filtered above
        aged_out = datetime.fromisoformat(job.finished_at) < cutoff
        over_budget = total_bytes > job_log_max_total_bytes
        if not (aged_out or over_budget):
            continue
        try:
            _delete_log(job.stdout_log_path)
            _delete_log(job.stderr_log_path)
            job_store.mark_logs_pruned(job.id)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see G2R-011 below
            # G2R-011 (`docs/GAP2_SPEC.md`): this call runs
            # opportunistically inline on every `exec_start`. A failure
            # pruning this still-queryable job is not a reason to abort the
            # unrelated request and the row remains eligible for the next
            # pass. G3R-007 extends the same non-aborting rule to row-less
            # cleanup debt, but gives that debt its own durable table so
            # "best effort" no longer means "warning and forget".
            _logger.warning(
                "prune_job_logs: failed to prune job %s: %s",
                job.id,
                _safe_error(exc),
            )
            continue
        total_bytes -= sizes[job.id]
        pruned.append(job.id)

    if total_bytes > job_log_max_total_bytes:
        _logger.warning(
            "prune_job_logs: tracked log usage remains above cap after maintenance: %d > %d bytes",
            total_bytes,
            job_log_max_total_bytes,
        )
    return pruned


def _log_size(path: str | None) -> int:
    if path is None:
        return 0
    try:
        return Path(path).stat().st_size
    except FileNotFoundError:
        return 0


def _delete_log(path: str | None) -> None:
    if path is None:
        return
    Path(path).unlink(missing_ok=True)
