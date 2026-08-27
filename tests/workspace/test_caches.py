"""S30: named dependency-cache volumes (§11). Pure logic, no Docker
needed — `docker.types.Mount` just builds a plain dict."""

import pytest

from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.workspace.caches import (
    build_cache_mounts,
    cache_volume_name,
    resolve_caches,
)


def test_cache_volume_name_is_repository_scoped() -> None:
    assert (
        cache_volume_name(repository_id="example", cache_name="cargo") == "mcp-cache-example-cargo"
    )


def test_two_repositories_never_share_a_cache_volume() -> None:
    a = cache_volume_name(repository_id="repo-a", cache_name="cargo")
    b = cache_volume_name(repository_id="repo-b", cache_name="cargo")
    assert a != b


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$HOME/.cargo", ".cargo"),
        ("~/.cargo", ".cargo"),
        (".cargo", ".cargo"),
        ("$HOME/go/pkg/mod", "go/pkg/mod"),
        ("$HOME/.cargo/", ".cargo"),
    ],
)
def test_resolve_caches_normalizes_every_documented_path_form(raw: str, expected: str) -> None:
    resolved = resolve_caches(
        repository_id="example", project_caches=[ProjectCacheEntry(path=raw, name="cargo")]
    )
    assert resolved[0].home_relative_path == expected
    assert resolved[0].container_path == f"/home/mcpuser/{expected}"


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "/root/.cargo", "~other/.cargo"])
def test_resolve_caches_rejects_a_non_home_relative_path(bad_path: str) -> None:
    with pytest.raises(ContainerCreateFailedError):
        resolve_caches(
            repository_id="example", project_caches=[ProjectCacheEntry(path=bad_path, name="cargo")]
        )


def test_resolve_caches_rejects_path_traversal() -> None:
    with pytest.raises(ContainerCreateFailedError):
        resolve_caches(
            repository_id="example",
            project_caches=[ProjectCacheEntry(path="$HOME/../etc", name="cargo")],
        )


def test_resolve_caches_rejects_an_empty_relative_path() -> None:
    with pytest.raises(ContainerCreateFailedError):
        resolve_caches(
            repository_id="example", project_caches=[ProjectCacheEntry(path="$HOME/", name="cargo")]
        )


def test_build_cache_mounts_are_writable_docker_volumes() -> None:
    resolved = resolve_caches(
        repository_id="example",
        project_caches=[
            ProjectCacheEntry(path="$HOME/.cargo", name="cargo"),
            ProjectCacheEntry(path="$HOME/.rustup", name="rustup"),
        ],
    )
    mounts = build_cache_mounts(resolved)
    assert len(mounts) == 2
    assert mounts[0]["Type"] == "volume"
    assert mounts[0]["Source"] == "mcp-cache-example-cargo"
    assert mounts[0]["Target"] == "/home/mcpuser/.cargo"
    assert mounts[0]["ReadOnly"] is False


def test_resolve_caches_is_empty_for_no_project_caches() -> None:
    assert resolve_caches(repository_id="example", project_caches=[]) == []
