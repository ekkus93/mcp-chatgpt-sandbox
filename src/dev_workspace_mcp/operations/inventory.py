"""Bounded, side-effect-free operational resource inventory.

Post-MVP Operations O2 / PMOR-002..003 builds on O1's positive ownership
classifiers.  This module is deliberately internal: O3 will expose the same
service through MCP after the read-only behavior and bounds are independently
validated here.

Inventory may inspect Docker metadata, SQLite state, and filesystem metadata.
It never stops/removes/prunes Docker resources, runs retention, or recursively
walks repository contents.  Names remain addresses only; ownership comes from
O1 evidence.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from docker.errors import DockerException

from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
    classify_cache_volume,
    classify_workspace_container,
)
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED, LABEL_REPOSITORY_ID


class ByteProvenance(StrEnum):
    MEASURED = "measured"
    ACCOUNTED = "accounted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ByteObservation:
    value: int | None
    provenance: ByteProvenance
    reason: str | None = None

    @classmethod
    def measured(cls, value: int) -> ByteObservation:
        return cls(value=max(value, 0), provenance=ByteProvenance.MEASURED)

    @classmethod
    def accounted(cls, value: int) -> ByteObservation:
        return cls(value=max(value, 0), provenance=ByteProvenance.ACCOUNTED)

    @classmethod
    def unavailable(cls, reason: str) -> ByteObservation:
        return cls(value=None, provenance=ByteProvenance.UNAVAILABLE, reason=reason)


@dataclass(frozen=True, slots=True)
class InventoryDiagnostic:
    code: str
    message: str
    resource_kind: ResourceKind | None = None
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryResource:
    kind: ResourceKind
    resource_id: str
    lifecycle_state: ResourceLifecycleState
    ownership: OwnershipProof
    bytes: ByteObservation
    repository_id: str | None = None
    workspace_id: str | None = None
    metrics: Mapping[str, ByteObservation] = field(default_factory=dict)
    details: Mapping[str, str | int | bool | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_resources: int = 256
    max_inspections: int = 512
    max_diagnostics: int = 64

    def __post_init__(self) -> None:
        if self.max_resources <= 0:
            raise ValueError("max_resources must be positive")
        if self.max_inspections <= 0:
            raise ValueError("max_inspections must be positive")
        if self.max_diagnostics <= 0:
            raise ValueError("max_diagnostics must be positive")


@dataclass(frozen=True, slots=True)
class ResourceInventoryResult:
    resources: tuple[InventoryResource, ...]
    diagnostics: tuple[InventoryDiagnostic, ...]
    inspected_count: int
    result_limit: int
    inspection_limit: int
    truncated: bool
    incomplete: bool
    diagnostics_truncated: bool


class _DockerCollection(Protocol):
    def list(self, *args: object, **kwargs: object) -> list[Any]: ...


class _DockerClient(Protocol):
    containers: _DockerCollection
    volumes: _DockerCollection


def _safe_failure(exc: BaseException) -> str:
    """Return a path/secret-safe diagnostic classification.

    O2 diagnostics intentionally avoid echoing exception text: Docker and host
    exceptions frequently embed paths, commands, URLs, or credentials.  Errno
    is useful evidence for filesystem failures and can be rendered without the
    original path-bearing message.
    """

    if isinstance(exc, OSError) and exc.errno is not None:
        try:
            description = os.strerror(exc.errno)
        except ValueError:
            description = "OS error"
        return f"{type(exc).__name__} (errno {exc.errno}: {description})"
    return type(exc).__name__


def _owned_state_proof(*evidence: str) -> OwnershipProof:
    return OwnershipProof(
        status=OwnershipStatus.OWNED,
        evidence=tuple(evidence),
        reason="positive durable-state ownership proof",
    )


class ResourceInventoryService:
    """Collect one bounded PMOR-002/003 inventory snapshot."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        docker_client: _DockerClient,
        state_db_path: Path,
        limits: InventoryLimits | None = None,
    ) -> None:
        self._connection = connection
        self._docker = docker_client
        self._state_db_path = state_db_path
        self._limits = limits or InventoryLimits()
        self._resources: list[InventoryResource] = []
        self._diagnostics: list[InventoryDiagnostic] = []
        self._inspected = 0
        self._truncated = False
        self._incomplete = False
        self._diagnostics_truncated = False

    def collect(self) -> ResourceInventoryResult:
        """Inspect bounded sources without mutating Docker, state, or retention."""

        self._reset()
        self._collect_containers()
        self._collect_volumes()
        self._collect_job_logs()
        self._collect_cleanup_debt()
        self._collect_ssh_agents()
        self._collect_state_storage()
        return ResourceInventoryResult(
            resources=tuple(self._resources),
            diagnostics=tuple(self._diagnostics),
            inspected_count=self._inspected,
            result_limit=self._limits.max_resources,
            inspection_limit=self._limits.max_inspections,
            truncated=self._truncated,
            incomplete=self._incomplete,
            diagnostics_truncated=self._diagnostics_truncated,
        )

    def _reset(self) -> None:
        self._resources.clear()
        self._diagnostics.clear()
        self._inspected = 0
        self._truncated = False
        self._incomplete = False
        self._diagnostics_truncated = False

    def _remaining_candidate_budget(self) -> int:
        return min(
            self._limits.max_resources - len(self._resources),
            self._limits.max_inspections - self._inspected,
        )

    def _begin_inspection(self) -> bool:
        if len(self._resources) >= self._limits.max_resources:
            self._truncated = True
            self._incomplete = True
            return False
        if self._inspected >= self._limits.max_inspections:
            self._truncated = True
            self._incomplete = True
            return False
        self._inspected += 1
        return True

    def _add_diagnostic(
        self,
        *,
        code: str,
        message: str,
        resource_kind: ResourceKind | None = None,
        resource_id: str | None = None,
        marks_incomplete: bool = True,
    ) -> None:
        if marks_incomplete:
            self._incomplete = True
        if len(self._diagnostics) >= self._limits.max_diagnostics:
            self._diagnostics_truncated = True
            self._truncated = True
            return
        self._diagnostics.append(
            InventoryDiagnostic(
                code=code,
                message=message,
                resource_kind=resource_kind,
                resource_id=resource_id,
            )
        )

    def _bounded_source(self, items: list[Any]) -> list[Any]:
        budget = self._remaining_candidate_budget()
        if budget <= 0:
            if items:
                self._truncated = True
                self._incomplete = True
            return []
        if len(items) > budget:
            self._truncated = True
            self._incomplete = True
        return items[:budget]

    def _collect_containers(self) -> None:
        if self._remaining_candidate_budget() <= 0:
            return
        try:
            candidates = self._docker.containers.list(
                all=True,
                filters={"label": f"{LABEL_MANAGED}=true"},
            )
        except DockerException as exc:
            self._add_diagnostic(
                code="docker_container_list_failed",
                message=_safe_failure(exc),
                resource_kind=ResourceKind.WORKSPACE_CONTAINER,
            )
            return

        for container in self._bounded_source(candidates):
            if not self._begin_inspection():
                break
            resource_id = str(getattr(container, "id", "unknown"))
            try:
                container.reload()
                labels = dict(getattr(container, "labels", {}) or {})
                status = str(getattr(container, "status", "unknown"))
            except DockerException as exc:
                self._resources.append(
                    InventoryResource(
                        kind=ResourceKind.WORKSPACE_CONTAINER,
                        resource_id=resource_id,
                        lifecycle_state=ResourceLifecycleState.DEGRADED,
                        ownership=OwnershipProof(
                            OwnershipStatus.UNKNOWN,
                            (),
                            "container metadata inspection failed",
                        ),
                        bytes=ByteObservation.unavailable("container size not measured by O2"),
                    )
                )
                self._add_diagnostic(
                    code="docker_container_inspection_failed",
                    message=_safe_failure(exc),
                    resource_kind=ResourceKind.WORKSPACE_CONTAINER,
                    resource_id=resource_id,
                )
                continue

            proof = classify_workspace_container(labels)
            workspace_id = labels.get("dev-workspace-mcp.workspace-id")
            repository_id = labels.get(LABEL_REPOSITORY_ID)
            lifecycle = self._container_lifecycle(
                container_id=resource_id,
                workspace_id=workspace_id,
                repository_id=repository_id,
                proof=proof,
            )
            self._resources.append(
                InventoryResource(
                    kind=ResourceKind.WORKSPACE_CONTAINER,
                    resource_id=resource_id,
                    lifecycle_state=lifecycle,
                    ownership=proof,
                    bytes=ByteObservation.unavailable("container size not measured by O2"),
                    repository_id=repository_id,
                    workspace_id=workspace_id,
                    details={"status": status},
                )
            )

    def _container_lifecycle(
        self,
        *,
        container_id: str,
        workspace_id: str | None,
        repository_id: str | None,
        proof: OwnershipProof,
    ) -> ResourceLifecycleState:
        if proof.status is OwnershipStatus.FOREIGN:
            return ResourceLifecycleState.FOREIGN
        if proof.status is OwnershipStatus.UNKNOWN or workspace_id is None or repository_id is None:
            return ResourceLifecycleState.UNKNOWN
        try:
            row = self._connection.execute(
                "SELECT repository_id, container_id, state FROM workspace WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            self._add_diagnostic(
                code="workspace_state_lookup_failed",
                message=_safe_failure(exc),
                resource_kind=ResourceKind.WORKSPACE_CONTAINER,
                resource_id=container_id,
            )
            return ResourceLifecycleState.DEGRADED
        if row is None:
            return ResourceLifecycleState.ORPHANED_OWNED
        if row["repository_id"] != repository_id:
            return ResourceLifecycleState.FOREIGN
        if row["container_id"] != container_id:
            return ResourceLifecycleState.ORPHANED_OWNED
        if row["state"] in {"running", "starting", "creating", "rebuilding"}:
            return ResourceLifecycleState.ACTIVE
        return ResourceLifecycleState.RETAINED

    def _collect_volumes(self) -> None:
        if self._remaining_candidate_budget() <= 0:
            return
        try:
            candidates = self._docker.volumes.list(
                filters={"label": f"{LABEL_MANAGED}=true"},
            )
        except DockerException as exc:
            self._add_diagnostic(
                code="docker_volume_list_failed",
                message=_safe_failure(exc),
                resource_kind=ResourceKind.CACHE_VOLUME,
            )
            return

        for volume in self._bounded_source(candidates):
            if not self._begin_inspection():
                break
            resource_id = str(getattr(volume, "name", "unknown"))
            try:
                volume.reload()
                attrs = getattr(volume, "attrs", {}) or {}
                labels = dict(attrs.get("Labels") or {})
            except DockerException as exc:
                self._resources.append(
                    InventoryResource(
                        kind=ResourceKind.CACHE_VOLUME,
                        resource_id=resource_id,
                        lifecycle_state=ResourceLifecycleState.DEGRADED,
                        ownership=OwnershipProof(
                            OwnershipStatus.UNKNOWN,
                            (),
                            "volume metadata inspection failed",
                        ),
                        bytes=ByteObservation.unavailable("volume size unavailable"),
                    )
                )
                self._add_diagnostic(
                    code="docker_volume_inspection_failed",
                    message=_safe_failure(exc),
                    resource_kind=ResourceKind.CACHE_VOLUME,
                    resource_id=resource_id,
                )
                continue

            proof = classify_cache_volume(labels)
            repository_id = labels.get(LABEL_REPOSITORY_ID)
            lifecycle = self._cache_lifecycle(repository_id=repository_id, proof=proof)
            size = self._volume_size(attrs)
            self._resources.append(
                InventoryResource(
                    kind=ResourceKind.CACHE_VOLUME,
                    resource_id=resource_id,
                    lifecycle_state=lifecycle,
                    ownership=proof,
                    bytes=size,
                    repository_id=repository_id,
                )
            )

    def _cache_lifecycle(
        self,
        *,
        repository_id: str | None,
        proof: OwnershipProof,
    ) -> ResourceLifecycleState:
        if proof.status is OwnershipStatus.FOREIGN:
            return ResourceLifecycleState.FOREIGN
        if proof.status is OwnershipStatus.UNKNOWN or repository_id is None:
            return ResourceLifecycleState.UNKNOWN
        try:
            row = self._connection.execute(
                "SELECT 1 FROM repository WHERE id = ?",
                (repository_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            self._add_diagnostic(
                code="repository_state_lookup_failed",
                message=_safe_failure(exc),
                resource_kind=ResourceKind.CACHE_VOLUME,
            )
            return ResourceLifecycleState.DEGRADED
        return (
            ResourceLifecycleState.RETAINED
            if row is not None
            else ResourceLifecycleState.ORPHANED_OWNED
        )

    @staticmethod
    def _volume_size(attrs: Mapping[str, Any]) -> ByteObservation:
        usage = attrs.get("UsageData")
        if isinstance(usage, Mapping):
            size = usage.get("Size")
            if isinstance(size, int) and size >= 0:
                return ByteObservation.measured(size)
        return ByteObservation.unavailable(
            "bounded Docker volume metadata did not provide a size measurement"
        )

    def _bounded_rows(self, query: str) -> list[sqlite3.Row]:
        budget = self._remaining_candidate_budget()
        if budget <= 0:
            return []
        try:
            rows = self._connection.execute(query, (budget + 1,)).fetchall()
        except sqlite3.Error as exc:
            self._add_diagnostic(code="state_query_failed", message=_safe_failure(exc))
            return []
        if len(rows) > budget:
            self._truncated = True
            self._incomplete = True
        return rows[:budget]

    def _collect_job_logs(self) -> None:
        rows = self._bounded_rows(
            """
            SELECT id, workspace_id, state, stdout_total_bytes, stderr_total_bytes
            FROM job
            WHERE logs_pruned = 0
            ORDER BY id
            LIMIT ?
            """
        )
        for row in rows:
            if not self._begin_inspection():
                break
            lifecycle = (
                ResourceLifecycleState.ACTIVE
                if row["state"] in {"queued", "starting", "running"}
                else ResourceLifecycleState.RETAINED
            )
            self._resources.append(
                InventoryResource(
                    kind=ResourceKind.JOB_LOG_DIRECTORY,
                    resource_id=row["id"],
                    lifecycle_state=lifecycle,
                    ownership=_owned_state_proof(
                        "durable job row identifies the job id",
                        "log path is reconstructed beneath configured state_dir from validated id",
                    ),
                    bytes=ByteObservation.accounted(
                        int(row["stdout_total_bytes"]) + int(row["stderr_total_bytes"])
                    ),
                    workspace_id=row["workspace_id"],
                )
            )

    def _collect_cleanup_debt(self) -> None:
        rows = self._bounded_rows(
            """
            SELECT job_id, workspace_id, bytes_hint, last_error
            FROM job_log_cleanup
            ORDER BY created_at, job_id
            LIMIT ?
            """
        )
        for row in rows:
            if not self._begin_inspection():
                break
            self._resources.append(
                InventoryResource(
                    kind=ResourceKind.JOB_LOG_CLEANUP,
                    resource_id=row["job_id"],
                    lifecycle_state=ResourceLifecycleState.CLEANUP_PENDING,
                    ownership=_owned_state_proof("durable job_log_cleanup tombstone row"),
                    bytes=ByteObservation.accounted(int(row["bytes_hint"])),
                    workspace_id=row["workspace_id"],
                    details={"last_error_recorded": row["last_error"] is not None},
                )
            )

    def _collect_ssh_agents(self) -> None:
        rows = self._bounded_rows(
            """
            SELECT ssh_agent.workspace_id, ssh_agent.pid,
                   CASE WHEN workspace.id IS NULL THEN 0 ELSE 1 END AS workspace_present
            FROM ssh_agent
            LEFT JOIN workspace ON workspace.id = ssh_agent.workspace_id
            ORDER BY ssh_agent.workspace_id
            LIMIT ?
            """
        )
        for row in rows:
            if not self._begin_inspection():
                break
            lifecycle = (
                ResourceLifecycleState.ACTIVE
                if row["workspace_present"]
                else ResourceLifecycleState.ORPHANED_OWNED
            )
            self._resources.append(
                InventoryResource(
                    kind=ResourceKind.SSH_AGENT,
                    resource_id=row["workspace_id"],
                    lifecycle_state=lifecycle,
                    ownership=_owned_state_proof("durable ssh_agent row"),
                    bytes=ByteObservation.unavailable("ssh-agent byte size is not meaningful"),
                    workspace_id=row["workspace_id"],
                    details={"pid": int(row["pid"])},
                )
            )

    def _collect_state_storage(self) -> None:
        if not self._begin_inspection():
            return

        db_bytes: ByteObservation
        try:
            db_bytes = ByteObservation.measured(self._state_db_path.stat().st_size)
        except OSError as exc:
            db_bytes = ByteObservation.unavailable(_safe_failure(exc))
            self._add_diagnostic(
                code="state_db_size_unavailable",
                message=_safe_failure(exc),
                resource_kind=ResourceKind.STATE_STORAGE,
                resource_id="state-db",
            )

        capacity: ByteObservation
        free: ByteObservation
        try:
            stat = os.statvfs(self._state_db_path.parent)
            capacity = ByteObservation.measured(stat.f_frsize * stat.f_blocks)
            free = ByteObservation.measured(stat.f_frsize * stat.f_bavail)
        except OSError as exc:
            reason = _safe_failure(exc)
            capacity = ByteObservation.unavailable(reason)
            free = ByteObservation.unavailable(reason)
            self._add_diagnostic(
                code="state_filesystem_metrics_unavailable",
                message=reason,
                resource_kind=ResourceKind.STATE_STORAGE,
                resource_id="state-db",
            )

        self._resources.append(
            InventoryResource(
                kind=ResourceKind.STATE_STORAGE,
                resource_id="state-db",
                lifecycle_state=ResourceLifecycleState.ACTIVE,
                ownership=_owned_state_proof("configured state_db_path"),
                bytes=db_bytes,
                metrics={
                    "filesystem_capacity_bytes": capacity,
                    "filesystem_free_bytes": free,
                },
            )
        )
