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
"""

from __future__ import annotations

import contextlib
import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from docker.errors import DockerException
from docker.errors import NotFound as DockerNotFound
from docker.models.containers import Container

import docker
from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import AuditConfig
from dev_workspace_mcp.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from dev_workspace_mcp.jobs.store import NON_TERMINAL_STATES, JobStore
from dev_workspace_mcp.repositories import RepositoryRecord, RepositoryRegistry
from dev_workspace_mcp.workspace.naming import (
    LABEL_WORKSPACE_ID,
    WorkspaceMode,
    container_labels,
    container_name,
    ephemeral_workspace_id,
    persistent_workspace_id,
)
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


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


class DockerWorkspaceManager:
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
                 workspace_path, state, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        self._db.commit()

    def _save(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._db.execute(
            """
            UPDATE workspace
            SET container_id = ?, container_name = ?, image = ?, state = ?, last_used_at = ?
            WHERE id = ?
            """,
            (
                record.container_id,
                record.container_name,
                record.image,
                record.state.value,
                record.last_used_at,
                record.id,
            ),
        )
        self._db.commit()
        return record

    # --- open / ensure -------------------------------------------------------

    def ensure_workspace(
        self,
        *,
        repository: RepositoryRecord,
        image: str,
        mode: WorkspaceMode = "persistent",
        workspace_path: str = "/workspace",
        run_kwargs: dict[str, Any] | None = None,
    ) -> WorkspaceRecord:
        """Create a new workspace, or reconcile and (re)start — or, if no
        container exists at all to start, recreate — an existing one.
        The mechanism behind `workspace_open` (§8.3).

        `mode: "ephemeral"` always creates a fresh workspace (FR-005) —
        there is no "the" ephemeral workspace for a repository to reuse.

        HR-017 (`docs/HARDENING_SPEC.md` §9): `reconcile()` can leave an
        existing row with no container at all to start — `ABSENT` (its
        container was removed out of band, e.g. `docker system prune`)
        or still `FAILED` from a create attempt that never got as far as
        a container (a transient Docker error, or a name collision with
        an unlabeled container — see `_run_container`'s own adopt-or-remove
        step for that case). `start()` cannot handle either: it only ever
        starts an *existing* container. Recreating the container for the
        already-existing row (the `reconciled.container_id is None`
        branch below) is what recovers both, rather than wedging the
        workspace permanently."""
        if mode == "ephemeral":
            workspace_id = ephemeral_workspace_id(repository.id, suffix=secrets.token_hex(4))
            return self._create(
                workspace_id=workspace_id,
                repository=repository,
                mode=mode,
                image=image,
                workspace_path=workspace_path,
                run_kwargs=run_kwargs or {},
            )

        workspace_id = persistent_workspace_id(repository.id)
        existing = self._load(workspace_id)
        if existing is None:
            return self._create(
                workspace_id=workspace_id,
                repository=repository,
                mode=mode,
                image=image,
                workspace_path=workspace_path,
                run_kwargs=run_kwargs or {},
            )

        reconciled = self.reconcile(workspace_id)
        if reconciled.state == WorkspaceState.RUNNING:
            return reconciled
        if reconciled.container_id is not None:
            return self.start(workspace_id)
        # HR-017: no container at all to start — recreate one for the
        # existing row, the same way `_create` does for a brand-new one,
        # just without inserting a second row.
        return self._run_container(reconciled, image=image, run_kwargs=run_kwargs or {})

    def _upsert_repository(self, repository: RepositoryRecord) -> None:
        """`workspace.repository_id` is a foreign key into `repository`
        (§67), but nothing else in the system persists a repository row —
        `RepositoryRegistry` (S5) is in-memory only, rebuilt from config on
        every start. A workspace is the first thing that needs the row to
        exist, so it is the thing that ensures it does."""
        self._db.execute(
            """
            INSERT INTO repository (id, canonical_path, configured_remote)
            VALUES (?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                canonical_path = excluded.canonical_path,
                configured_remote = excluded.configured_remote
            """,
            (repository.id, str(repository.canonical_path), repository.github),
        )
        self._db.commit()

    def _create(
        self,
        *,
        workspace_id: str,
        repository: RepositoryRecord,
        mode: WorkspaceMode,
        image: str,
        workspace_path: str,
        run_kwargs: dict[str, Any],
    ) -> WorkspaceRecord:
        """A brand-new workspace: ensures the repository row exists,
        inserts the workspace row, then runs the container. `_recreate`
        (rebuild/reset) reuses `_run_container` instead, since both rows
        already exist by then."""
        self._upsert_repository(repository)
        now = _now()
        record = WorkspaceRecord(
            id=workspace_id,
            repository_id=repository.id,
            mode=mode,
            container_id=None,
            container_name=container_name(workspace_id),
            image=image,
            workspace_path=workspace_path,
            state=WorkspaceState.CREATING,
            created_at=now,
            last_used_at=now,
        )
        self._insert(record)
        return self._run_container(record, image=image, run_kwargs=run_kwargs)

    def _find_container_by_name(self, name: str) -> Container | None:
        try:
            return self._docker.containers.get(name)
        except DockerNotFound:
            return None

    def _adopt_or_remove_by_name(self, record: WorkspaceRecord) -> WorkspaceRecord | None:
        """HR-017 (`docs/HARDENING_SPEC.md` §9): before creating a
        container at `record`'s deterministic name (§51), check whether
        one already exists there. `containers.run`'s own 409 on a name
        collision is otherwise a dead end no retry can get past, since
        the name is deterministic and will collide again every time —
        this is what used to permanently wedge a workspace behind an
        orphaned container.

        A container already labeled for this exact `workspace_id` is
        adopted (its own earlier `containers.run` plausibly completed,
        but this process crashed, or the daemon restarted, before the
        result was persisted) — nothing left to create. Anything else
        occupying the name can only be a stale container from a
        *previous* incarnation of this same logical workspace, since the
        name is deterministic per `workspace_id` — removed to free the
        name for the create below. Returns the adopted record, or `None`
        if it's safe to proceed to `containers.run`."""
        assert record.container_name is not None
        existing = self._find_container_by_name(record.container_name)
        if existing is None:
            return None
        if existing.labels.get(LABEL_WORKSPACE_ID) == record.id:
            return self._save(
                replace(
                    record,
                    container_id=existing.id,
                    state=_state_from_docker_container(existing),
                    last_used_at=_now(),
                )
            )
        with contextlib.suppress(DockerException):
            existing.remove(force=True)
        return None

    def _run_container(
        self, record: WorkspaceRecord, *, image: str, run_kwargs: dict[str, Any]
    ) -> WorkspaceRecord:
        """Run a new container for `record` (whose row already exists) and
        persist the result. Shared by `_create` and `_recreate`."""
        assert record.container_name is not None
        adopted = self._adopt_or_remove_by_name(record)
        if adopted is not None:
            return adopted
        try:
            container = self._docker.containers.run(
                image,
                name=record.container_name,
                detach=True,
                labels=container_labels(workspace_id=record.id, repository_id=record.repository_id),
                **run_kwargs,
            )
        except DockerException as exc:
            self._save(replace(record, state=WorkspaceState.FAILED, last_used_at=_now()))
            raise ContainerCreateFailedError(
                f"failed to create container for workspace {record.id!r}: {exc}",
                details={"workspace_id": record.id, "image": image},
            ) from exc

        return self._save(
            replace(
                record,
                container_id=container.id,
                image=image,
                state=WorkspaceState.RUNNING,
                last_used_at=_now(),
            )
        )

    # --- lifecycle transitions (§8.4) ----------------------------------------

    def start(self, workspace_id: str) -> WorkspaceRecord:
        record = self._get_or_raise(workspace_id)
        container = self._require_container(record)
        self._save(replace(record, state=WorkspaceState.STARTING, last_used_at=_now()))
        try:
            container.start()
        except DockerException as exc:
            self._save(replace(record, state=WorkspaceState.FAILED, last_used_at=_now()))
            raise ContainerRuntimeFailedError(
                f"failed to start container for workspace {workspace_id!r}: {exc}"
            ) from exc
        return self._save(replace(record, state=WorkspaceState.RUNNING, last_used_at=_now()))

    def stop(self, workspace_id: str) -> WorkspaceRecord:
        record = self._get_or_raise(workspace_id)
        container = self._find_container(record)
        if container is None:
            return self._save(replace(record, state=WorkspaceState.ABSENT, last_used_at=_now()))
        self._save(replace(record, state=WorkspaceState.STOPPING, last_used_at=_now()))
        try:
            container.stop()
        except DockerException as exc:
            self._save(replace(record, state=WorkspaceState.FAILED, last_used_at=_now()))
            raise ContainerRuntimeFailedError(
                f"failed to stop container for workspace {workspace_id!r}: {exc}"
            ) from exc
        # §8.1/§19.2: the key comes out of the agent whenever the
        # workspace stops, including this idle stop's own caller — the
        # agent process itself is left running (`unload_key`'s own
        # docstring explains why).
        if self._ssh_agents is not None:
            self._ssh_agents.unload_key(workspace_id)
        return self._save(replace(record, state=WorkspaceState.STOPPED, last_used_at=_now()))

    def _list_running_persistent(self) -> list[WorkspaceRecord]:
        rows = self._db.execute(
            "SELECT * FROM workspace WHERE state = 'running' AND mode = 'persistent'"
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def sweep_idle_workspaces(
        self,
        *,
        idle_stop_after_seconds: int | None,
        job_store: JobStore,
        audit_config: AuditConfig,
    ) -> list[str]:
        """Stops every persistent, running workspace idle for at least
        `idle_stop_after_seconds` with no running/queued job (§8.1).
        `None` (`idle_stop_after: never`) disables this entirely. Returns
        the ids of workspaces it stopped.

        Called opportunistically from `workspace_open` (S17) — like
        S16's job-log retention, there is no scheduler elsewhere in the
        daemon to call this "on a schedule" instead; a docstring at the
        call site says so.
        """
        if idle_stop_after_seconds is None:
            return []
        now = datetime.now(UTC)
        stopped: list[str] = []
        for record in self._list_running_persistent():
            idle_seconds = (now - datetime.fromisoformat(record.last_used_at)).total_seconds()
            if idle_seconds < idle_stop_after_seconds:
                continue
            active_jobs = [
                job
                for job in job_store.list_by_workspace(record.id)
                if job.state in NON_TERMINAL_STATES
            ]
            if active_jobs:
                continue
            self.stop(record.id)
            write_audit_event(
                audit_config,
                operation="workspace_stopped_idle",
                result="success",
                workspace_id=record.id,
                repository_id=record.repository_id,
                metadata={"idle_seconds": idle_seconds},
            )
            stopped.append(record.id)
        return stopped

    def restart(self, workspace_id: str) -> WorkspaceRecord:
        """§8.4: restart the existing container."""
        record = self._get_or_raise(workspace_id)
        container = self._require_container(record)
        self._save(replace(record, state=WorkspaceState.STARTING, last_used_at=_now()))
        try:
            container.restart()
        except DockerException as exc:
            self._save(replace(record, state=WorkspaceState.FAILED, last_used_at=_now()))
            raise ContainerRuntimeFailedError(
                f"failed to restart container for workspace {workspace_id!r}: {exc}"
            ) from exc
        return self._save(replace(record, state=WorkspaceState.RUNNING, last_used_at=_now()))

    def rebuild(
        self, workspace_id: str, *, image: str, run_kwargs: dict[str, Any] | None = None
    ) -> WorkspaceRecord:
        """§8.4: rebuild the container while preserving the repository.
        The repository lives on the host bind mount (S8), untouched by
        destroying and recreating the container itself."""
        return self._recreate(
            workspace_id, image=image, run_kwargs=run_kwargs, state=WorkspaceState.REBUILDING
        )

    def reset(
        self, workspace_id: str, *, image: str, run_kwargs: dict[str, Any] | None = None
    ) -> WorkspaceRecord:
        """§8.4: discard environment state and recreate from configuration.
        SHALL NOT delete the host repository — same guarantee as `rebuild`,
        since neither touches the bind mount."""
        return self._recreate(
            workspace_id, image=image, run_kwargs=run_kwargs, state=WorkspaceState.REBUILDING
        )

    def _recreate(
        self,
        workspace_id: str,
        *,
        image: str,
        run_kwargs: dict[str, Any] | None,
        state: WorkspaceState,
    ) -> WorkspaceRecord:
        record = self._get_or_raise(workspace_id)
        self._remove_container_if_present(record)
        record = self._save(replace(record, container_id=None, state=state, last_used_at=_now()))
        return self._run_container(record, image=image, run_kwargs=run_kwargs or {})

    def delete(self, workspace_id: str) -> None:
        """§8.4: remove workspace/container metadata. SHALL NOT delete the
        host repository — deletion here is Docker resources and the DB
        row only.

        HR-018 (`docs/HARDENING_SPEC.md` §9): `job.workspace_id`
        references `workspace (id)` with no `ON DELETE CASCADE` — a
        workspace that ran even one job used to make the final
        `DELETE FROM workspace` raise `sqlite3.IntegrityError`, and only
        *after* the container had already been removed above, wedging
        the row in `DELETING` forever (surfaced as a bare traceback, not
        a `WorkspaceError` `translate_workspace_errors` could shape into
        a clean response). Dependent `job` rows are now deleted first,
        and both deletes commit together in one transaction — so a
        failure at this stage can no longer split "container gone" from
        "row still present" the way the pre-fix code could.
        """
        record = self._get_or_raise(workspace_id)
        self._save(replace(record, state=WorkspaceState.DELETING, last_used_at=_now()))
        self._remove_container_if_present(record)
        if self._ssh_agents is not None:
            self._ssh_agents.terminate(workspace_id)  # §19.2: no agent after delete
        with self._db:
            self._db.execute("DELETE FROM job WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM workspace WHERE id = ?", (workspace_id,))

    def _remove_container_if_present(self, record: WorkspaceRecord) -> None:
        container = self._find_container(record)
        if container is None:
            return
        try:
            container.remove(force=True)
        except DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed to remove container for workspace {record.id!r}: {exc}"
            ) from exc

    # --- container lookup -----------------------------------------------------

    def _find_container(self, record: WorkspaceRecord) -> Container | None:
        if record.container_id is None:
            return None
        try:
            return self._docker.containers.get(record.container_id)
        except DockerNotFound:
            return None

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
        """Reconcile the DB record for `workspace_id` against actual Docker
        state (§69):

        - the recorded container is missing -> mark the workspace `absent`;
        - an unrecorded but correctly labeled container exists -> adopt it;
        - more than one labeled container claims this workspace -> fail
          closed, this needs an operator, not a guess.
        """
        record = self._get_or_raise(workspace_id)

        labeled = self._docker.containers.list(
            all=True, filters={"label": f"{LABEL_WORKSPACE_ID}={workspace_id}"}
        )
        if len(labeled) > 1:
            raise ContainerRuntimeFailedError(
                f"multiple containers are labeled for workspace {workspace_id!r}; "
                "this requires operator reconciliation (§69)",
                details={"workspace_id": workspace_id, "container_ids": [c.id for c in labeled]},
                recoverable=False,
            )

        if record.container_id is not None:
            container = self._find_container(record)
            if container is None:
                return self._save(
                    replace(
                        record, container_id=None, state=WorkspaceState.ABSENT, last_used_at=_now()
                    )
                )
            return self._save(
                replace(
                    record,
                    state=_state_from_docker_container(container),
                    last_used_at=_now(),
                )
            )

        if labeled:
            (container,) = labeled
            return self._save(
                replace(
                    record,
                    container_id=container.id,
                    state=_state_from_docker_container(container),
                    last_used_at=_now(),
                )
            )

        return record

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
        for workspace_id in ids:
            try:
                reconciled.append(self.reconcile(workspace_id))
            except ContainerRuntimeFailedError:
                continue
        return reconciled


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
