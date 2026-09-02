"""Post-MVP Operations O1 / PMOAT-001 ownership-proof acceptance."""

from __future__ import annotations

import contextlib

import docker
import pytest

from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.operations.resources import (
    INVENTORY_ONLY_RESOURCE_KINDS,
    LABEL_RESOURCE_KIND,
    OWNERSHIP_RULES,
    RESOURCE_KIND_CACHE_VOLUME,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
    cache_volume_labels,
    classify_cache_volume,
    classify_workspace_container,
)
from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.caches import build_cache_mounts, resolve_caches
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
    current_test_resource_namespace,
)


def _cargo_cache(repository: RepositoryRecord):
    return resolve_caches(
        repository_id=repository.id,
        project_caches=[ProjectCacheEntry(name="cargo", path="$HOME/.cargo")],
    )[0]


def test_pmoat_001_daemon_created_cache_volume_has_positive_ownership_labels(
    docker_client: docker.DockerClient,
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
) -> None:
    """A cache name alone cannot be the ownership proof PMOR-001 requires."""
    cache = _cargo_cache(repository)
    record = manager.ensure_workspace(
        repository=repository,
        image="alpine:3.20",
        run_kwargs={
            "command": ["sleep", "3600"],
            "mounts": build_cache_mounts([cache]),
        },
    )
    assert record.container_id is not None

    volume = docker_client.volumes.get(cache.volume_name)
    volume.reload()
    labels = volume.attrs.get("Labels") or {}
    assert labels[LABEL_MANAGED] == "true"
    assert labels[LABEL_RESOURCE_KIND] == RESOURCE_KIND_CACHE_VOLUME
    assert labels[LABEL_REPOSITORY_ID] == repository.id
    namespace = current_test_resource_namespace()
    assert namespace is not None
    assert labels[LABEL_TEST_NAMESPACE] == namespace

    proof = classify_cache_volume(labels, expected_repository_id=repository.id)
    assert proof.status is OwnershipStatus.OWNED
    assert proof.positively_owned


def test_pmoat_001_foreign_cache_lookalike_has_no_ownership_authority(
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """A deliberately similar volume name remains foreign without proof labels."""
    cache = _cargo_cache(repository)
    foreign = docker_client.volumes.create(name=f"{cache.volume_name}-foreign")
    try:
        foreign.reload()
        proof = classify_cache_volume(foreign.attrs.get("Labels") or {})
        assert proof.status is OwnershipStatus.FOREIGN
        assert not proof.positively_owned
    finally:
        with contextlib.suppress(docker.errors.NotFound):
            foreign.remove(force=True)


def test_pmoat_001_exact_cache_name_collision_is_not_adopted_or_deleted(
    docker_client: docker.DockerClient,
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
) -> None:
    """Even the exact deterministic cache name is not ownership evidence."""
    cache = _cargo_cache(repository)
    foreign = docker_client.volumes.create(name=cache.volume_name)
    try:
        with pytest.raises(ContainerCreateFailedError, match="ownership"):
            manager.ensure_workspace(
                repository=repository,
                image="alpine:3.20",
                run_kwargs={
                    "command": ["sleep", "3600"],
                    "mounts": build_cache_mounts([cache]),
                },
            )

        # The existing workspace-delete cleanup hook is also forbidden from
        # turning the matching name into deletion authority.
        manager._remove_cache_volumes([cache.volume_name])
        foreign.reload()
        assert docker_client.volumes.get(foreign.name).name == foreign.name
    finally:
        with contextlib.suppress(docker.errors.NotFound):
            foreign.remove(force=True)


def test_pmoat_001_workspace_delete_requires_cache_repository_identity(
    docker_client: docker.DockerClient,
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
) -> None:
    """Managed/kind/namespace labels do not authorize cross-repository deletion."""
    record = manager.ensure_workspace(
        repository=repository,
        image="alpine:3.20",
        run_kwargs={"command": ["sleep", "3600"]},
    )
    cache = _cargo_cache(repository)
    foreign = docker_client.volumes.create(
        name=cache.volume_name,
        labels=cache_volume_labels(repository_id="other-repository"),
    )
    try:
        manager.delete(record.id, cache_volume_names=[cache.volume_name])

        foreign.reload()
        assert docker_client.volumes.get(foreign.name).name == foreign.name
    finally:
        with contextlib.suppress(docker.errors.NotFound):
            foreign.remove(force=True)


def test_pmoat_001_workspace_classifier_requires_all_positive_identity_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed=true alone is not enough to own a container."""
    monkeypatch.delenv("DEV_WORKSPACE_MCP_TEST_RESOURCE_NAMESPACE", raising=False)
    incomplete = classify_workspace_container({LABEL_MANAGED: "true"})
    assert incomplete.status is OwnershipStatus.UNKNOWN
    assert not incomplete.positively_owned

    owned = classify_workspace_container(
        {
            LABEL_MANAGED: "true",
            LABEL_WORKSPACE_ID: "ws_example",
            LABEL_REPOSITORY_ID: "example",
        },
        expected_workspace_id="ws_example",
        expected_repository_id="example",
    )
    assert owned.status is OwnershipStatus.OWNED

    wrong_repo = classify_workspace_container(
        {
            LABEL_MANAGED: "true",
            LABEL_WORKSPACE_ID: "ws_example",
            LABEL_REPOSITORY_ID: "other",
        },
        expected_workspace_id="ws_example",
        expected_repository_id="example",
    )
    assert wrong_repo.status is OwnershipStatus.FOREIGN


def test_pmoat_001_resource_model_names_every_required_lifecycle_state() -> None:
    assert {state.value for state in ResourceLifecycleState} == {
        "active",
        "retained",
        "cleanup_pending",
        "orphaned_owned",
        "foreign",
        "unknown",
        "degraded",
    }
    assert set(OWNERSHIP_RULES) == set(ResourceKind)
    assert ResourceKind.DOCKER_IMAGE in INVENTORY_ONLY_RESOURCE_KINDS
    assert OWNERSHIP_RULES[ResourceKind.DOCKER_IMAGE].inventory_only is True
    assert (
        "path is reconstructed as <state_dir>/jobs/<validated job id>"
        in OWNERSHIP_RULES[ResourceKind.JOB_LOG_DIRECTORY].positive_evidence
    )
