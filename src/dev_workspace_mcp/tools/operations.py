"""Read-only operational resource tooling (Post-MVP Operations O3/O9).

The public MCP shape is intentionally narrower than the internal inventory
objects. It accepts only opaque repository/workspace IDs and serializes an
allowlisted metadata surface, so future internal diagnostic fields cannot
accidentally disclose arbitrary host paths.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import docker
from docker.errors import DockerException
from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from dev_workspace_mcp.errors import translate_workspace_errors
from dev_workspace_mcp.operations.inventory import (
    ByteObservation,
    ByteProvenance,
    InventoryDiagnostic,
    InventoryLimits,
    InventoryResource,
    ResourceInventoryResult,
    ResourceInventoryService,
)
from dev_workspace_mcp.operations.pressure import PidPressureStatus
from dev_workspace_mcp.operations.pressure import observe_pid_pressure as observe_pid_pressure
from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)
from dev_workspace_mcp.workspace.naming import (
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
)

_MAX_RESOURCES = 256
_MAX_INSPECTIONS = 256
_MAX_DIAGNOSTICS = 64
_OPAQUE_RESOURCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_OPAQUE_RESOURCE_ID_RE = re.compile(_OPAQUE_RESOURCE_ID_PATTERN)
_DYNAMIC_OWNERSHIP_LABELS = frozenset(
    {LABEL_REPOSITORY_ID, LABEL_WORKSPACE_ID, LABEL_TEST_NAMESPACE}
)

OpaqueResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=_OPAQUE_RESOURCE_ID_PATTERN,
        description="Opaque resource identity; never a host filesystem path.",
    ),
]
InventoryLimit = Annotated[int, Field(ge=1, le=_MAX_RESOURCES)]
DiagnosticLimit = Annotated[int, Field(ge=1, le=_MAX_DIAGNOSTICS)]
BoundedText = Annotated[str, Field(max_length=512)]


class ResourceInventoryInput(BaseModel):
    """Bounded, path-free input contract for ``resource_inventory``."""

    model_config = ConfigDict(extra="forbid")

    repository_id: OpaqueResourceId | None = None
    workspace_id: OpaqueResourceId | None = None
    max_resources: InventoryLimit = _MAX_RESOURCES
    max_inspections: InventoryLimit = _MAX_INSPECTIONS
    max_diagnostics: DiagnosticLimit = _MAX_DIAGNOSTICS


class ByteObservationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[int, Field(ge=0)] | None
    provenance: ByteProvenance
    reason: BoundedText | None = None


class OwnershipProofOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OwnershipStatus
    evidence: Annotated[list[BoundedText], Field(max_length=8)]
    reason: BoundedText


class ResourceMetricsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filesystem_capacity_bytes: ByteObservationOutput | None = None
    filesystem_free_bytes: ByteObservationOutput | None = None


class ResourceMetadataOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docker_status: Annotated[str, Field(max_length=64)] | None = None
    ssh_agent_pid: Annotated[int, Field(ge=1)] | None = None
    last_error_recorded: bool | None = None


class InventoryResourceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ResourceKind
    resource_id: OpaqueResourceId
    lifecycle_state: ResourceLifecycleState
    ownership: OwnershipProofOutput
    bytes: ByteObservationOutput
    repository_id: OpaqueResourceId | None = None
    workspace_id: OpaqueResourceId | None = None
    metrics: ResourceMetricsOutput
    metadata: ResourceMetadataOutput


class InventoryDiagnosticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")]
    message: BoundedText
    resource_kind: ResourceKind | None = None
    resource_id: OpaqueResourceId | None = None


class InventoryScopeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: OpaqueResourceId | None = None
    workspace_id: OpaqueResourceId | None = None


class CleanupDebtSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "partial"]
    count: Annotated[int, Field(ge=0)]
    bytes: ByteObservationOutput


class PidPressureOutput(BaseModel):
    """Optional Linux cgroup-v2 PID pressure; unavailable is never zero."""

    model_config = ConfigDict(extra="forbid")

    status: PidPressureStatus
    source: Literal["cgroup_v2"] | None = None
    current: Annotated[int, Field(ge=0)] | None = None
    limit: Annotated[int, Field(ge=0)] | None = None
    unlimited: bool | None = None
    reason: BoundedText | None = None


class ResourceInventoryOutput(BaseModel):
    """Bounded public snapshot; no host path field exists in this model."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "degraded"]
    pid_pressure: PidPressureOutput
    scope: InventoryScopeOutput
    resources: Annotated[list[InventoryResourceOutput], Field(max_length=_MAX_RESOURCES)]
    diagnostics: Annotated[list[InventoryDiagnosticOutput], Field(max_length=_MAX_DIAGNOSTICS)]
    cleanup_debt: CleanupDebtSummaryOutput
    inspected_count: Annotated[int, Field(ge=0, le=_MAX_INSPECTIONS)]
    result_limit: InventoryLimit
    inspection_limit: InventoryLimit
    truncated: bool
    incomplete: bool
    diagnostics_truncated: bool


