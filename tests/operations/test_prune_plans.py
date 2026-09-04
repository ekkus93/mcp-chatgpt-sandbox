"""Post-MVP Operations O6 / PMOAT-007 immutable/stale-plan acceptance."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
from types import ModuleType

import pytest

from dev_workspace_mcp.operations.inventory import (
    ByteObservation,
    InventoryResource,
    ResourceInventoryResult,
)
from dev_workspace_mcp.operations.prune_candidates import (
    PruneCandidate,
    build_prune_candidates,
)
from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)


def _planning_module() -> ModuleType:
    module_name = "dev_workspace_mcp.operations.prune_plans"
    assert importlib.util.find_spec(module_name) is not None, (
        "PMOAT-007 RED: O6 has no immutable/stale-aware prune-plan implementation yet"
    )
    return importlib.import_module(module_name)


def _proof(status: OwnershipStatus = OwnershipStatus.OWNED) -> OwnershipProof:
    return OwnershipProof(
        status=status,
        evidence=("positive-test-evidence",),
        reason="test ownership proof",
    )


def _resource(
    kind: ResourceKind,
    resource_id: str,
    lifecycle_state: ResourceLifecycleState,
    *,
    ownership: OwnershipStatus = OwnershipStatus.OWNED,
    repository_id: str | None = None,
    workspace_id: str | None = None,
    details: Mapping[str, str | int | bool | None] | None = None,
) -> InventoryResource:
    return InventoryResource(
        kind=kind,
        resource_id=resource_id,
        lifecycle_state=lifecycle_state,
        ownership=_proof(ownership),
        bytes=ByteObservation.unavailable("test unavailable"),
        repository_id=repository_id,
        workspace_id=workspace_id,
        details=details or {},
    )


def _inventory(
    *resources: InventoryResource,
    incomplete: bool = False,
    truncated: bool = False,
) -> ResourceInventoryResult:
    return ResourceInventoryResult(
        resources=tuple(resources),
        diagnostics=(),
        inspected_count=len(resources),
        result_limit=256,
        inspection_limit=256,
        truncated=truncated,
        incomplete=incomplete,
        diagnostics_truncated=False,
    )


def _container_candidate(
    *,
    resource_id: str = "container-a",
    status: str = "exited",
    repository_id: str = "repo-a",
    workspace_id: str = "workspace-a",
) -> PruneCandidate:
    candidates = build_prune_candidates(
        _inventory(
            _resource(
                ResourceKind.WORKSPACE_CONTAINER,
                resource_id,
                ResourceLifecycleState.ORPHANED_OWNED,
                repository_id=repository_id,
                workspace_id=workspace_id,
                details={"status": status},
            )
        )
    )
    assert len(candidates) == 1
    return candidates[0]


def _cache_candidate(
    *,
    resource_id: str = "cache-a",
    repository_id: str = "repo-a",
) -> PruneCandidate:
    candidates = build_prune_candidates(
        _inventory(
            _resource(
                ResourceKind.CACHE_VOLUME,
                resource_id,
                ResourceLifecycleState.ORPHANED_OWNED,
                repository_id=repository_id,
            )
        )
    )
    assert len(candidates) == 1
    return candidates[0]


def test_o6_plan_token_binds_exact_candidates_and_material_preconditions() -> None:
    module = _planning_module()
    create_prune_plan = module.create_prune_plan

    exited = _container_candidate(status="exited")
    same_again = _container_candidate(status="exited")
    dead = _container_candidate(status="dead")
    with_extra = (exited, _cache_candidate(resource_id="cache-extra"))

    first = create_prune_plan((exited,))
    second = create_prune_plan((same_again,))
    changed_precondition = create_prune_plan((dead,))
    expanded = create_prune_plan(with_extra)

    assert first == second
    assert first.token.startswith("pp_")
    assert len(first.token) == 67
    assert first.candidates == (exited,)
    assert changed_precondition.token != first.token
    assert expanded.token != first.token


def test_o6_plan_store_is_bounded_ephemeral_and_restart_forgets_tokens() -> None:
    module = _planning_module()
    PlanStore = module.PrunePlanStore
    PlanNotFoundError = module.PlanNotFoundError

    first_store = PlanStore(max_plans=2)
    first = first_store.put(module.create_prune_plan((_cache_candidate(resource_id="cache-1"),)))
    second = first_store.put(module.create_prune_plan((_cache_candidate(resource_id="cache-2"),)))
    third = first_store.put(module.create_prune_plan((_cache_candidate(resource_id="cache-3"),)))

    with pytest.raises(PlanNotFoundError):
        first_store.get(first.token)
    assert first_store.get(second.token) == second
    assert first_store.get(third.token) == third

    restarted = PlanStore(max_plans=2)
    with pytest.raises(PlanNotFoundError):
        restarted.get(third.token)


def test_o6_revalidation_never_expands_old_plan_with_newly_eligible_resource() -> None:
    module = _planning_module()
    plan = module.create_prune_plan((_cache_candidate(resource_id="cache-planned"),))
    fresh = _inventory(
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-planned",
            ResourceLifecycleState.ORPHANED_OWNED,
            repository_id="repo-a",
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-new",
            ResourceLifecycleState.ORPHANED_OWNED,
            repository_id="repo-a",
        ),
    )

    result = module.revalidate_prune_plan(plan, fresh)

    assert [decision.candidate.resource_id for decision in result.decisions] == ["cache-planned"]
    assert result.valid_candidates == plan.candidates
    assert result.stale_candidates == ()


def test_o6_candidate_becoming_active_is_stale_while_independent_candidate_remains_valid() -> None:
    module = _planning_module()
    container = _container_candidate(resource_id="container-race")
    cache = _cache_candidate(resource_id="cache-still-valid")
    plan = module.create_prune_plan((container, cache))
    fresh = _inventory(
        _resource(
            ResourceKind.WORKSPACE_CONTAINER,
            "container-race",
            ResourceLifecycleState.ACTIVE,
            repository_id="repo-a",
            workspace_id="workspace-a",
            details={"status": "running"},
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-still-valid",
            ResourceLifecycleState.ORPHANED_OWNED,
            repository_id="repo-a",
        ),
    )

    result = module.revalidate_prune_plan(plan, fresh)
    by_id = {decision.candidate.resource_id: decision for decision in result.decisions}

    assert by_id["container-race"].status.value == "stale"
    assert by_id["container-race"].reason == "lifecycle_changed"
    assert by_id["cache-still-valid"].status.value == "valid"
    assert result.mixed is True


@pytest.mark.parametrize(
    ("fresh_resource", "reason"),
    [
        (
            _resource(
                ResourceKind.CACHE_VOLUME,
                "cache-a",
                ResourceLifecycleState.ORPHANED_OWNED,
                ownership=OwnershipStatus.FOREIGN,
                repository_id="repo-a",
            ),
            "ownership_changed",
        ),
        (
            _resource(
                ResourceKind.CACHE_VOLUME,
                "cache-a",
                ResourceLifecycleState.ORPHANED_OWNED,
                ownership=OwnershipStatus.UNKNOWN,
                repository_id="repo-a",
            ),
            "ownership_unprovable",
        ),
        (
            _resource(
                ResourceKind.CACHE_VOLUME,
                "cache-a",
                ResourceLifecycleState.ORPHANED_OWNED,
                repository_id="repo-b",
            ),
            "scope_changed",
        ),
    ],
)
def test_o6_identity_or_ownership_change_fails_closed(
    fresh_resource: InventoryResource,
    reason: str,
) -> None:
    module = _planning_module()
    plan = module.create_prune_plan((_cache_candidate(),))

    result = module.revalidate_prune_plan(plan, _inventory(fresh_resource))

    assert len(result.decisions) == 1
    assert result.decisions[0].status.value == "stale"
    assert result.decisions[0].reason == reason
    assert result.valid_candidates == ()


def test_o6_material_precondition_change_fails_closed() -> None:
    module = _planning_module()
    plan = module.create_prune_plan((_container_candidate(status="exited"),))
    fresh = _inventory(
        _resource(
            ResourceKind.WORKSPACE_CONTAINER,
            "container-a",
            ResourceLifecycleState.ORPHANED_OWNED,
            repository_id="repo-a",
            workspace_id="workspace-a",
            details={"status": "dead"},
        )
    )

    result = module.revalidate_prune_plan(plan, fresh)

    assert result.decisions[0].status.value == "stale"
    assert result.decisions[0].reason == "precondition_changed"


def test_o6_duplicate_fresh_identity_is_unprovable_and_fails_closed() -> None:
    module = _planning_module()
    plan = module.create_prune_plan((_cache_candidate(),))
    duplicate = _resource(
        ResourceKind.CACHE_VOLUME,
        "cache-a",
        ResourceLifecycleState.ORPHANED_OWNED,
        repository_id="repo-a",
    )

    result = module.revalidate_prune_plan(plan, _inventory(duplicate, duplicate))

    assert result.decisions[0].status.value == "stale"
    assert result.decisions[0].reason == "identity_ambiguous"


def test_o6_missing_candidate_requires_complete_inventory_before_resource_absent() -> None:
    module = _planning_module()
    plan = module.create_prune_plan((_cache_candidate(),))

    complete = module.revalidate_prune_plan(plan, _inventory())
    incomplete = module.revalidate_prune_plan(plan, _inventory(incomplete=True))
    truncated = module.revalidate_prune_plan(plan, _inventory(truncated=True))

    assert complete.decisions[0].status.value == "stale"
    assert complete.decisions[0].reason == "resource_absent"
    assert incomplete.decisions[0].status.value == "stale"
    assert incomplete.decisions[0].reason == "inventory_incomplete"
    assert truncated.decisions[0].status.value == "stale"
    assert truncated.decisions[0].reason == "inventory_incomplete"
