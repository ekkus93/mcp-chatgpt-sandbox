"""S30: named dependency-cache volumes (§11). Pure logic, no Docker
needed — `docker.types.Mount` just builds a plain dict."""

import pytest

from mcp_chatgpt_sandbox.errors import ContainerCreateFailedError
from mcp_chatgpt_sandbox.project_config import ProjectCacheEntry
from mcp_chatgpt_sandbox.workspace.caches import (
    build_cache_mounts,
    cache_volume_name,
    resolve_caches,
)


def test_cache_volume_name_is_repository_scoped() -> None:
    name = cache_volume_name(repository_id="example", cache_name="cargo")
    assert name.startswith("mcp-cache-example-cargo-")


def test_cache_volume_name_is_deterministic() -> None:
    """Same inputs SHALL always resolve to the same volume — every
    workspace opened for a repository needs to find the *same* cache,
    not a fresh one each time."""
    first = cache_volume_name(repository_id="example", cache_name="cargo")
    second = cache_volume_name(repository_id="example", cache_name="cargo")
    assert first == second


def test_two_repositories_never_share_a_cache_volume() -> None:
    a = cache_volume_name(repository_id="repo-a", cache_name="cargo")
    b = cache_volume_name(repository_id="repo-b", cache_name="cargo")
    assert a != b


def test_at_hat008_ambiguous_concatenation_does_not_collide() -> None:
    """HAT-008, HR-009's own literal example: repository `my-app` + cache
    `cargo` and repository `my` + cache `app-cargo` both concatenate to
    the identical string `"my-app-cargo"` — plain string joining (the
    pre-H23 implementation) collided here."""
    a = cache_volume_name(repository_id="my-app", cache_name="cargo")
    b = cache_volume_name(repository_id="my", cache_name="app-cargo")
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


def test_at_hat007_resolve_caches_rejects_a_colon_in_the_path() -> None:
    """HR-008: `build_cache_mounts`' caller joins every resolved cache
    path into one colon-separated `MCP_CACHE_PATHS` string that
    `entrypoint.sh` splits on `:` — a path containing a literal `:`
    would silently become two entries there instead of the one this
    config entry actually named."""
    with pytest.raises(ContainerCreateFailedError):
        resolve_caches(
            repository_id="example",
            project_caches=[ProjectCacheEntry(path="$HOME/weird:path", name="cargo")],
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
    assert mounts[0]["Source"] == cache_volume_name(repository_id="example", cache_name="cargo")
    assert mounts[0]["Target"] == "/home/mcpuser/.cargo"
    assert mounts[0]["ReadOnly"] is False


def test_resolve_caches_is_empty_for_no_project_caches() -> None:
    assert resolve_caches(repository_id="example", project_caches=[]) == []
