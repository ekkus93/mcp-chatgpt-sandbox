"""Post-MVP Operations O5 / PMOAT-006 prune-candidate acceptance."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
from types import ModuleType

import pytest

from mcp_chatgpt_sandbox.operations.inventory import (
    ByteObservation,
    ByteProvenance,
    InventoryResource,
    ResourceInventoryResult,
)
from mcp_chatgpt_sandbox.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)


def _planning_module() -> ModuleType:
    module_name = "mcp_chatgpt_sandbox.operations.prune_candidates"
    assert importlib.util.find_spec(module_name) is not None, (
        "PMOAT-006 RED: O5 has no deterministic read-only prune-candidate builder yet"
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
    bytes_observation: ByteObservation | None = None,
    repository_id: str | None = None,
    workspace_id: str | None = None,
    details: Mapping[str, str | int | bool | None] | None = None,
) -> InventoryResource:
    return InventoryResource(
        kind=kind,
        resource_id=resource_id,
        lifecycle_state=lifecycle_state,
        ownership=_proof(ownership),
        bytes=bytes_observation or ByteObservation.unavailable("test unavailable"),
        repository_id=repository_id,
        workspace_id=workspace_id,
        details=details or {},
    )


def _inventory(*resources: InventoryResource) -> ResourceInventoryResult:
    return ResourceInventoryResult(
        resources=tuple(resources),
        diagnostics=(),
        inspected_count=len(resources),
        result_limit=256,
        inspection_limit=256,
        truncated=False,
        incomplete=False,
        diagnostics_truncated=False,
    )


def test_o5_only_explicit_owned_orphan_cleanup_states_become_candidates() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    resources = (
        _resource(
            ResourceKind.WORKSPACE_CONTAINER,
            "container-orphan",
            ResourceLifecycleState.ORPHANED_OWNED,
            workspace_id="workspace-orphan",
            repository_id="repo-a",
            details={"status": "exited"},
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-orphan",
            ResourceLifecycleState.ORPHANED_OWNED,
            repository_id="repo-a",
        ),
        _resource(
            ResourceKind.JOB_LOG_CLEANUP,
            "job-debt",
            ResourceLifecycleState.CLEANUP_PENDING,
            workspace_id="workspace-old",
            bytes_observation=ByteObservation.accounted(4096),
        ),
        _resource(
            ResourceKind.SSH_AGENT,
            "workspace-agent",
            ResourceLifecycleState.ORPHANED_OWNED,
            workspace_id="workspace-agent",
            details={
                "pid": 4242,
                "boot_id": "boot-opaque",
                "process_start_time": 123456,
            },
        ),
        _resource(
            ResourceKind.WORKSPACE_CONTAINER,
            "container-active",
            ResourceLifecycleState.ACTIVE,
            details={"status": "running"},
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-retained",
            ResourceLifecycleState.RETAINED,
        ),
        _resource(
            ResourceKind.JOB_LOG_DIRECTORY,
            "job-retained",
            ResourceLifecycleState.RETAINED,
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-foreign",
            ResourceLifecycleState.ORPHANED_OWNED,
            ownership=OwnershipStatus.FOREIGN,
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-unknown",
            ResourceLifecycleState.UNKNOWN,
            ownership=OwnershipStatus.UNKNOWN,
        ),
        _resource(
            ResourceKind.CACHE_VOLUME,
            "cache-degraded",
            ResourceLifecycleState.DEGRADED,
        ),
        _resource(
            ResourceKind.DOCKER_IMAGE,
            "image-orphan-looking",
            ResourceLifecycleState.ORPHANED_OWNED,
        ),
        _resource(
            ResourceKind.WORKSPACE_CONTAINER,
            "container-running-orphan",
            ResourceLifecycleState.ORPHANED_OWNED,
            details={"status": "running"},
        ),
    )

    result = build_prune_candidates(_inventory(*resources))

    assert {(candidate.kind, candidate.resource_id) for candidate in result} == {
        (ResourceKind.WORKSPACE_CONTAINER, "container-orphan"),
        (ResourceKind.CACHE_VOLUME, "cache-orphan"),
        (ResourceKind.JOB_LOG_CLEANUP, "job-debt"),
        (ResourceKind.SSH_AGENT, "workspace-agent"),
    }


def test_o5_candidates_are_deterministic_and_have_opaque_stable_ids() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    first = _resource(
        ResourceKind.CACHE_VOLUME,
        "cache-b",
        ResourceLifecycleState.ORPHANED_OWNED,
        repository_id="repo-b",
    )
    second = _resource(
        ResourceKind.WORKSPACE_CONTAINER,
        "container-a",
        ResourceLifecycleState.ORPHANED_OWNED,
        workspace_id="workspace-a",
        repository_id="repo-a",
        details={"status": "exited"},
    )

    forward = build_prune_candidates(_inventory(first, second))
    reverse = build_prune_candidates(_inventory(second, first))

    assert forward == reverse
    assert [candidate.candidate_id for candidate in forward] == [
        candidate.candidate_id for candidate in reverse
    ]
    assert all(
        candidate.candidate_id.startswith("pc_")
        and len(candidate.candidate_id) == 35
        and "/" not in candidate.candidate_id
        and "\\" not in candidate.candidate_id
        for candidate in forward
    )


def test_o5_candidate_records_reason_ownership_preconditions_and_exact_action() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    candidates = build_prune_candidates(
        _inventory(
            _resource(
                ResourceKind.WORKSPACE_CONTAINER,
                "container-orphan",
                ResourceLifecycleState.ORPHANED_OWNED,
                workspace_id="workspace-a",
                repository_id="repo-a",
                details={"status": "exited"},
            ),
            _resource(
                ResourceKind.JOB_LOG_CLEANUP,
                "job-cleanup",
                ResourceLifecycleState.CLEANUP_PENDING,
                workspace_id="workspace-a",
                bytes_observation=ByteObservation.accounted(512),
            ),
        )
    )
    by_kind = {candidate.kind: candidate for candidate in candidates}

    container = by_kind[ResourceKind.WORKSPACE_CONTAINER]
    assert container.reason == "positively owned workspace container is orphaned from durable state"
    assert container.ownership.status is OwnershipStatus.OWNED
    assert container.ownership.evidence == ("positive-test-evidence",)
    assert container.required_lifecycle_state is ResourceLifecycleState.ORPHANED_OWNED
    assert container.action == "remove_exact_container"
    assert container.workspace_id == "workspace-a"
    assert container.repository_id == "repo-a"

    debt = by_kind[ResourceKind.JOB_LOG_CLEANUP]
    assert debt.reason == "durable job-log cleanup debt is pending"
    assert debt.required_lifecycle_state is ResourceLifecycleState.CLEANUP_PENDING
    assert debt.action == "retry_job_log_cleanup"


def test_o5_preserves_byte_value_and_provenance_without_fabricating_zero() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    candidates = build_prune_candidates(
        _inventory(
            _resource(
                ResourceKind.CACHE_VOLUME,
                "cache-measured",
                ResourceLifecycleState.ORPHANED_OWNED,
                bytes_observation=ByteObservation.measured(9001),
            ),
            _resource(
                ResourceKind.WORKSPACE_CONTAINER,
                "container-unknown-size",
                ResourceLifecycleState.ORPHANED_OWNED,
                bytes_observation=ByteObservation.unavailable("not measured"),
                details={"status": "exited"},
            ),
        )
    )
    by_id = {candidate.resource_id: candidate for candidate in candidates}

    assert by_id["cache-measured"].bytes.value == 9001
    assert by_id["cache-measured"].bytes.provenance is ByteProvenance.MEASURED
    assert by_id["container-unknown-size"].bytes.value is None
    assert by_id["container-unknown-size"].bytes.provenance is ByteProvenance.UNAVAILABLE


def test_o5_candidate_builder_is_snapshot_only_and_does_not_mutate_input() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    resource = _resource(
        ResourceKind.CACHE_VOLUME,
        "cache-read-only",
        ResourceLifecycleState.ORPHANED_OWNED,
        details={"sentinel": "unchanged"},
    )
    inventory = _inventory(resource)
    before = inventory.resources

    first = build_prune_candidates(inventory)
    second = build_prune_candidates(inventory)

    assert inventory.resources == before
    assert resource.details == {"sentinel": "unchanged"}
    assert first == second


def test_o5_ssh_agent_requires_full_y2_process_identity() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    complete = _resource(
        ResourceKind.SSH_AGENT,
        "workspace-complete",
        ResourceLifecycleState.ORPHANED_OWNED,
        workspace_id="workspace-complete",
        details={"pid": 77, "boot_id": "boot-a", "process_start_time": 888},
    )
    pid_only = _resource(
        ResourceKind.SSH_AGENT,
        "workspace-pid-only",
        ResourceLifecycleState.ORPHANED_OWNED,
        workspace_id="workspace-pid-only",
        details={"pid": 78},
    )
    missing_start = _resource(
        ResourceKind.SSH_AGENT,
        "workspace-no-start",
        ResourceLifecycleState.ORPHANED_OWNED,
        workspace_id="workspace-no-start",
        details={"pid": 79, "boot_id": "boot-b"},
    )

    candidates = build_prune_candidates(_inventory(complete, pid_only, missing_start))

    assert [candidate.resource_id for candidate in candidates] == ["workspace-complete"]
    candidate = candidates[0]
    assert candidate.preconditions["pid"] == 77
    assert candidate.preconditions["boot_id"] == "boot-a"
    assert candidate.preconditions["process_start_time"] == 888


def test_o5_duplicate_candidate_identity_fails_closed() -> None:
    module = _planning_module()
    build_prune_candidates = module.build_prune_candidates

    duplicate_a = _resource(
        ResourceKind.CACHE_VOLUME,
        "cache-duplicate",
        ResourceLifecycleState.ORPHANED_OWNED,
    )
    duplicate_b = _resource(
        ResourceKind.CACHE_VOLUME,
        "cache-duplicate",
        ResourceLifecycleState.ORPHANED_OWNED,
    )

    with pytest.raises(ValueError, match="duplicate prune candidate identity"):
        build_prune_candidates(_inventory(duplicate_a, duplicate_b))
