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
to live here directly, so `from mcp_chatgpt_sandbox.workspace.manager import
DockerWorkspaceManager` (etc.) continues to resolve exactly as it did
before the split.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from docker.errors import DockerException
from docker.errors import NotFound as DockerNotFound
from docker.models.containers import Container

from mcp_chatgpt_sandbox.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from mcp_chatgpt_sandbox.jobs.retention import (
    attempt_job_log_cleanup,
    get_job_log_cleanup_debt,
)
from mcp_chatgpt_sandbox.operations.resources import (
    cache_volume_labels,
    classify_cache_volume,
    classify_workspace_container,
)
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.workspace.manager_lifecycle import _LifecycleMixin
from mcp_chatgpt_sandbox.workspace.naming import LABEL_WORKSPACE_ID
from mcp_chatgpt_sandbox.workspace.records import (
    WorkspaceRecord,
    WorkspaceState,
    _now,
    _oom_killed_from_docker_container,
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
    """Workspace lifecycle with PMOR-001 positive resource ownership.

    O1 intentionally overrides the few inherited Docker-resource chokepoints
    rather than teaching later inventory/prune code a second ownership model.
    Deterministic names remain addresses only: no container or volume is
    adopted, removed, or reused unless its labels positively prove ownership.
    """

    def _find_container(self, record: WorkspaceRecord) -> Container | None:
        if record.container_id is None:
            return None
        try:
            container = self._docker.containers.get(record.container_id)
        except DockerNotFound:
            return None
        except DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed to look up container {record.container_id!r} "
                f"for workspace {record.id!r}: {exc}",
                details={"workspace_id": record.id},
            ) from exc

        proof = classify_workspace_container(
            container.labels,
            expected_workspace_id=record.id,
            expected_repository_id=record.repository_id,
        )
        if not proof.positively_owned:
            raise ContainerRuntimeFailedError(
                f"container {record.container_id!r} for workspace {record.id!r} "
                f"does not have positive MCP ownership proof: {proof.reason}",
                details={
                    "workspace_id": record.id,
                    "container_id": record.container_id,
                    "ownership_status": proof.status.value,
                },
                recoverable=False,
            )
        return container

    def reconcile(self, workspace_id: str) -> WorkspaceRecord:
        """Reconcile only containers with full PMOR-001 ownership proof."""
        record = self._get_or_raise(workspace_id)
        try:
            discovered = self._docker.containers.list(
                all=True, filters={"label": f"{LABEL_WORKSPACE_ID}={workspace_id}"}
            )
        except DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed to list containers for workspace {workspace_id!r}: {exc}",
                details={"workspace_id": workspace_id},
            ) from exc

        owned: list[Container] = []
        for container in discovered:
            proof = classify_workspace_container(
                container.labels,
                expected_workspace_id=record.id,
                expected_repository_id=record.repository_id,
            )
            if proof.positively_owned:
                owned.append(container)
            else:
                _logger.warning(
                    "workspace %s: ignoring container %s claiming the workspace label "
                    "without positive ownership proof (%s: %s)",
                    workspace_id,
                    container.id,
                    proof.status.value,
                    proof.reason,
                )

        if len(owned) > 1:
            raise ContainerRuntimeFailedError(
                f"multiple positively owned containers are labeled for workspace {workspace_id!r}; "
                "this requires operator reconciliation (§69)",
                details={"workspace_id": workspace_id, "container_ids": [c.id for c in owned]},
                recoverable=False,
            )

        if record.container_id is not None:
            recorded_container = self._find_container(record)
            if recorded_container is None:
                return self._save(
                    replace(
                        record,
                        container_id=None,
                        state=WorkspaceState.ABSENT,
                        oom_killed=False,
                        last_used_at=_now(),
                    )
                )
            return self._save(
                replace(
                    record,
                    state=_state_from_docker_container(recorded_container),
                    oom_killed=_oom_killed_from_docker_container(recorded_container),
                    last_used_at=_now(),
                )
            )

        if owned:
            (container,) = owned
            return self._save(
                replace(
                    record,
                    container_id=container.id,
                    state=_state_from_docker_container(container),
                    oom_killed=_oom_killed_from_docker_container(container),
                    last_used_at=_now(),
                )
            )
        return record

    def _ensure_owned_volume_mounts(
        self, record: WorkspaceRecord, run_kwargs: dict[str, Any]
    ) -> None:
        """Explicitly create/verify volume mounts before Docker can mount them.

        All volume-type mounts assembled for a workspace are dependency-cache
        volumes in the current architecture. Creating them explicitly is
        load-bearing: Docker's implicit mount-time creation cannot attach
        ownership labels, and an already-existing same-named volume cannot be
        safely relabeled/adopted because the name itself is not proof.
        """
        sources: set[str] = set()
        for mount in run_kwargs.get("mounts") or []:
            if mount.get("Type") != "volume":
                continue
            source = mount.get("Source")
            if not isinstance(source, str) or not source:
                raise ContainerCreateFailedError(
                    "workspace volume mount has no valid named-volume source",
                    details={"workspace_id": record.id},
                )
            sources.add(source)

        labels = cache_volume_labels(repository_id=record.repository_id)
        for source in sorted(sources):
            try:
                volume = self._docker.volumes.get(source)
            except DockerNotFound:
                try:
                    volume = self._docker.volumes.create(name=source, labels=labels)
                except DockerException as exc:
                    raise ContainerCreateFailedError(
                        f"failed to create owned cache volume {source!r}: {exc}",
                        details={"workspace_id": record.id, "volume_name": source},
                    ) from exc
            except DockerException as exc:
                raise ContainerCreateFailedError(
                    f"failed to inspect cache volume {source!r}: {exc}",
                    details={"workspace_id": record.id, "volume_name": source},
                ) from exc

            # Docker volume create is name-idempotent. A foreign volume can
            # appear between the get() above and create(), and Docker may return
            # that existing object instead of applying our requested labels.
            # Re-inspect the object we will actually mount and prove ownership
            # after either path; the pre-create name lookup is never authority.
            try:
                volume.reload()
            except DockerException as exc:
                raise ContainerCreateFailedError(
                    f"failed to verify cache volume {source!r} ownership: {exc}",
                    details={"workspace_id": record.id, "volume_name": source},
                ) from exc
            proof = classify_cache_volume(
                volume.attrs.get("Labels") or {},
                expected_repository_id=record.repository_id,
            )
            if not proof.positively_owned:
                raise ContainerCreateFailedError(
                    f"refusing to reuse cache volume {source!r} without positive MCP ownership "
                    f"proof: {proof.reason}",
                    details={
                        "workspace_id": record.id,
                        "volume_name": source,
                        "ownership_status": proof.status.value,
                    },
                )

    def _run_container(
        self,
        record: WorkspaceRecord,
        *,
        image: str,
        run_kwargs: dict[str, Any],
        allow_adopt: bool = True,
    ) -> WorkspaceRecord:
        try:
            self._ensure_owned_volume_mounts(record, run_kwargs)
            return super()._run_container(
                record,
                image=image,
                run_kwargs=run_kwargs,
                allow_adopt=allow_adopt,
            )
        except ContainerCreateFailedError:
            self._save(
                replace(
                    record,
                    container_id=None,
                    state=WorkspaceState.FAILED,
                    oom_killed=False,
                    last_used_at=_now(),
                )
            )
            raise

    def _adopt_or_remove_by_name(
        self, record: WorkspaceRecord, *, allow_adopt: bool
    ) -> WorkspaceRecord | None:
        """Resolve deterministic-name collisions without name-as-ownership fallback."""
        assert record.container_name is not None
        existing = self._find_container_by_name(record.container_name)
        if existing is None:
            return None
        proof = classify_workspace_container(
            existing.labels,
            expected_workspace_id=record.id,
            expected_repository_id=record.repository_id,
        )
        if not proof.positively_owned:
            raise ContainerCreateFailedError(
                f"container name {record.container_name!r} is occupied by a resource whose MCP "
                f"ownership cannot be proven; refusing to adopt or remove it: {proof.reason}",
                details={
                    "workspace_id": record.id,
                    "container_name": record.container_name,
                    "container_id": existing.id,
                    "ownership_status": proof.status.value,
                },
            )
        if allow_adopt:
            return self._save(
                replace(
                    record,
                    container_id=existing.id,
                    state=_state_from_docker_container(existing),
                    oom_killed=_oom_killed_from_docker_container(existing),
                    last_used_at=_now(),
                )
            )
        try:
            existing.remove(force=True)
        except DockerException as exc:
            raise ContainerCreateFailedError(
                f"failed to remove positively owned container {existing.id!r} before recreation: "
                f"{exc}",
                details={"workspace_id": record.id, "container_id": existing.id},
            ) from exc
        return None

    def delete(
        self,
        workspace_id: str,
        *,
        cache_volume_names: list[str] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        """Delete while binding cache cleanup to durable repository identity.

        The inherited lifecycle implementation deliberately performs cache
        cleanup only after the workspace/job rows are committed gone. PMOR-001
        additionally requires the cache's repository label to correspond to
        the workspace being deleted. Capture that trusted identity from the
        pre-delete WorkspaceRecord, delegate the primary deletion with cache
        cleanup disabled, then run the same best-effort cleanup under that
        positive proof. Cache names remain addresses only.
        """
        record = self._get_or_raise(workspace_id)
        super().delete(workspace_id, cache_volume_names=None, state_dir=state_dir)
        if cache_volume_names:
            self._remove_cache_volumes(
                cache_volume_names,
                expected_repository_id=record.repository_id,
            )

    def _remove_cache_volumes(
        self,
        cache_volume_names: list[str],
        *,
        expected_repository_id: str | None = None,
    ) -> None:
        """Best-effort delete only positively owned cache volumes.

        The workspace row is already gone when this cleanup runs, so failure
        remains non-fatal per RR-012. The normal delete path supplies the
        repository id captured from durable workspace state before deletion.
        If an inherited or future caller omits that proof, cleanup fails closed:
        no volume is deleted merely from its name plus self-asserted labels.
        """
        if expected_repository_id is None:
            _logger.warning(
                "workspace delete: leaving %d cache volume(s) untouched because durable "
                "repository ownership identity was not provided",
                len(cache_volume_names),
            )
            return

        for name in cache_volume_names:
            try:
                volume = self._docker.volumes.get(name)
                volume.reload()
            except DockerNotFound:
                continue
            except DockerException as exc:
                _logger.warning(
                    "workspace delete: failed to inspect cache volume %r: %s", name, exc
                )
                continue
            proof = classify_cache_volume(
                volume.attrs.get("Labels") or {},
                expected_repository_id=expected_repository_id,
            )
            if not proof.positively_owned:
                _logger.warning(
                    "workspace delete: leaving cache volume %r untouched because ownership is "
                    "not proven (%s: %s)",
                    name,
                    proof.status.value,
                    proof.reason,
                )
                continue
            try:
                volume.remove(force=True)
            except DockerException as exc:
                _logger.warning("workspace delete: failed to remove cache volume %r: %s", name, exc)

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
