"""Immutable, stale-aware prune planning for O6 / PMOR-006, PMOR-007.

Plans are deliberately ephemeral and bounded. A daemon restart forgets every
plan token; callers must request a new plan rather than allowing execution to
reconstruct an old authorization boundary from current inventory.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

from dev_workspace_mcp.operations.inventory import (
    InventoryResource,
    ResourceInventoryResult,
)
from dev_workspace_mcp.operations.prune_candidates import (
    PruneCandidate,
    build_prune_candidates,
)
from dev_workspace_mcp.operations.resources import OwnershipStatus, ResourceKind

MAX_STORED_PRUNE_PLANS = 256


class RevalidationStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class PrunePlan:
    token: str
    candidates: tuple[PruneCandidate, ...]


@dataclass(frozen=True, slots=True)
class PrunePlanDecision:
    candidate: PruneCandidate
    status: RevalidationStatus
    reason: str | None


@dataclass(frozen=True, slots=True)
class PrunePlanRevalidation:
    decisions: tuple[PrunePlanDecision, ...]
    valid_candidates: tuple[PruneCandidate, ...]
    stale_candidates: tuple[PruneCandidate, ...]
    mixed: bool


class PlanNotFoundError(KeyError):
    """Raised when an ephemeral prune-plan token is unknown to this daemon."""


class PrunePlanStore:
    """Bounded in-memory plan store; restart semantics are intentionally forgetful."""

    def __init__(self, *, max_plans: int = 64) -> None:
        if not 1 <= max_plans <= MAX_STORED_PRUNE_PLANS:
            raise ValueError(f"max_plans must be between 1 and {MAX_STORED_PRUNE_PLANS}")
        self._max_plans = max_plans
        self._plans: OrderedDict[str, PrunePlan] = OrderedDict()

    def put(self, plan: PrunePlan) -> PrunePlan:
        self._plans[plan.token] = plan
        self._plans.move_to_end(plan.token)
        while len(self._plans) > self._max_plans:
            self._plans.popitem(last=False)
        return plan

    def get(self, token: str) -> PrunePlan:
        try:
            return self._plans[token]
        except KeyError:
            raise PlanNotFoundError("unknown or expired prune plan token") from None


def _canonical_candidate(candidate: PruneCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "kind": candidate.kind.value,
        "resource_id": candidate.resource_id,
        "reason": candidate.reason,
        "ownership": {
            "status": candidate.ownership.status.value,
            "evidence": list(candidate.ownership.evidence),
            "reason": candidate.ownership.reason,
        },
        "required_lifecycle_state": candidate.required_lifecycle_state.value,
        "action": candidate.action,
        "bytes": {
            "value": candidate.bytes.value,
            "provenance": candidate.bytes.provenance.value,
            "reason": candidate.bytes.reason,
        },
        "repository_id": candidate.repository_id,
        "workspace_id": candidate.workspace_id,
        "preconditions": dict(sorted(candidate.preconditions.items())),
    }


def create_prune_plan(candidates: tuple[PruneCandidate, ...]) -> PrunePlan:
    """Freeze an exact candidate snapshot behind a deterministic opaque token."""

    ordered = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.kind.value,
                candidate.resource_id,
                candidate.candidate_id,
            ),
        )
    )
    identities = [(candidate.kind, candidate.resource_id) for candidate in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate prune plan candidate identity")

    payload = json.dumps(
        [_canonical_candidate(candidate) for candidate in ordered],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return PrunePlan(token=f"pp_{hashlib.sha256(payload).hexdigest()}", candidates=ordered)


def _index_inventory(
    inventory: ResourceInventoryResult,
) -> dict[tuple[ResourceKind, str], list[InventoryResource]]:
    indexed: dict[tuple[ResourceKind, str], list[InventoryResource]] = {}
    for resource in inventory.resources:
        indexed.setdefault((resource.kind, resource.resource_id), []).append(resource)
    return indexed


def _single_resource_inventory(resource: InventoryResource) -> ResourceInventoryResult:
    return ResourceInventoryResult(
        resources=(resource,),
        diagnostics=(),
        inspected_count=1,
        result_limit=1,
        inspection_limit=1,
        truncated=False,
        incomplete=False,
        diagnostics_truncated=False,
    )


def _stale(candidate: PruneCandidate, reason: str) -> PrunePlanDecision:
    return PrunePlanDecision(
        candidate=candidate,
        status=RevalidationStatus.STALE,
        reason=reason,
    )


def _revalidate_candidate(
    candidate: PruneCandidate,
    matches: list[InventoryResource],
    *,
    inventory_incomplete: bool,
) -> PrunePlanDecision:
    if not matches:
        reason = "inventory_incomplete" if inventory_incomplete else "resource_absent"
        return _stale(candidate, reason)
    if len(matches) != 1:
        return _stale(candidate, "identity_ambiguous")

    fresh = matches[0]
    if fresh.ownership.status is OwnershipStatus.UNKNOWN:
        return _stale(candidate, "ownership_unprovable")
    if fresh.ownership.status is not OwnershipStatus.OWNED:
        return _stale(candidate, "ownership_changed")
    if (
        fresh.repository_id != candidate.repository_id
        or fresh.workspace_id != candidate.workspace_id
    ):
        return _stale(candidate, "scope_changed")
    if fresh.lifecycle_state is not candidate.required_lifecycle_state:
        return _stale(candidate, "lifecycle_changed")
    if fresh.ownership != candidate.ownership:
        return _stale(candidate, "ownership_evidence_changed")

    rebuilt = build_prune_candidates(_single_resource_inventory(fresh))
    if len(rebuilt) != 1 or rebuilt[0].preconditions != candidate.preconditions:
        return _stale(candidate, "precondition_changed")

    return PrunePlanDecision(
        candidate=candidate,
        status=RevalidationStatus.VALID,
        reason=None,
    )


def revalidate_prune_plan(
    plan: PrunePlan,
    inventory: ResourceInventoryResult,
) -> PrunePlanRevalidation:
    """Revalidate only the frozen plan candidates; never discover or expand."""

    indexed = _index_inventory(inventory)
    inventory_incomplete = inventory.incomplete or inventory.truncated
    decisions = tuple(
        _revalidate_candidate(
            candidate,
            indexed.get((candidate.kind, candidate.resource_id), []),
            inventory_incomplete=inventory_incomplete,
        )
        for candidate in plan.candidates
    )
    valid = tuple(
        decision.candidate for decision in decisions if decision.status is RevalidationStatus.VALID
    )
    stale = tuple(
        decision.candidate for decision in decisions if decision.status is RevalidationStatus.STALE
    )
    return PrunePlanRevalidation(
        decisions=decisions,
        valid_candidates=valid,
        stale_candidates=stale,
        mixed=bool(valid and stale),
    )
