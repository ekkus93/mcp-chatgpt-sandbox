"""`_LifecycleMixin` — the creation (§8.3) and lifecycle-transition (§8.4)
two-thirds of `DockerWorkspaceManager`.

Extracted from `workspace/manager.py` — see `records.py`'s module docstring
for the full split rationale. Inherits from `manager_core.py`'s
`_CoreMixin`: every method here that isn't purely self-contained calls into
the persistence (`_load`/`_save`/`_insert`/`_get_or_raise`), container-lookup
(`_find_container`/`_require_container`), or reconciliation (`reconcile`)
layer that module owns — real inheritance, not a `Protocol`/self-type
trick, since nothing in `_CoreMixin` calls back into this module (the
dependency runs one way). `manager.py`'s `DockerWorkspaceManager` composes
this in directly (`class DockerWorkspaceManager(_LifecycleMixin):`) and
picks up `_CoreMixin`'s methods transitively.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docker.errors import DockerException
from docker.errors import NotFound as DockerNotFound
from docker.models.containers import Container

from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import AuditConfig
from dev_workspace_mcp.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceError,
)
from dev_workspace_mcp.jobs.store import NON_TERMINAL_STATES, JobStore
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager_core import _CoreMixin
from dev_workspace_mcp.workspace.naming import (
    LABEL_WORKSPACE_ID,
    WorkspaceMode,
    container_labels,
    container_name,
    ephemeral_workspace_id,
    persistent_workspace_id,
)
from dev_workspace_mcp.workspace.records import (
    WorkspaceRecord,
    WorkspaceState,
    _now,
    _oom_killed_from_docker_container,
    _state_from_docker_container,
)

_logger = logging.getLogger(__name__)


class _LifecycleMixin(_CoreMixin):
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
        except DockerException as exc:
            # HR-022 (`docs/HARDENING_SPEC.md` §10): a transient Docker
            # API error here (unlike "not found") isn't safe to treat as
            # "no container" — translated to a structured error rather
            # than left to escape uncaught past `translate_workspace_errors`.
            raise ContainerRuntimeFailedError(
                f"failed to look up container {name!r}: {exc}",
                details={"container_name": name},
            ) from exc

    def _adopt_or_remove_by_name(
        self, record: WorkspaceRecord, *, allow_adopt: bool
    ) -> WorkspaceRecord | None:
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
        if it's safe to proceed to `containers.run`.

        RR-014 (`docs/REMEDIATION_SPEC.md` §11): `allow_adopt=False` for
        `_recreate` (`rebuild`/`reset`) — adoption is HR-017's crash-
        recovery mechanism for `_create`/`ensure_workspace`'s own "no
        container to start" case, and is never appropriate for
        `rebuild`/`reset`, whose entire point is guaranteeing a genuinely
        new container regardless of what's already there. `record.container_id`
        being `None` when `_recreate` runs (H15 scenario #2: a labeled
        container survives at the deterministic name even though the row
        lost track of it) used to reach this method with adoption still
        enabled, silently returning the stale container instead of
        rebuilding/resetting it — reproduced empirically. `allow_adopt=False`
        routes that same labeled container into the removal branch below
        instead, exactly like any other occupant of the name."""
        assert record.container_name is not None
        existing = self._find_container_by_name(record.container_name)
        if existing is None:
            return None
        if allow_adopt and existing.labels.get(LABEL_WORKSPACE_ID) == record.id:
            return self._save(
                replace(
                    record,
                    container_id=existing.id,
                    state=_state_from_docker_container(existing),
                    oom_killed=_oom_killed_from_docker_container(existing),
                    last_used_at=_now(),
                )
            )
        with contextlib.suppress(DockerException):
            existing.remove(force=True)
        return None

    def _run_container(
        self,
        record: WorkspaceRecord,
        *,
        image: str,
        run_kwargs: dict[str, Any],
        allow_adopt: bool = True,
    ) -> WorkspaceRecord:
        """Run a new container for `record` (whose row already exists) and
        persist the result. Shared by `_create` and `_recreate` —
        `allow_adopt=False` for the latter (RR-014, see
        `_adopt_or_remove_by_name`'s own docstring)."""
        assert record.container_name is not None
        adopted = self._adopt_or_remove_by_name(record, allow_adopt=allow_adopt)
        if adopted is not None:
            # RR-014: an adopted container is not necessarily running —
            # returning it as-is used to propagate a non-running state
            # straight out of `ensure_workspace` with no `start()`, so
            # the first `workspace_open` after adopting a stopped orphan
            # reported `STOPPED` and the very next tool call raised
            # `WORKSPACE_NOT_RUNNING`; it self-healed only on a *second*
            # `workspace_open` (`ensure_workspace`'s own reconcile branch
            # calls `start()` once `container_id` is populated). Started
            # here instead, so a caller always gets back a record that is
            # actually ready to use on the first call.
            if adopted.state == WorkspaceState.RUNNING:
                return adopted
            return self.start(adopted.id)
        try:
            container = self._docker.containers.run(
                image,
                name=record.container_name,
                detach=True,
                labels=container_labels(workspace_id=record.id, repository_id=record.repository_id),
                **run_kwargs,
            )
        except DockerException as exc:
            self._save(
                replace(record, state=WorkspaceState.FAILED, oom_killed=False, last_used_at=_now())
            )
            _logger.warning(
                "workspace %s: container create failed (image=%s): %s", record.id, image, exc
            )
            raise ContainerCreateFailedError(
                f"failed to create container for workspace {record.id!r}: {exc}",
                details={"workspace_id": record.id, "image": image},
            ) from exc

        _logger.info(
            "workspace %s: container %s created (image=%s)",
            record.id,
            record.container_name,
            image,
        )
        # RR-021: recomputed, not left stale — see `start()`'s own
        # comment above. Reachable via `_recreate` (`rebuild`/`reset`)
        # too, where `record` at this point still carries whatever
        # `oom_killed` value the *old*, just-removed container had —
        # reproduced empirically that a rebuild/reset after an OOM kill
        # otherwise stamped that stale `True` onto the brand-new
        # container's own record, which cannot possibly have OOM'd yet.
        container.reload()
        return self._save(
            replace(
                record,
                container_id=container.id,
                image=image,
                state=WorkspaceState.RUNNING,
                oom_killed=_oom_killed_from_docker_container(container),
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
            self._save(
                replace(record, state=WorkspaceState.FAILED, oom_killed=False, last_used_at=_now())
            )
            _logger.warning("workspace %s: container start failed: %s", workspace_id, exc)
            raise ContainerRuntimeFailedError(
                f"failed to start container for workspace {workspace_id!r}: {exc}"
            ) from exc
        _logger.info("workspace %s: container started", workspace_id)
        # RR-021 (`docs/REMEDIATION_SPEC.md` §15): `oom_killed` recomputed
        # from the container's own current state, not left as whatever
        # `record` (read at the top, before this transition) happened to
        # carry — reproduced empirically that a workspace which OOM'd,
        # then successfully restarted, kept reporting `oom_killed: true`
        # indefinitely. `container.start()` does not itself refresh
        # `container.attrs` (confirmed empirically — `Running` still read
        # `False` immediately after a successful `start()` without this),
        # so `reload()` is required before `_oom_killed_from_docker_container`
        # can see the field's *current* value rather than a stale snapshot
        # from before this call.
        container.reload()
        return self._save(
            replace(
                record,
                state=WorkspaceState.RUNNING,
                oom_killed=_oom_killed_from_docker_container(container),
                last_used_at=_now(),
            )
        )

    def stop(self, workspace_id: str) -> WorkspaceRecord:
        record = self._get_or_raise(workspace_id)
        container = self._find_container(record)
        if container is None:
            return self._save(replace(record, state=WorkspaceState.ABSENT, last_used_at=_now()))
        self._save(replace(record, state=WorkspaceState.STOPPING, last_used_at=_now()))
        try:
            container.stop()
        except DockerException as exc:
            self._save(
                replace(record, state=WorkspaceState.FAILED, oom_killed=False, last_used_at=_now())
            )
            _logger.warning("workspace %s: container stop failed: %s", workspace_id, exc)
            raise ContainerRuntimeFailedError(
                f"failed to stop container for workspace {workspace_id!r}: {exc}"
            ) from exc
        _logger.info("workspace %s: container stopped", workspace_id)
        # RR-021: recomputed, not left stale — see `start()`'s own
        # comment above. `stop()` specifically also covers the case
        # where `container` was *already* stopped (an OOM kill that
        # happened before this call, `.stop()` on it a safe no-op): the
        # reload here correctly *preserves* `oom_killed: true` for that
        # case rather than assuming a successful `stop()` call always
        # means "we ourselves stopped a healthy container."
        container.reload()
        # §8.1/§19.2: the key comes out of the agent whenever the
        # workspace stops, including this idle stop's own caller — the
        # agent process itself is left running (`unload_key`'s own
        # docstring explains why).
        if self._ssh_agents is not None:
            self._ssh_agents.unload_key(workspace_id)
        return self._save(
            replace(
                record,
                state=WorkspaceState.STOPPED,
                oom_killed=_oom_killed_from_docker_container(container),
                last_used_at=_now(),
            )
        )

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
            try:
                self.stop(record.id)
            except WorkspaceError as exc:
                # G2R-011 (`docs/GAP2_SPEC.md`): the sweep is
                # opportunistic background work riding along on an
                # unrelated `workspace_open` call (S17) — a failure to
                # stop *this* idle workspace is not a reason to abort
                # the sweep for every other workspace still due, and it
                # must never surface as a failure of the request that
                # happened to trigger it. The same RR-012 principle
                # (`docs/REMEDIATION_SPEC.md` §10) `delete()`'s own
                # comment above already establishes for cleanup-adjacent
                # failures. Logged, not raised (`stop()` itself already
                # logs the underlying Docker failure at `:359`, so this
                # is deliberately terser); the workspace is simply left
                # running and picked up by a later sweep.
                _logger.warning(
                    "sweep_idle_workspaces: failed to stop workspace %s: %s", record.id, exc
                )
                continue
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
            self._save(
                replace(record, state=WorkspaceState.FAILED, oom_killed=False, last_used_at=_now())
            )
            raise ContainerRuntimeFailedError(
                f"failed to restart container for workspace {workspace_id!r}: {exc}"
            ) from exc
        # RR-021: recomputed, not left stale — see `start()`'s own comment.
        container.reload()
        return self._save(
            replace(
                record,
                state=WorkspaceState.RUNNING,
                oom_killed=_oom_killed_from_docker_container(container),
                last_used_at=_now(),
            )
        )

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
        # RR-014: adoption disabled — `rebuild`/`reset` SHALL always
        # produce a genuinely new container, never silently return
        # whatever happens to already occupy the deterministic name.
        return self._run_container(
            record, image=image, run_kwargs=run_kwargs or {}, allow_adopt=False
        )

    def delete(
        self,
        workspace_id: str,
        *,
        cache_volume_names: list[str] | None = None,
        state_dir: Path | None = None,
    ) -> None:
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

        HR-009 (`docs/HARDENING_SPEC.md` §4.2): `cache_volume_names`,
        when the caller has them (`cli.py`'s `delete` command, from the
        repository's `.mcp-workspace.yml`), are removed here too — a
        cache volume this module itself has no way to discover on its
        own (nothing links a Docker volume back to the `repository_id`
        that created it; see `workspace/caches.py`'s own module
        docstring). Without this, a repository id later reused for a
        genuinely different project would silently *inherit* the old
        project's cache volumes by name — caches hold executables, so
        this is a cross-repository write primitive, not only leftover
        disk usage.

        RR-012 (`docs/REMEDIATION_SPEC.md` §10): cache-volume removal and
        ssh-agent termination run *after* the row deletion, and neither
        can raise — HR-018's second clause ("a failure SHALL NOT leave
        the container removed and the row in `deleting`") SHALL hold for
        every failure point, not only the `job` foreign-key one it was
        written for. Reproduced end to end against a real Docker daemon:
        a cache volume held in use by another container (§4.2's own
        cross-workspace sharing is exactly what makes this reachable)
        made the old code raise `ContainerRuntimeFailedError` *between*
        container removal and row deletion — container gone, row stuck
        in `deleting` permanently (a retry finds no container and hits
        the identical volume-removal failure again, forever). Neither
        cleanup step is what makes a workspace "deleted" — the container
        and the rows are — so both now run only after those two are
        already gone, and are best-effort (logged, not raised) on top of
        that as a second, independent guarantee that neither can ever
        again come between anything.

        G2R-015 (`docs/GAP2_SPEC.md`): deleting the `job` rows below used
        to leave their `<state_dir>/jobs/<job_id>/` log directories
        behind — nothing else ever deletes them, and `prune_job_logs`
        (`jobs/retention.py`) enumerates candidates from `job_store`, so
        a row-less directory is invisible to it and permanently
        unreachable, both as leftover disk usage and as a silent
        under-count in `job_log_max_total_bytes` accounting (still-live
        jobs get pruned to compensate for space the orphans are actually
        using). Chose "remove the directories here" over "make retention
        scan the directory tree instead of the table": `delete` already
        knows exactly which job ids belonged to this workspace, from the
        rows it is about to delete — scanning `<state_dir>/jobs/` and
        diffing against `job_store` to rediscover the same information
        would be strictly more work for the same result, and would still
        leave orphans sitting on disk between a delete and the next
        opportunistic `prune_job_logs` call rather than reclaiming them
        immediately. `state_dir` is optional and `None` by default (a
        caller with no on-disk job logs to worry about, or that hasn't
        been updated yet, gets the exact old behavior) — deliberately
        NOT derived from a job row's own `stdout_log_path` string: every
        directory this method ever removes is reconstructed as
        `state_dir / "jobs" / job_id` from the caller-supplied root and
        the job's own id (a random hex string, `execution.py`'s
        `_new_job_id`), never trusted from a stored path that test
        fixtures elsewhere in this codebase set to placeholders like
        `/dev/null` — an unconfined `shutil.rmtree` of that string's
        parent would target `/dev` itself.
        """
        record = self._get_or_raise(workspace_id)
        self._save(replace(record, state=WorkspaceState.DELETING, last_used_at=_now()))
        self._remove_container_if_present(record)
        # Read *before* the `DELETE FROM job` below — once those rows are
        # gone, nothing links a job id back to its on-disk directory.
        job_ids = [
            row["id"]
            for row in self._db.execute(
                "SELECT id FROM job WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        ]
        with self._db:
            self._db.execute("DELETE FROM job WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM workspace WHERE id = ?", (workspace_id,))
        _logger.info("workspace %s: deleted", workspace_id)
        if state_dir is not None:
            self._remove_job_log_dirs(state_dir, job_ids)
        if cache_volume_names:
            self._remove_cache_volumes(cache_volume_names)
        if self._ssh_agents is not None:
            self._terminate_ssh_agent(workspace_id)  # §19.2: no agent after delete

    def _remove_job_log_dirs(self, state_dir: Path, job_ids: list[str]) -> None:
        """G2R-015: best-effort, the same RR-012 reasoning
        `_remove_cache_volumes`/`_terminate_ssh_agent` below already
        apply — the workspace is already fully deleted (container and
        rows both gone) by the time this runs, so one directory this
        daemon somehow can't remove (a permission problem, a stray
        concurrent reader) is logged and left behind, never a reason to
        report `delete` itself as failed."""
        for job_id in job_ids:
            job_dir = state_dir / "jobs" / job_id
            try:
                shutil.rmtree(job_dir)
            except FileNotFoundError:
                continue  # already gone — nothing to reclaim
            except OSError as exc:
                _logger.warning(
                    "workspace delete: failed to remove job log directory %s: %s", job_dir, exc
                )

    def _remove_cache_volumes(self, cache_volume_names: list[str]) -> None:
        for name in cache_volume_names:
            try:
                volume = self._docker.volumes.get(name)
            except DockerNotFound:
                continue  # never created (the cache was declared but never actually used)
            try:
                volume.remove(force=True)
            except DockerException as exc:
                # RR-012: best-effort — the workspace is already fully
                # deleted (container and rows both gone) by the time this
                # runs, so a volume genuinely still in use by another
                # workspace of the same repository (§4.2's sharing) is
                # logged and left for a later delete to reclaim, never a
                # reason to report this call itself as failed.
                _logger.warning("workspace delete: failed to remove cache volume %r: %s", name, exc)

    def _terminate_ssh_agent(self, workspace_id: str) -> None:
        """RR-012: best-effort, deliberately broader than
        `SSHAgentManager.terminate`'s own internal handling (which only
        covers the signal itself, per its own docstring) — this call
        site is what §8.4/HR-018 actually need never able to leave the
        workspace's own deletion incomplete or reported as failed."""
        assert self._ssh_agents is not None  # checked by the one caller
        try:
            self._ssh_agents.terminate(workspace_id)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see above
            _logger.warning(
                "workspace delete: failed to terminate ssh-agent for %s: %s", workspace_id, exc
            )

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
