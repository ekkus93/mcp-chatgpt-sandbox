"""Public plan-first reclamation tools for O7-O12 / PMOR-006..012.

``resource_prune_plan`` remains mutation-free and freezes the exact positive-
ownership candidates in a bounded ephemeral store. ``resource_prune_execute``
accepts only one token from that same store plus explicit confirmation. It has
no discovery/filter/path input and delegates candidate-by-candidate exact
revalidation and action to the plan-scoped execution service.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import docker
from docker.errors import DockerException
from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import AuditConfig
from dev_workspace_mcp.errors import InvalidArgumentError, translate_workspace_errors
from dev_workspace_mcp.operations.inventory import (
    ByteObservation,
    ByteProvenance,
    InventoryLimits,
    ResourceInventoryService,
)
from dev_workspace_mcp.operations.prune_candidates import PruneCandidate, build_prune_candidates
from dev_workspace_mcp.operations.prune_execution import (
    PruneExecutionResult,
    PruneExecutionService,
    PruneExecutionStatus,
)
from dev_workspace_mcp.operations.prune_plans import (
    PlanNotFoundError,
    PrunePlanStore,
    create_prune_plan,
)
from dev_workspace_mcp.workspace.naming import (
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
)

_MAX_RESOURCES = 256
_MAX_INSPECTIONS = 256
_MAX_DIAGNOSTICS = 64
_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_OPAQUE_ID_RE = re.compile(_OPAQUE_ID_PATTERN)
_DYNAMIC_OWNERSHIP_LABELS = frozenset(
    {LABEL_REPOSITORY_ID, LABEL_WORKSPACE_ID, LABEL_TEST_NAMESPACE}
)

_SAFE_AUDIT_PLAN_REASONS = frozenset(
    {
        "positively owned workspace container is orphaned from durable state",
        "positively owned cache volume is orphaned from durable repository state",
        "durable job-log cleanup debt is pending",
        "positively owned SSH agent is orphaned from durable workspace state",
        "durable SSH-agent socket cleanup debt is pending",
    }
)
_SAFE_AUDIT_FAILURE_CLASSIFICATIONS = frozenset(
    {
        "action_failed",
        "deletion_probe_failed",
        "deletion_unproven",
        "identity_changed",
        "identity_probe_failed",
        "secondary_cleanup_failed",
        "unsupported_action",
        "unsupported_candidate_kind",
    }
)

PlanResourceKind = Literal[
    "workspace_container",
    "cache_volume",
    "job_log_cleanup",
    "ssh_agent",
    "ssh_agent_cleanup",
]
PlanAction = Literal[
    "remove_exact_container",
    "remove_exact_cache_volume",
    "retry_job_log_cleanup",
    "terminate_exact_ssh_agent",
    "retry_ssh_agent_cleanup",
]
PlanLifecycleState = Literal["orphaned_owned", "cleanup_pending"]
ExecuteOutcomeStatus = Literal[
    "not_confirmed",
    "reclaimed",
    "already_absent",
    "stale",
    "failed",
]
ExecuteAggregateStatus = Literal["not_confirmed", "success", "mixed", "degraded"]
OpaqueId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=_OPAQUE_ID_PATTERN,
        description="Opaque daemon/resource identity; never a host filesystem path.",
    ),
]
PlanToken = Annotated[str, Field(pattern=r"^pp_[0-9a-f]{64}$", min_length=67, max_length=67)]
CandidateId = Annotated[str, Field(pattern=r"^pc_[0-9a-f]{32}$", min_length=35, max_length=35)]
BoundedText = Annotated[str, Field(max_length=512)]
ResourceKindFilter = Annotated[list[PlanResourceKind], Field(min_length=1, max_length=5)]


class ResourcePrunePlanInput(BaseModel):
    """Narrow, path-free O7 planning request."""

    model_config = ConfigDict(extra="forbid")

    repository_id: OpaqueId | None = None
    workspace_id: OpaqueId | None = None
    resource_kinds: ResourceKindFilter | None = None

    @field_validator("resource_kinds")
    @classmethod
    def _resource_kinds_unique(
        cls, value: list[PlanResourceKind] | None
    ) -> list[PlanResourceKind] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return value


class ResourcePruneExecuteInput(BaseModel):
    """O8 destructive request: exact stored plan token plus explicit consent."""

    model_config = ConfigDict(extra="forbid")

    plan_token: PlanToken
    confirm: bool = False


class ByteObservationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[int, Field(ge=0)] | None
    provenance: ByteProvenance
    reason: BoundedText | None = None


class CandidateOwnershipOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["owned"]
    evidence: Annotated[list[BoundedText], Field(max_length=8)]
    reason: BoundedText


class CandidatePreconditionsOutput(BaseModel):
    """Safe public summary; raw host/process identity remains plan-internal."""

    model_config = ConfigDict(extra="forbid")

    docker_status: Literal["exited", "dead"] | None = None
    ssh_agent_pid: Annotated[int, Field(ge=1)] | None = None
    ssh_agent_boot_id_bound: bool
    ssh_agent_process_start_time_bound: bool
    ssh_agent_cleanup_identity_bound: bool


class PruneCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: CandidateId
    kind: PlanResourceKind
    resource_id: OpaqueId
    reason: BoundedText
    ownership: CandidateOwnershipOutput
    required_lifecycle_state: PlanLifecycleState
    action: PlanAction
    bytes: ByteObservationOutput
    preconditions: CandidatePreconditionsOutput


class ProjectedBytesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_bytes: Annotated[int, Field(ge=0)]
    unknown_candidate_count: Annotated[int, Field(ge=0)]
    complete: bool


class ResourcePrunePlanOutput(BaseModel):
    """Immutable dry-run response; there is no execute field or mode switch."""

    model_config = ConfigDict(extra="forbid")

    dry_run: Literal[True]
    plan_token: PlanToken
    candidate_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    candidates: Annotated[list[PruneCandidateOutput], Field(max_length=_MAX_RESOURCES)]
    projected_bytes: ProjectedBytesOutput


class PruneExecutionOutcomeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: CandidateId
    kind: PlanResourceKind
    status: ExecuteOutcomeStatus
    reason: BoundedText | None = None


class ResourcePruneExecuteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    executed: bool
    status: ExecuteAggregateStatus
    candidate_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    reclaimed_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    already_absent_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    stale_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    failed_count: Annotated[int, Field(ge=0, le=_MAX_RESOURCES)]
    outcomes: Annotated[list[PruneExecutionOutcomeOutput], Field(max_length=_MAX_RESOURCES)]


@dataclass(frozen=True, slots=True)
class _Scope:
    repository_id: str | None
    workspace_id: str | None
    resource_kinds: frozenset[str] | None


class _DockerCollectionAdapter:
    """Expose only the read-side collection call consumed by inventory."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def list(self, *args: object, **kwargs: object) -> list[Any]:
        return list(self._collection.list(*args, **kwargs))


