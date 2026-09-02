"""Post-MVP Operations O1 / PMOAT-001 ownership-proof acceptance."""

from __future__ import annotations

import contextlib

import docker

from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.caches import build_cache_mounts, resolve_caches
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import current_test_resource_namespace


def test_pmoat_001_daemon_created_cache_volume_has_positive_ownership_labels(
    docker_client: docker.DockerClient,
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
) -> None:
    """A cache name alone cannot be the ownership proof PMOR-001 requires."""
    cache = resolve_caches(
        repository_id=repository.id,
        project_caches=[ProjectCacheEntry(name="cargo", path="$HOME/.cargo")],
    )[0]
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
    assert labels["dev-workspace-mcp.managed"] == "true"
    assert labels["dev-workspace-mcp.resource-kind"] == "cache-volume"
    assert labels["dev-workspace-mcp.repository-id"] == repository.id
    assert labels["dev-workspace-mcp.cache-name"] == cache.name
    namespace = current_test_resource_namespace()
    assert namespace is not None
    assert labels["dev-workspace-mcp.test-namespace"] == namespace


def test_pmoat_001_foreign_cache_lookalike_has_no_ownership_authority(
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """A deliberately similar volume name remains foreign without proof labels."""
    cache = resolve_caches(
        repository_id=repository.id,
        project_caches=[ProjectCacheEntry(name="cargo", path="$HOME/.cargo")],
    )[0]
    foreign = docker_client.volumes.create(name=f"{cache.volume_name}-foreign")
    try:
        foreign.reload()
        labels = foreign.attrs.get("Labels") or {}
        assert labels.get("dev-workspace-mcp.managed") != "true"
        assert labels.get("dev-workspace-mcp.resource-kind") != "cache-volume"
    finally:
        with contextlib.suppress(docker.errors.NotFound):
            foreign.remove(force=True)
