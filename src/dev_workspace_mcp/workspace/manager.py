"""Docker workspace lifecycle (§6.2, §8.2, §69).

Container image, user, and mount configuration are supplied by the caller
through `run_kwargs` — S7 (default image, container user) and S8 (mounts)
own those decisions. This module owns creation, naming, labeling, state
tracking, and reconciliation: the mechanism `workspace_open` (S11) sits on
top of, not a finished workspace by itself.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import docker
from docker.errors import DockerException
from docker.errors import NotFound as DockerNotFound
from docker.models.containers import Container

from dev_workspace_mcp.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.naming import (
    LABEL_WORKSPACE_ID,
    WorkspaceMode,
    container_labels,
    container_name,
    ephemeral_workspace_id,
    persistent_workspace_id,
)


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


class DockerWorkspaceManager:
    def __init__(self, docker_client: docker.DockerClient, db: sqlite3.Connection) -> None:
        self._docker = docker_client
        self._db = db

    # --- persistence (§3's workspace table) ---------------------------------

    def _load(self, workspace_id: str) -> WorkspaceRecord | None:
        row = self._db.execute("SELECT * FROM workspace WHERE id = ?", (workspace_id,)).fetchone()
        if row is None:
            return None
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

    def _get_or_raise(self, workspace_id: str) -> WorkspaceRecord:
        record = self._load(workspace_id)
        if record is None:
            raise WorkspaceNotFoundError(f"workspace not found: {workspace_id!r}")
        return record

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
        """Create a new workspace, or reconcile and (re)start an existing
        one. The mechanism behind `workspace_open` (§8.3).

        `mode: "ephemeral"` always creates a fresh workspace (FR-005) —
        there is no "the" ephemeral workspace for a repository to reuse.
        """
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
        return self.start(workspace_id)

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

    def _run_container(
        self, record: WorkspaceRecord, *, image: str, run_kwargs: dict[str, Any]
    ) -> WorkspaceRecord:
        """Run a new container for `record` (whose row already exists) and
        persist the result. Shared by `_create` and `_recreate`."""
        assert record.container_name is not None
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
        return self._save(replace(record, state=WorkspaceState.STOPPED, last_used_at=_now()))

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
        row only."""
        record = self._get_or_raise(workspace_id)
        self._save(replace(record, state=WorkspaceState.DELETING, last_used_at=_now()))
        self._remove_container_if_present(record)
        self._db.execute("DELETE FROM workspace WHERE id = ?", (workspace_id,))
        self._db.commit()

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
                    state=_state_from_docker_status(container.status),
                    last_used_at=_now(),
                )
            )

        if labeled:
            (container,) = labeled
            return self._save(
                replace(
                    record,
                    container_id=container.id,
                    state=_state_from_docker_status(container.status),
                    last_used_at=_now(),
                )
            )

        return record


def _state_from_docker_status(status: str) -> WorkspaceState:
    return WorkspaceState.RUNNING if status == "running" else WorkspaceState.STOPPED