class _DockerInventoryAdapter:
    """Narrow Docker SDK access so planning cannot call mutation methods."""

    def __init__(self, client: docker.DockerClient) -> None:
        self.containers = _DockerCollectionAdapter(client.containers)
        self.volumes = _DockerCollectionAdapter(client.volumes)


class _UnavailableCollection:
    def list(self, *_args: object, **_kwargs: object) -> list[Any]:
        raise DockerException("Docker daemon unavailable")


class _UnavailableDocker:
    containers = _UnavailableCollection()
    volumes = _UnavailableCollection()


def _public_evidence(value: str) -> str:
    key, separator, _raw_value = value.partition("=")
    if separator and key in _DYNAMIC_OWNERSHIP_LABELS:
        return f"{key}=<opaque-id>"
    return value


def _public_opaque_id(value: str) -> str:
    if len(value) > 512 or _OPAQUE_ID_RE.fullmatch(value) is None:
        raise ValueError("internal prune candidate identity is not public-safe")
    return value


def _byte_output(observation: ByteObservation) -> ByteObservationOutput:
    return ByteObservationOutput(
        value=observation.value,
        provenance=observation.provenance,
        reason=observation.reason,
    )


def _preconditions_output(candidate: PruneCandidate) -> CandidatePreconditionsOutput:
    docker_status_raw = candidate.preconditions.get("docker_status")
    if docker_status_raw == "exited":
        docker_status: Literal["exited", "dead"] | None = "exited"
    elif docker_status_raw == "dead":
        docker_status = "dead"
    else:
        docker_status = None
    pid_raw = candidate.preconditions.get("pid")
    pid = (
        pid_raw
        if isinstance(pid_raw, int) and not isinstance(pid_raw, bool) and pid_raw > 0
        else None
    )
    boot_id = candidate.preconditions.get("boot_id")
    process_start_time = candidate.preconditions.get("process_start_time")
    socket_device = candidate.preconditions.get("socket_device")
    socket_inode = candidate.preconditions.get("socket_inode")
    cleanup_identity_bound = (
        isinstance(socket_device, int)
        and not isinstance(socket_device, bool)
        and socket_device >= 0
        and isinstance(socket_inode, int)
        and not isinstance(socket_inode, bool)
        and socket_inode >= 0
    )
    return CandidatePreconditionsOutput(
        docker_status=docker_status,
        ssh_agent_pid=pid,
        ssh_agent_boot_id_bound=isinstance(boot_id, str) and bool(boot_id),
        ssh_agent_process_start_time_bound=(
            isinstance(process_start_time, int)
            and not isinstance(process_start_time, bool)
            and process_start_time >= 0
        ),
        ssh_agent_cleanup_identity_bound=cleanup_identity_bound,
    )


