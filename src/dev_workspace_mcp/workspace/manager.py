"""Docker workspace lifecycle (§6.2, §8.2, §69).

Container image, user, and mount configuration are supplied by the caller
through `run_kwargs` — S7 (default image, container user) and S8 (mounts)
own those decisions. This module owns creation, naming, labeling, state
tracking, and reconciliation: the mechanism `workspace_open` (S11) sits on
top of, not a finished workspace by itself.

`sweep_idle_workspaces` (§8.1, S17) is the idle-stop mechanism: never
delete, only stop, a persistent workspace idle for `idle_stop_after`
with no running/queued job. Idleness is measured from `last_used_at` —
which every lifecycle transition already bumps, but so must every
*non*-lifecycle touch (an `exec` call, a filesystem write) for idleness
to mean what §8.1 says it means ("no MCP request touched the workspace"),
not just "no lifecycle event happened". `touch_last_used` is the free
function those call sites use; it needs no `DockerWorkspaceManager`
instance, since a filesystem tool (S9) doesn't otherwise have one.

`DockerWorkspaceManager` itself grew past 800 lines and was split three
ways (like `tools/git.py` and `tests/workspace/test_git_commit_tool.py`
before it):

- `records.py` — the `WorkspaceState`/`WorkspaceRecord` data model and the
  pure functions built on it (`_now`, `touch_last_used`,
  `_state_from_docker_container`, `_oom_killed_from_docker_container`);
- `manager_core.py` — `_CoreMixin`: persistence, container lookup, and
  reconciliation (§69);
- `manager_lifecycle.py` — `_LifecycleMixin(_CoreMixin)`: creation (§8.3)
  and lifecycle transitions (§8.4).

This module composes the two mixins back into the one public
`DockerWorkspaceManager` class below, and re-exports every name that used
to live here directly, so `from dev_workspace_mcp.workspace.manager import
DockerWorkspaceManager` (etc.) continues to resolve exactly as it did
before the split.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from dev_workspace_mcp.errors import WorkspaceNotFoundError
from dev_workspace_mcp.jobs.retention import (
    attempt_job_log_cleanup,
    get_job_log_cleanup_debt,
)
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.manager_lifecycle import _LifecycleMixin
from dev_workspace_mcp.workspace.records import (
    WorkspaceRecord,
    WorkspaceState,
    _state_from_docker_container,
    touch_last_used,
)

_logger = logging.getLogger(__name__)


__all__ = [
    "DockerWorkspaceManager",
    "WorkspaceRecord",
    "WorkspaceState",
    # re-exported: tests/workspace/test_recovery.py imports it directly
    "_state_from_docker_container",
    "get_authorized",
    "resolve_repository_root",
    "touch_last_used",
]


def resolve_repository_root(
    db: sqlite3.Connection, workspace_id: str, registry: RepositoryRegistry
) -> Path:
    """Repository root for `workspace_id`, via the `repository` row a
    workspace's creation upserts (S6) — what a workspace_id-scoped tool
    needs without necessarily depending on `DockerWorkspaceManager` or
    `workspace_open` (S11) having run in this process. Shared by S9's
    filesystem tools and S20+'s host-side Git reads.

    HR-016 (`docs/HARDENING_SPEC.md` §8): re-validates
    `workspace.repository_id` against `registry` — the live
    `RepositoryRegistry`, reflecting the server's *current* configuration
    — not persisted state alone. A repository removed from configuration
    since the workspace was opened SHALL fail authorization here exactly
    as if `workspace_id` had never existed, rather than remaining fully
    usable through its stale `repository` row. Deliberately still returns
    the *persisted* `canonical_path`, not `registry`'s own copy: that path
    is what the workspace's container was actually bind-mounted against
    at open time (§8.3), and a live config edit that only changes a
    repository's `path` without removing it is a different, narrower
    concern this check does not attempt to solve — this is authorization
    (is `repository_id` still allowed at all), not path reconciliation.

    Read-only: does not call `touch_last_used`. A host-side Git read
    (`git_status`/`git_diff`/`git_log`, §20.1) never touches the
    container at all, so it should not reset the idle-stop clock the
    container's own usage is measured by; a filesystem tool, which reads
    the same working tree the container has mounted, does — its caller
    calls `touch_last_used` alongside this."""
    row = db.execute(
        """
        SELECT workspace.repository_id AS repository_id,
               repository.canonical_path AS canonical_path
        FROM workspace
        JOIN repository ON repository.id = workspace.repository_id
        WHERE workspace.id = ?
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise WorkspaceNotFoundError(f"workspace not found: {workspace_id!r}")
    registry.get(row["repository_id"])  # raises RepositoryNotAllowedError if deauthorized
    return Path(row["canonical_path"])


def get_authorized(
    manager: DockerWorkspaceManager, workspace_id: str, registry: RepositoryRegistry
) -> WorkspaceRecord:
    """HR-016: `DockerWorkspaceManager.get` alone (like
    `resolve_repository_root` alone, above) trusts the persisted
    `workspace` row's `repository_id` with no live-configuration check —
    the same gap, for every workspace-scoped MCP tool that needs the
    manager's own `WorkspaceRecord` (state, container id, mode) rather
    than just a filesystem root. Tools call this instead of `manager.get`
    directly wherever the *result* is used to authorize further access
    (running `exec`, mutating Git, reporting workspace state back to the
    client) — not from the CLI's `reset`/`rebuild`/`delete` operator path
    (`cli.py`), which already requires `registry.get(repository_id)` to
    succeed before it ever constructs a manager at all, since those
    commands take a `repository_id` directly rather than a `workspace_id`."""
    record = manager.get(workspace_id)
    registry.get(record.repository_id)  # raises RepositoryNotAllowedError if deauthorized
    return record


class DockerWorkspaceManager(_LifecycleMixin):
    """The full workspace manager: `_LifecycleMixin` (creation, lifecycle
    transitions) composed onto `_CoreMixin` (persistence, container
    lookup, reconciliation) it inherits from in turn — see this module's
    own docstring above for why the split landed on these three pieces.
    Every method a caller could previously reach on `DockerWorkspaceManager`
    is still reachable on it exactly the same way; only where each body
    physically lives changed."""

    def _remove_job_log_dirs(self, state_dir: Path, job_ids: list[str]) -> None:
        """G3R-007: consume the durable tombstones created before job deletion.

        `_LifecycleMixin.delete` calls this only after workspace/job rows are
        committed gone. The schema's BEFORE DELETE trigger has already created
        one `job_log_cleanup` row per job, so immediate cleanup must use the
        same debt-aware function as later maintenance: failure records the
        attempt and preserves debt; success clears debt only after the
        directory is proven absent. Workspace deletion itself remains complete.
        """
        for job_id in job_ids:
            debt = get_job_log_cleanup_debt(self._db, job_id)
            if debt is None:
                # Do not fall back to an untracked rmtree. Missing debt here is
                # an invariant failure that would recreate Y7's lost-cleanup gap.
                _logger.error(
                    "workspace delete: missing durable job-log cleanup debt for %s", job_id
                )
                continue
            attempt_job_log_cleanup(self._db, state_dir=state_dir, debt=debt)
