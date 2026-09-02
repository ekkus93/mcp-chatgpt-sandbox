"""Read-only operational resource tooling (Post-MVP Operations O3).

The public MCP shape is intentionally narrower than the internal inventory
objects. It accepts only opaque repository/workspace IDs and serializes an
allowlisted metadata surface, so future internal diagnostic fields cannot
accidentally disclose arbitrary host paths.
"""

from __future__ import annotations

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
from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)

_MAX_RESOURCES = 256
_MAX_INSPECTIONS = 256
_MAX_DIAGNOSTICS = 64

OpaqueResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
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


class CleanupDebtSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "partial"]
    count: Annotated[int, Field(ge=0)]
    bytes: ByteObservationOutput


class InventoryScopeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: OpaqueResourceId | None = None
    workspace_id: OpaqueResourceId | None = None


class ResourceInventoryOutput(BaseModel):
    """Bounded public snapshot; no host path field exists in this model."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "degraded"]
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


def _byte_output(observation: ByteObservation) -> ByteObservationOutput:
    return ByteObservationOutput(
        value=observation.value,
        provenance=observation.provenance,
        reason=observation.reason,
    )


def _ownership_output(proof: OwnershipProof) -> OwnershipProofOutput:
    return OwnershipProofOutput(
        status=proof.status,
        evidence=list(proof.evidence),
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
        repository_id=repository_id,
        workspace_id=resource.workspace_id,
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
        resource_id=diagnostic.resource_id,
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


def _cleanup_debt_summary(
    resources: list[InventoryResourceOutput],
    *,
    complete: bool,
) -> CleanupDebtSummaryOutput:
    debt = [resource for resource in resources if resource.kind is ResourceKind.JOB_LOG_CLEANUP]
    accounted_bytes = sum(resource.bytes.value or 0 for resource in debt)
    return CleanupDebtSummaryOutput(
        status="complete" if complete else "partial",
        count=len(debt),
        bytes=ByteObservationOutput(
            value=accounted_bytes,
            provenance=ByteProvenance.ACCOUNTED,
            reason=None,
        ),
    )


def _result_output(
    result: ResourceInventoryResult,
    *,
    db: sqlite3.Connection,
    scope: _Scope,
) -> ResourceInventoryOutput:
    workspace_repositories = _workspace_repository_map(db, result.resources)
    resources: list[InventoryResourceOutput] = []
    for resource in result.resources:
        repository_id = _resource_repository_id(resource, workspace_repositories)
        if _matches_scope(resource, repository_id=repository_id, scope=scope):
            resources.append(_resource_output(resource, repository_id=repository_id))

    incomplete = result.incomplete
    cleanup_debt = _cleanup_debt_summary(resources, complete=not incomplete)
    return ResourceInventoryOutput(
        status="degraded" if incomplete else "complete",
        scope=InventoryScopeOutput(
            repository_id=scope.repository_id,
            workspace_id=scope.workspace_id,
        ),
        resources=resources,
        diagnostics=[_diagnostic_output(item) for item in result.diagnostics],
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
    """Register O3's bounded read-only operational inventory tool."""

    @server.tool(
        name="resource_inventory",
        description=(
            "Bounded read-only inventory of MCP-owned operational resources, "
            "cleanup debt, and degraded/unavailable observations (PMOR-001..003)."
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
