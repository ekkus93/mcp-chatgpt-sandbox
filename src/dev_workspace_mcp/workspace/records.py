"""The workspace state/record data model (§8.2) and the pure
Docker-container-status-interpretation helpers built on it.

Extracted from `workspace/manager.py` — that module's `DockerWorkspaceManager`
grew past 800 lines and needed splitting (like `tools/git.py` and
`tests/workspace/test_git_commit_tool.py` before it). This module holds the
one piece of `manager.py` with no dependency on `DockerWorkspaceManager`
itself: `WorkspaceState`/`WorkspaceRecord` are plain data, and `_now`/
`touch_last_used`/`_state_from_docker_container`/`_oom_killed_from_docker_container`
are pure functions of a timestamp or a `Container`, not of manager state.
Both `manager_core.py` (persistence, container lookup, reconciliation) and
`manager_lifecycle.py` (creation, lifecycle transitions) import from here —
this module imports from neither, so the dependency graph stays acyclic.

`manager.py` re-imports and re-exports everything below unchanged, so
`from dev_workspace_mcp.workspace.manager import WorkspaceState` (etc.)
continues to resolve exactly as it did before the split.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from dev_workspace_mcp.workspace.naming import WorkspaceMode
from docker.models.containers import Container


class WorkspaceState(StrEnum):
    """§8.2's lifecycle states. Transitions are explicit: this field is
    only ever set by a named manager method, never ad hoc."""

    ABSENT = "absent"
    CREATING = "creating"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    REBUILDING = "rebuilding"
    FAILED = "failed"
    DELETING = "deleting"


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: str
    repository_id: str
    mode: WorkspaceMode
    container_id: str | None
    container_name: str | None
    image: str | None
    workspace_path: str
    state: WorkspaceState
    created_at: str
    last_used_at: str
    #: H32 (`docs/HARDENING_SPEC.md` §9/§26 row): OOM detection already
    #: existed (folded into `state == FAILED` by `_state_from_docker_container`
    #: below), but had no distinct reporting surface — a `FAILED` workspace
    #: looked identical whether it OOM'd or crashed for any other reason.
    #: Reflects the *current* container's own Docker-reported `OOMKilled`
    #: flag, recomputed on every reconcile — `False` again once the same
    #: container (or a replacement) is observed running or stopped cleanly,
    #: not a sticky "this workspace once OOM'd" history flag.
    oom_killed: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def touch_last_used(db: sqlite3.Connection, workspace_id: str) -> None:
    """Records that `workspace_id` was just used, for §8.1's idle-stop
    accounting. A plain `UPDATE`, not a `DockerWorkspaceManager` method —
    callers that only have a workspace_id and a `db` connection (S9's
    filesystem tools, which never construct a manager) can call this
    without one. Silently does nothing if the workspace doesn't exist —
    callers that need existence checked have already done so."""
    with db:
        db.execute("UPDATE workspace SET last_used_at = ? WHERE id = ?", (_now(), workspace_id))


def _state_from_docker_container(container: Container) -> WorkspaceState:
    """§44.2: `dead`, OOM-killed, or a Docker-level runtime error are
    unambiguous crash signals -> `failed`. A plain nonzero exit code is
    deliberately NOT used as a crash signal on its own: an intentional
    `stop()` sends SIGTERM, and a process with no handler for it (e.g.
    `sleep`, and plenty of real long-running processes) legitimately
    exits 128+15=143 — a container that was deliberately stopped, not
    one that crashed. Either way the repository is unaffected — it
    lives in a bind mount, not the container's own writable layer
    (§5.1), so this only changes what state is reported, never what
    happens to the checkout."""
    if container.status == "running":
        return WorkspaceState.RUNNING
    state = container.attrs.get("State", {})
    if container.status == "dead" or state.get("OOMKilled") or state.get("Error"):
        return WorkspaceState.FAILED
    return WorkspaceState.STOPPED


def _oom_killed_from_docker_container(container: Container) -> bool:
    """The reporting half of the same Docker-reported flag
    `_state_from_docker_container` folds into `FAILED` above — H32
    (`docs/HARDENING_SPEC.md` §9/§26 row). Read alongside that function
    at every call site, from the same `container.attrs` snapshot, so
    the two never disagree about which container's state they describe."""
    return bool(container.attrs.get("State", {}).get("OOMKilled"))
