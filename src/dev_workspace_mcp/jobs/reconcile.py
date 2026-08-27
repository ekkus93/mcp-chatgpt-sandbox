"""Job reconciliation on daemon startup (§15.4, §73).

`reconcile_jobs` runs once, early in `serve()`'s startup sequence, before
any tool serves a request — the same slot §73 reserves for Docker
reconciliation (S6) and SSH-agent reconciliation (S23).

A job left `queued` or `starting` never reached a real process: the
daemon instance that would have started one died first, so there is
nothing to reconcile it against — it is unconditionally `lost`.

A job left `running` did reach a real `docker exec`, and its `pid` is a
value in the *host* PID namespace (verified empirically in S12, not the
container's own namespace). That makes `os.kill(pid, 0)` a meaningful
liveness probe even after a restart. But liveness alone is not enough to
resume `running`: the daemon's reader for that exec's output stream died
with the old process, this store has no `exec_id` to re-attach to (only
`pid`, per §67's schema — re-attaching to a live exec instance is future
work, not scoped to S14), and a live host PID cannot be distinguished
from an unrelated process that has since reused the same number. So a
`running` job is `lost` too, on the conservative side of §15.4's "SHALL
transition to lost" — this module guarantees that floor; resuming a
still-running job "where feasible" (a SHOULD) is deferred to whichever
task gives the execution service a way to re-attach.
"""

from __future__ import annotations

import logging
import os

from dev_workspace_mcp.jobs.store import NON_TERMINAL_STATES, JobStore

_logger = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` sends no signal — it only probes existence and
    permission. Used for the reconciliation note below, not to decide
    whether to keep the job `running` (see module docstring)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by a different uid than the daemon's own. The
        # daemon's uid does not change across a restart, so this would be
        # surprising; treat it as alive rather than guessing.
        return True
    return True


def reconcile_jobs(job_store: JobStore) -> list[tuple[str, bool]]:
    """Marks every non-terminal job `lost` (§15.4). Returns `(job_id,
    pid_still_alive)` for every job it touched — the second element flags
    a job whose host process may still be running unattended, so a caller
    (`serve()`) can warn specifically about that rather than reporting an
    ordinary "lost" uniformly; the daemon itself has no way left to reach
    or reap that process (see module docstring)."""
    results: list[tuple[str, bool]] = []
    for job in job_store.list_by_state(*NON_TERMINAL_STATES):
        pid_alive = job.pid is not None and _pid_alive(job.pid)
        job_store.mark_lost(job.id)
        results.append((job.id, pid_alive))
        _logger.warning(
            "job %s: marked lost at startup (host process still alive: %s)", job.id, pid_alive
        )
    if results:
        _logger.info("job reconciliation: %d non-terminal job(s) marked lost", len(results))
    return results