@dataclass(frozen=True, slots=True)
class _Scope:
    repository_id: str | None
    workspace_id: str | None

    @property
    def narrow(self) -> bool:
        return self.repository_id is not None or self.workspace_id is not None


class _DockerCollectionAdapter:
    """Expose only the read-side collection method O2 inventory consumes."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def list(self, *args: object, **kwargs: object) -> list[Any]:
        return list(self._collection.list(*args, **kwargs))


class _DockerInventoryAdapter:
    """Narrow a Docker SDK client to O2's mutation-free inventory protocol."""

    def __init__(self, client: docker.DockerClient) -> None:
        self.containers = _DockerCollectionAdapter(client.containers)
        self.volumes = _DockerCollectionAdapter(client.volumes)


class _UnavailableCollection:
    def list(self, *_args: object, **_kwargs: object) -> list[Any]:
        raise DockerException("Docker daemon unavailable")


class _UnavailableDocker:
    """Read-only adapter that lets O2 report Docker unavailability per source."""

    containers = _UnavailableCollection()
    volumes = _UnavailableCollection()


def _public_opaque_id(value: str | None) -> str | None:
    """Return only IDs that satisfy the public path-free opaque-ID contract."""
    if value is None or len(value) > 512 or _OPAQUE_RESOURCE_ID_RE.fullmatch(value) is None:
        return None
    return value


def _public_evidence(value: str) -> str:
    """Redact Docker-controlled dynamic label values at the MCP boundary."""
    key, separator, _raw_value = value.partition("=")
    if separator and key in _DYNAMIC_OWNERSHIP_LABELS:
        return f"{key}=<opaque-id>"
    return value


def _byte_output(observation: ByteObservation) -> ByteObservationOutput:
    return ByteObservationOutput(
        value=observation.value,
        provenance=observation.provenance,
        reason=observation.reason,
    )


def _ownership_output(proof: OwnershipProof) -> OwnershipProofOutput:
    return OwnershipProofOutput(
        status=proof.status,
        evidence=[_public_evidence(value) for value in proof.evidence],
        reason=proof.reason,
    )


def _resource_output(
    resource: InventoryResource,
    *,
    repository_id: str | None,
) -> InventoryResourceOutput:
    status = resource.details.get("status")
    safe_status = status if isinstance(status, str) and len(status) <= 64 else None
    pid = resource.details.get("pid")
    safe_pid = pid if isinstance(pid, int) and pid > 0 else None
    last_error_recorded = resource.details.get("last_error_recorded")
    safe_last_error = last_error_recorded if isinstance(last_error_recorded, bool) else None
    return InventoryResourceOutput(
        kind=resource.kind,
        resource_id=resource.resource_id,
        lifecycle_state=resource.lifecycle_state,
        ownership=_ownership_output(resource.ownership),
        bytes=_byte_output(resource.bytes),
        repository_id=_public_opaque_id(repository_id),
        workspace_id=_public_opaque_id(resource.workspace_id),
        metrics=ResourceMetricsOutput(
            filesystem_capacity_bytes=(
                _byte_output(resource.metrics["filesystem_capacity_bytes"])
                if "filesystem_capacity_bytes" in resource.metrics
                else None
            ),
            filesystem_free_bytes=(
                _byte_output(resource.metrics["filesystem_free_bytes"])
                if "filesystem_free_bytes" in resource.metrics
                else None
            ),
        ),
        metadata=ResourceMetadataOutput(
            docker_status=safe_status,
            ssh_agent_pid=safe_pid,
            last_error_recorded=safe_last_error,
        ),
    )


