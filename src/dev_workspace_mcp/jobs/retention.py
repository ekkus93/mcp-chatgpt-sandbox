"""Job log retention (§15.6) — S16.

Two independent triggers prune a completed job's on-disk log files
(never its `JobStore` row, and never a running job's logs — only
`TERMINAL_STATES` are ever candidates):

- age: a job finished more than `job_retention_days` ago;
- total size: the sum of every job's log files on disk exceeds
  `job_log_max_total_bytes`, oldest-finished-first, until it doesn't.

A pruned job stays queryable — `exec_status` still reports its final
state, exit code, and byte counts — with `logs_pruned: true` and empty
content from `exec_output`, since its files are gone.

"Retention... on a schedule and on demand" (§15.6): this module is the
on-demand half — `prune_job_logs` is callable any time. The scheduled
half needs a periodic timer, which nothing else in the daemon has either
(idle-stop, S17, is likely to want the same thing); `tools/execution.py`
calls this opportunistically on every `exec_start`, a reasonable stand-in
for a schedule in a server that otherwise runs long stretches between
requests, but not a substitute for a real one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dev_workspace_mcp.jobs.store import TERMINAL_STATES, JobStore

_logger = logging.getLogger(__name__)


def prune_job_logs(
    job_store: JobStore,
    *,
    job_retention_days: int,
    job_log_max_total_bytes: int,
) -> list[str]:
    """Prunes completed jobs' log files per the two triggers above.
    Returns the ids of jobs it pruned."""
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
    total_bytes = sum(sizes.values())
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
            # opportunistically inline on every `exec_start`
            # (module docstring above) — a failure pruning *this* job
            # (a locked log file, a transient DB error) is not a reason
            # to abort the rest of the batch, and must never surface as
            # a failure of the unrelated request that happened to
            # trigger it. The same RR-012 principle (`docs/
            # REMEDIATION_SPEC.md` §10) `workspace/manager_lifecycle.py`
            # applies to `delete()`'s own best-effort cleanup steps.
            # Logged, not raised; a job left unpruned here is simply
            # picked up again by the next opportunistic call.
            _logger.warning("prune_job_logs: failed to prune job %s: %s", job.id, exc)
            continue
        total_bytes -= sizes[job.id]
        pruned.append(job.id)

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