def _candidate_output(candidate: PruneCandidate) -> PruneCandidateOutput:
    return PruneCandidateOutput(
        candidate_id=candidate.candidate_id,
        kind=cast(PlanResourceKind, candidate.kind.value),
        resource_id=_public_opaque_id(candidate.resource_id),
        reason=candidate.reason,
        ownership=CandidateOwnershipOutput(
            status="owned",
            evidence=[_public_evidence(value) for value in candidate.ownership.evidence],
            reason=candidate.ownership.reason,
        ),
        required_lifecycle_state=cast(PlanLifecycleState, candidate.required_lifecycle_state.value),
        action=cast(PlanAction, candidate.action),
        bytes=_byte_output(candidate.bytes),
        preconditions=_preconditions_output(candidate),
    )


def _workspace_repository_map(
    db: sqlite3.Connection,
    candidates: tuple[PruneCandidate, ...],
) -> dict[str, str]:
    workspace_ids = sorted(
        {
            candidate.workspace_id
            for candidate in candidates
            if candidate.workspace_id is not None and candidate.repository_id is None
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


def _candidate_repository_id(
    candidate: PruneCandidate,
    workspace_repositories: dict[str, str],
) -> str | None:
    if candidate.repository_id is not None:
        return candidate.repository_id
    if candidate.workspace_id is not None:
        return workspace_repositories.get(candidate.workspace_id)
    return None


def _matches_scope(
    candidate: PruneCandidate,
    *,
    repository_id: str | None,
    scope: _Scope,
) -> bool:
    if scope.resource_kinds is not None and candidate.kind.value not in scope.resource_kinds:
        return False
    if scope.repository_id is not None and repository_id != scope.repository_id:
        return False
    return scope.workspace_id is None or candidate.workspace_id == scope.workspace_id


def _scoped_candidates(
    db: sqlite3.Connection,
    candidates: tuple[PruneCandidate, ...],
    scope: _Scope,
) -> tuple[PruneCandidate, ...]:
    workspace_repositories = _workspace_repository_map(db, candidates)
    return tuple(
        candidate
        for candidate in candidates
        if _matches_scope(
            candidate,
            repository_id=_candidate_repository_id(candidate, workspace_repositories),
            scope=scope,
        )
    )


def _projected_bytes(candidates: tuple[PruneCandidate, ...]) -> ProjectedBytesOutput:
    values = [candidate.bytes.value for candidate in candidates]
    unknown = sum(value is None for value in values)
    return ProjectedBytesOutput(
        known_bytes=sum(value for value in values if value is not None),
        unknown_candidate_count=unknown,
        complete=unknown == 0,
    )



def _audit_plan_reason(candidate: PruneCandidate) -> str:
    if candidate.reason in _SAFE_AUDIT_PLAN_REASONS:
        return candidate.reason
    return "eligible_reclamation_candidate"


def _audit_failure_classification(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason in _SAFE_AUDIT_FAILURE_CLASSIFICATIONS:
        return reason
    return "failure_detail_redacted"


def _audit_reclaimed_bytes(result: PruneExecutionResult) -> dict[str, int]:
    values = [
        outcome.candidate.bytes.value
        for outcome in result.outcomes
        if outcome.status is PruneExecutionStatus.RECLAIMED
    ]
    return {
        "known_bytes": sum(value for value in values if value is not None),
        "unknown_candidate_count": sum(value is None for value in values),
    }


def _audit_execution_candidates(result: PruneExecutionResult) -> list[dict[str, object]]:
    decision = "executed" if result.confirmed else "not_executed"
    problem_statuses = {PruneExecutionStatus.STALE, PruneExecutionStatus.FAILED}
    return [
        {
            "candidate_id": outcome.candidate.candidate_id,
            "kind": outcome.candidate.kind.value,
            "decision": decision,
            "outcome": outcome.status.value,
            "bytes_reclaimed": (
                outcome.candidate.bytes.value
                if outcome.status is PruneExecutionStatus.RECLAIMED
                else None
            ),
            "failure_classification": (
                _audit_failure_classification(outcome.reason)
                if outcome.status in problem_statuses
                else None
            ),
        }
        for outcome in result.outcomes
    ]


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    state_db_path: Path,
    audit_config: AuditConfig,
) -> None:
    """Register plan creation and explicit plan-scoped execution."""

    plan_store = PrunePlanStore()

    @server.tool(
        name="resource_prune_plan",
        description=(
            "Create an immutable dry-run resource reclamation plan from positively owned "
            "eligible resources. Planning never deletes, stops, kills, prunes, or executes."
        ),
    )
    @translate_workspace_errors
    async def resource_prune_plan(
        repository_id: OpaqueId | None = None,
        workspace_id: OpaqueId | None = None,
        resource_kinds: ResourceKindFilter | None = None,
    ) -> ResourcePrunePlanOutput:
        request = ResourcePrunePlanInput(
            repository_id=repository_id,
            workspace_id=workspace_id,
            resource_kinds=resource_kinds,
        )
        inventory_docker: Any = (
            _DockerInventoryAdapter(docker_client)
            if docker_client is not None
            else _UnavailableDocker()
        )
        inventory = ResourceInventoryService(
            connection=db,
            docker_client=inventory_docker,
            state_db_path=state_db_path,
            limits=InventoryLimits(
                max_resources=_MAX_RESOURCES,
                max_inspections=_MAX_INSPECTIONS,
                max_diagnostics=_MAX_DIAGNOSTICS,
            ),
        ).collect()
        candidates = build_prune_candidates(inventory)
        scope = _Scope(
            repository_id=request.repository_id,
            workspace_id=request.workspace_id,
            resource_kinds=(
                frozenset(request.resource_kinds) if request.resource_kinds is not None else None
            ),
        )
        selected = _scoped_candidates(db, candidates, scope)
        plan = plan_store.put(create_prune_plan(selected))
        output_candidates = [_candidate_output(candidate) for candidate in plan.candidates]
        output = ResourcePrunePlanOutput(
            dry_run=True,
            plan_token=plan.token,
            candidate_count=len(output_candidates),
            candidates=output_candidates,
            projected_bytes=_projected_bytes(plan.candidates),
        )
        write_audit_event(
            audit_config,
            operation="resource_prune_plan",
            result="success",
            repository_id=request.repository_id,
            workspace_id=request.workspace_id,
            metadata={
                "dry_run": True,
                "plan_id": plan.token,
                "candidate_count": len(plan.candidates),
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "kind": candidate.kind.value,
                        "reason": _audit_plan_reason(candidate),
                        "decision": "planned",
                    }
                    for candidate in plan.candidates
                ],
            },
        )
        return output

    @server.tool(
        name="resource_prune_execute",
        description=(
            "Execute only a previously returned immutable reclamation plan. Explicit "
            "confirmation is required; every exact candidate is independently revalidated."
        ),
    )
    @translate_workspace_errors
    async def resource_prune_execute(
        plan_token: PlanToken,
        confirm: bool = False,
    ) -> ResourcePruneExecuteOutput:
        request = ResourcePruneExecuteInput(plan_token=plan_token, confirm=confirm)
        try:
            plan = plan_store.get(request.plan_token)
        except PlanNotFoundError as exc:
            raise InvalidArgumentError("unknown or expired prune plan token") from exc

        result = PruneExecutionService(
            connection=db,
            docker_client=docker_client,
            state_dir=state_db_path.parent,
        ).execute(plan, confirmed=request.confirm)
        outputs = [
            PruneExecutionOutcomeOutput(
                candidate_id=outcome.candidate.candidate_id,
                kind=cast(PlanResourceKind, outcome.candidate.kind.value),
                status=outcome.status.value,
                reason=outcome.reason,
            )
            for outcome in result.outcomes
        ]
        output = ResourcePruneExecuteOutput(
            confirmed=result.confirmed,
            executed=result.confirmed,
            status=result.status.value,
            candidate_count=len(outputs),
            reclaimed_count=sum(
                outcome.status is PruneExecutionStatus.RECLAIMED for outcome in result.outcomes
            ),
            already_absent_count=sum(
                outcome.status is PruneExecutionStatus.ALREADY_ABSENT for outcome in result.outcomes
            ),
            stale_count=sum(
                outcome.status is PruneExecutionStatus.STALE for outcome in result.outcomes
            ),
            failed_count=sum(
                outcome.status is PruneExecutionStatus.FAILED for outcome in result.outcomes
            ),
            outcomes=outputs,
        )
        write_audit_event(
            audit_config,
            operation="resource_prune_execute",
            result=result.status.value,
            metadata={
                "plan_id": plan.token,
                "confirmed": result.confirmed,
                "status": result.status.value,
                "candidate_count": len(result.outcomes),
                "reclaimed_bytes": _audit_reclaimed_bytes(result),
                "candidates": _audit_execution_candidates(result),
            },
        )
        return output