def _diagnostic_output(diagnostic: InventoryDiagnostic) -> InventoryDiagnosticOutput:
    return InventoryDiagnosticOutput(
        code=diagnostic.code,
        message=diagnostic.message,
        resource_kind=diagnostic.resource_kind,
        resource_id=_public_opaque_id(diagnostic.resource_id),
    )


def _workspace_repository_map(
    db: sqlite3.Connection,
    resources: tuple[InventoryResource, ...],
) -> dict[str, str]:
    workspace_ids = sorted(
        {
            resource.workspace_id
            for resource in resources
            if resource.workspace_id is not None and resource.repository_id is None
        }
    )
    if not workspace_ids:
        return {}
    placeholders = ", ".join("?" for _ in workspace_ids)
    rows = db.execute(
        f"SELECT id, repository_id FROM workspace WHERE id IN ({placeholders}) ORDER BY id",
        tuple(workspace_ids),
    ).fetchall()
    return {str(row["id"]): str(row["repository_id"]) for row in rows}


def _resource_repository_id(
    resource: InventoryResource,
    workspace_repositories: dict[str, str],
) -> str | None:
    if resource.repository_id is not None:
        return resource.repository_id
    if resource.workspace_id is not None:
        return workspace_repositories.get(resource.workspace_id)
    return None


def _matches_scope(
    resource: InventoryResource,
    *,
    repository_id: str | None,
    scope: _Scope,
) -> bool:
    if scope.repository_id is not None and repository_id != scope.repository_id:
        return False
    return scope.workspace_id is None or resource.workspace_id == scope.workspace_id


def _diagnostic_relevant(
    diagnostic: InventoryDiagnostic,
    *,
    scope: _Scope,
    scoped_resource_keys: set[tuple[ResourceKind, str]],
) -> bool:
    if not scope.narrow:
        return True
    if diagnostic.resource_id is not None:
        return (diagnostic.resource_kind, diagnostic.resource_id) in scoped_resource_keys
    # Diagnostics without a kind identify a shared durable-state source. Docker
    # list failures are also source-wide: if enumeration itself failed, we cannot
    # prove the requested repository/workspace had no matching Docker resources.
    if diagnostic.resource_kind is None:
        return True
    return diagnostic.code in {"docker_container_list_failed", "docker_volume_list_failed"}


def _cleanup_debt_summary(
    resources: list[InventoryResourceOutput],
    *,
    complete: bool,
) -> CleanupDebtSummaryOutput:
    cleanup_kinds = {ResourceKind.JOB_LOG_CLEANUP, ResourceKind.SSH_AGENT_CLEANUP}
    debt = [resource for resource in resources if resource.kind in cleanup_kinds]
    values = [resource.bytes.value for resource in debt]
    bytes_complete = all(value is not None for value in values)
    if bytes_complete:
        byte_observation = ByteObservationOutput(
            value=sum(value for value in values if value is not None),
            provenance=ByteProvenance.ACCOUNTED,
            reason=None,
        )
    else:
        byte_observation = ByteObservationOutput(
            value=None,
            provenance=ByteProvenance.UNAVAILABLE,
            reason="one or more cleanup-debt byte observations are unavailable",
        )
    return CleanupDebtSummaryOutput(
        status="complete" if complete and bytes_complete else "partial",
        count=len(debt),
        bytes=byte_observation,
    )


