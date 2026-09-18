"""`_CoreMixin` — the persistence (§3's workspace table), container-lookup,
and startup-reconciliation-sweep (§44.1) third of `DockerWorkspaceManager`.
Per-workspace reconciliation (§69) is `DockerWorkspaceManager.reconcile()` in
`manager.py`; this mixin only declares it (PSCR2-011).

Extracted from `workspace/manager.py` — see `records.py`'s module docstring
for the full split rationale. This piece owns `__init__` and every method
with no dependency on creation or lifecycle-transition logic: `manager.py`'s
`DockerWorkspaceManager` composes this with `manager_lifecycle.py`'s
`_LifecycleMixin` (which itself inherits from `_CoreMixin`, since creation
and lifecycle transitions both need the persistence/lookup/reconcile layer
below, and nothing here needs anything from them) to get back the exact
same single class and public interface as before the split.
"""

from __future__ import annotations

import logging
import sqlite3

import docker
from docker.errors import DockerException
from docker.errors import NotFound as DockerNotFound
from docker.models.containers import Container

from mcp_chatgpt_sandbox.errors import (
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from mcp_chatgpt_sandbox.workspace.naming import (
    container_matches_test_namespace,
)
from mcp_chatgpt_sandbox.workspace.records import (
    WorkspaceRecord,
    WorkspaceState,
)
from mcp_chatgpt_sandbox.workspace.ssh_agent import SSHAgentManager

_logger = logging.getLogger(__name__)


class _CoreMixin:
    def __init__(
        self,
        docker_client: docker.DockerClient,
        db: sqlite3.Connection,
        *,
        ssh_agent_manager: SSHAgentManager | None = None,
    ) -> None:
        self._docker = docker_client
        self._db = db
        # Optional: only `tools/workspace.py` (where `ssh.mode: agent`
        # lifecycle actually matters) constructs this with one — every
        # other caller (execution/git tools, `reconcile_all` at startup)
        # has no reason to touch ssh-agents at all, so `stop`/`delete`
        # below no-op the agent-cleanup step when this is `None`.
        self._ssh_agents = ssh_agent_manager

    # --- persistence (§3's workspace table) ---------------------------------

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row["id"],
            repository_id=row["repository_id"],
            mode=row["mode"],
            container_id=row["container_id"],
            container_name=row["container_name"],
            image=row["image"],
            workspace_path=row["workspace_path"],
            state=WorkspaceState(row["state"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            oom_killed=bool(row["oom_killed"]),
        )

    def _load(self, workspace_id: str) -> WorkspaceRecord | None:
        row = self._db.execute("SELECT * FROM workspace WHERE id = ?", (workspace_id,)).fetchone()
        return self._record_from_row(row) if row is not None else None

    def _get_or_raise(self, workspace_id: str) -> WorkspaceRecord:
        record = self._load(workspace_id)
        if record is None:
            raise WorkspaceNotFoundError(f"workspace not found: {workspace_id!r}")
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        """Side-effect-free lookup — for `workspace_info` (§25), which
        reports current state and SHALL NOT itself start or reconcile
        anything. Use `ensure_workspace`/`reconcile` for those."""
        return self._get_or_raise(workspace_id)

    def _insert(self, record: WorkspaceRecord) -> None:
        self._db.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, container_id, container_name, image,
                 workspace_path, state, created_at, last_used_at, oom_killed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.repository_id,
                record.mode,
                record.container_id,
                record.container_name,
                record.image,
                record.workspace_path,
                record.state.value,
                record.created_at,
                record.last_used_at,
                int(record.oom_killed),
            ),
        )
        self._db.commit()

    def _save(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._db.execute(
            """
            UPDATE workspace
            SET container_id = ?, container_name = ?, image = ?, state = ?,
                last_used_at = ?, oom_killed = ?
            WHERE id = ?
            """,
            (
                record.container_id,
                record.container_name,
                record.image,
                record.state.value,
                record.last_used_at,
                int(record.oom_killed),
                record.id,
            ),
        )
        self._db.commit()
        return record

    # --- container lookup -----------------------------------------------------

    def _find_container(self, record: WorkspaceRecord) -> Container | None:
        if record.container_id is None:
            return None
        try:
            container = self._docker.containers.get(record.container_id)
            if not container_matches_test_namespace(container.labels):
                raise ContainerRuntimeFailedError(
                    f"container {record.container_id!r} for workspace {record.id!r} "
                    "belongs to a different test resource namespace",
                    details={"workspace_id": record.id},
                    recoverable=False,
                )
            return container
        except DockerNotFound:
            return None
        except DockerException as exc:
            # HR-022: a transient Docker API error (unlike "not found")
            # isn't safe to treat as "no container" — this is the
            # chokepoint `stop`/`start`/`restart`/`delete`/`reconcile`
            # all resolve a workspace's container through.
            raise ContainerRuntimeFailedError(
                f"failed to look up container {record.container_id!r} "
                f"for workspace {record.id!r}: {exc}",
                details={"workspace_id": record.id},
            ) from exc

    def _require_container(self, record: WorkspaceRecord) -> Container:
        container = self._find_container(record)
        if container is None:
            raise ContainerRuntimeFailedError(
                f"workspace {record.id!r} has no container to operate on",
                details={"workspace_id": record.id},
            )
        return container

    # --- reconciliation (§69) -----------------------------------------------

    def reconcile(self, workspace_id: str) -> WorkspaceRecord:
        """Reconcile one workspace against Docker state (§69).

        Implemented by `DockerWorkspaceManager.reconcile()` in
        `workspace/manager.py`, which adopts a container only on PMOR-001's
        positive ownership proof. It is declared here only so that this
        mixin's `reconcile_all()` and `_LifecycleMixin`'s callers type-check;
        at runtime `self.reconcile` always resolves to that override.

        PSCR2-011 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`) replaced a
        full, 77-line implementation that used to live here. The override
        shadowed it and nothing called `super().reconcile()`, so it never ran —
        yet it carried a complete §69 docstring and its own namespace filter,
        and it read as the live path. It misled the PSCR2 review into citing it
        as the production protection, and a discrimination run against it
        "passed" because the code under test was never executed.
        """

        raise NotImplementedError(
            "reconcile() is implemented by DockerWorkspaceManager in workspace/manager.py"
        )

    def reconcile_all(self) -> list[WorkspaceRecord]:
        """§44.1's daemon-restart sequence, steps 1-3: eagerly reconcile
        every persisted workspace against real Docker state at startup,
        rather than waiting for each one's next operation. `reconcile`
        itself (S6) remains in place as a safety net for whatever
        changes between this sweep and actual use — this does not
        replace it, it runs it for everything up front. A workspace
        that fails to reconcile (§69's duplicate-container case) is
        skipped rather than aborting the whole sweep; §69 already says
        that specific failure needs an operator, not this sweep guessing.
        """
        ids = [row["id"] for row in self._db.execute("SELECT id FROM workspace").fetchall()]
        reconciled: list[WorkspaceRecord] = []
        skipped = 0
        for workspace_id in ids:
            try:
                reconciled.append(self.reconcile(workspace_id))
            except ContainerRuntimeFailedError as exc:
                # H31 (`docs/HARDENING_TODO.md`, HR-031): a real
                # "swallowed exception" — correctly not fatal to the
                # whole startup sweep (this docstring's own §69
                # rationale), but previously invisible: an operator
                # would only discover a workspace needing their
                # attention by noticing it never came back, not from
                # anything the daemon told them.
                skipped += 1
                _logger.warning("workspace %s: reconciliation failed: %s", workspace_id, exc)
                continue
        _logger.info(
            "startup reconciliation: %d workspace(s) reconciled, %d skipped",
            len(reconciled),
            skipped,
        )
        return reconciled
