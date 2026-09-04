"""Deterministic, read-only prune-candidate construction for O5 / PMOR-006.

The builder consumes one already-collected ``ResourceInventoryResult`` snapshot.
It has no Docker, filesystem, process, or state mutation dependency. Candidate
construction is intentionally fail-closed: only explicitly supported resource
kinds in explicitly reclaimable lifecycle states with positive ownership proof
can be emitted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from dev_workspace_mcp.operations.inventory import (
    ByteObservation,
    InventoryResource,
    ResourceInventoryResult,
)
from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)

type PruneAction = str
type PreconditionValue = str | int | bool | None


@dataclass(frozen=True, slots=True)
class PruneCandidate:
    """One deterministic read-only description of a possible future reclaim."""

    candidate_id: str
    kind: ResourceKind
    resource_id: str
    reason: str
    ownership: OwnershipProof
    required_lifecycle_state: ResourceLifecycleState
    action: PruneAction
    bytes: ByteObservation
    repository_id: str | None
    workspace_id: str | None
    preconditions: Mapping[str, PreconditionValue]


@dataclass(frozen=True, slots=True)
class _Rule:
    lifecycle: ResourceLifecycleState
    action: PruneAction
    reason: str


_RULES: Mapping[ResourceKind, _Rule] = {
    ResourceKind.WORKSPACE_CONTAINER: _Rule(
        lifecycle=ResourceLifecycleState.ORPHANED_OWNED,
        action="remove_exact_container",
        reason="positively owned workspace container is orphaned from durable state",
    ),
    ResourceKind.CACHE_VOLUME: _Rule(
        lifecycle=ResourceLifecycleState.ORPHANED_OWNED,
        action="remove_exact_cache_volume",
        reason="positively owned cache volume is orphaned from durable repository state",
    ),
    ResourceKind.JOB_LOG_CLEANUP: _Rule(
        lifecycle=ResourceLifecycleState.CLEANUP_PENDING,
        action="retry_job_log_cleanup",
        reason="durable job-log cleanup debt is pending",
    ),
    ResourceKind.SSH_AGENT: _Rule(
        lifecycle=ResourceLifecycleState.ORPHANED_OWNED,
        action="terminate_exact_ssh_agent",
        reason="positively owned SSH agent is orphaned from durable workspace state",
    ),
}


def _candidate_id(kind: ResourceKind, resource_id: str) -> str:
    digest = hashlib.sha256(f"{kind.value}\0{resource_id}".encode()).hexdigest()[:32]
    return f"pc_{digest}"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _preconditions(resource: InventoryResource) -> Mapping[str, PreconditionValue] | None:
    if resource.kind is ResourceKind.WORKSPACE_CONTAINER:
        status = resource.details.get("status")
        # O5 describes reclamation but must not even nominate an observed
        # running/ambiguous container. Exact execution/revalidation comes later.
        if status not in {"exited", "dead"}:
            return None
        return MappingProxyType({"docker_status": status})

    if resource.kind is ResourceKind.SSH_AGENT:
        pid = _positive_int(resource.details.get("pid"))
        boot_id = resource.details.get("boot_id")
        process_start_time = _nonnegative_int(resource.details.get("process_start_time"))
        if pid is None or not isinstance(boot_id, str) or not boot_id or process_start_time is None:
            return None
        return MappingProxyType(
            {
                "pid": pid,
                "boot_id": boot_id,
                "process_start_time": process_start_time,
            }
        )

    return MappingProxyType({})


def _eligible(resource: InventoryResource, rule: _Rule) -> bool:
    return (
        resource.ownership.status is OwnershipStatus.OWNED
        and resource.lifecycle_state is rule.lifecycle
    )


def build_prune_candidates(inventory: ResourceInventoryResult) -> tuple[PruneCandidate, ...]:
    """Build deterministic O5 candidates from one immutable inventory snapshot."""

    seen: set[tuple[ResourceKind, str]] = set()
    candidates: list[PruneCandidate] = []

    for resource in inventory.resources:
        rule = _RULES.get(resource.kind)
        if rule is None:
            continue

        identity = (resource.kind, resource.resource_id)
        if identity in seen:
            raise ValueError("duplicate prune candidate identity")
        seen.add(identity)

        if not _eligible(resource, rule):
            continue

        preconditions = _preconditions(resource)
        if preconditions is None:
            continue

        candidates.append(
            PruneCandidate(
                candidate_id=_candidate_id(resource.kind, resource.resource_id),
                kind=resource.kind,
                resource_id=resource.resource_id,
                reason=rule.reason,
                ownership=resource.ownership,
                required_lifecycle_state=rule.lifecycle,
                action=rule.action,
                bytes=resource.bytes,
                repository_id=resource.repository_id,
                workspace_id=resource.workspace_id,
                preconditions=preconditions,
            )
        )

    candidates.sort(key=lambda candidate: (candidate.kind.value, candidate.resource_id))
    return tuple(candidates)