def _result_output(
    result: ResourceInventoryResult,
    *,
    db: sqlite3.Connection,
    scope: _Scope,
) -> ResourceInventoryOutput:
    workspace_repositories = _workspace_repository_map(db, result.resources)
    scoped_resources: list[tuple[InventoryResource, str | None]] = []
    for resource in result.resources:
        repository_id = _resource_repository_id(resource, workspace_repositories)
        if _matches_scope(resource, repository_id=repository_id, scope=scope):
            scoped_resources.append((resource, repository_id))

    resources = [
        _resource_output(resource, repository_id=repository_id)
        for resource, repository_id in scoped_resources
    ]
    scoped_resource_keys = {
        (resource.kind, resource.resource_id) for resource, _ in scoped_resources
    }
    relevant_diagnostics = [
        item
        for item in result.diagnostics
        if _diagnostic_relevant(item, scope=scope, scoped_resource_keys=scoped_resource_keys)
    ]

    scoped_degraded = any(
        resource.lifecycle_state is ResourceLifecycleState.DEGRADED
        for resource, _repository_id in scoped_resources
    )
    if scope.narrow:
        incomplete = (
            result.truncated
            or result.diagnostics_truncated
            or bool(relevant_diagnostics)
            or scoped_degraded
        )
    else:
        incomplete = result.incomplete
    cleanup_debt = _cleanup_debt_summary(resources, complete=not incomplete)
    pid_pressure = observe_pid_pressure()
    return ResourceInventoryOutput(
        status="degraded" if incomplete else "complete",
        pid_pressure=PidPressureOutput(
            status=pid_pressure.status,
            source=pid_pressure.source,
            current=pid_pressure.current,
            limit=pid_pressure.limit,
            unlimited=pid_pressure.unlimited,
            reason=pid_pressure.reason,
        ),
        scope=InventoryScopeOutput(
            repository_id=scope.repository_id,
            workspace_id=scope.workspace_id,
        ),
        resources=resources,
        diagnostics=[_diagnostic_output(item) for item in relevant_diagnostics],
        cleanup_debt=cleanup_debt,
        inspected_count=result.inspected_count,
        result_limit=result.result_limit,
        inspection_limit=result.inspection_limit,
        truncated=result.truncated,
        incomplete=incomplete,
        diagnostics_truncated=result.diagnostics_truncated,
    )


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    state_db_path: Path,
) -> None:
    """Register O3/O9's bounded read-only operational inventory tool."""

    @server.tool(
        name="resource_inventory",
        description=(
            "Bounded read-only inventory of MCP-owned operational resources, cleanup "
            "debt, degraded/unavailable observations, and optional cgroup-v2 PID "
            "pressure (PMOR-001..005, PMOR-010)."
        ),
    )
    @translate_workspace_errors
    async def resource_inventory(
        repository_id: OpaqueResourceId | None = None,
        workspace_id: OpaqueResourceId | None = None,
        max_resources: InventoryLimit = _MAX_RESOURCES,
        max_inspections: InventoryLimit = _MAX_INSPECTIONS,
        max_diagnostics: DiagnosticLimit = _MAX_DIAGNOSTICS,
    ) -> ResourceInventoryOutput:
        request = ResourceInventoryInput(
            repository_id=repository_id,
            workspace_id=workspace_id,
            max_resources=max_resources,
            max_inspections=max_inspections,
            max_diagnostics=max_diagnostics,
        )
        inventory_docker: Any = (
            _DockerInventoryAdapter(docker_client)
            if docker_client is not None
            else _UnavailableDocker()
        )
        result = ResourceInventoryService(
            connection=db,
            docker_client=inventory_docker,
            state_db_path=state_db_path,
            limits=InventoryLimits(
                max_resources=request.max_resources,
                max_inspections=request.max_inspections,
                max_diagnostics=request.max_diagnostics,
            ),
        ).collect()
        return _result_output(
            result,
            db=db,
            scope=_Scope(
                repository_id=request.repository_id,
                workspace_id=request.workspace_id,
            ),
        )
