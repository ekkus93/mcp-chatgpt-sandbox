"""Named dependency-cache volumes (§11) — S30.

Repository-scoped: a cache volume's name is keyed by `repository_id` and
the cache's own `name`, so every workspace opened for a given repository
shares the same cache, but two different repositories never share one
(§11: "The default SHOULD favor isolation where cross-project
contamination may affect reproducibility" — the strongest default this
task implements is one level short of full per-workspace isolation, but
still never crosses repository boundaries). A configurable `workspace` /
`repository` / `shared` scope, as §11 also describes, is not implemented
here — a deliberately scoped-down decision, not an oversight (see
docs/TODO.md).

Caches exist only when a repository's `.mcp-workspace.yml` declares them
(`project_config.py`) — there is no automatic default set of toolchain
caches a project didn't ask for.
"""

from __future__ import annotations

from dataclasses import dataclass

from docker.types import Mount

from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.workspace.container_config import DEFAULT_CONTAINER_HOME


@dataclass(frozen=True, slots=True)
class ResolvedCache:
    name: str
    volume_name: str
    home_relative_path: str
    container_path: str


def _home_relative_path(raw_path: str, *, cache_name: str) -> str:
    """§10's own example authors `caches[].path` as `$HOME/.cargo` (also
    accepts the equally common `~/.cargo` form, and a bare
    already-relative `.cargo`). Rejects anything that isn't genuinely
    home-relative — an absolute path elsewhere, or one that walks out via
    `..` — since a project-authored value ends up naming a Docker mount
    *target* inside the container; unlike the git-dir mounts (§18.2),
    there is no reason a cache path should ever need to point outside the
    container user's own home."""
    relative = raw_path
    for prefix in ("$HOME/", "~/"):
        if relative.startswith(prefix):
            relative = relative[len(prefix) :]
            break
    else:
        if relative.startswith(("/", "~", "$")):
            raise ContainerCreateFailedError(
                f"cache {cache_name!r}: path {raw_path!r} must be relative to "
                "$HOME (e.g. '$HOME/.cargo', '~/.cargo', or '.cargo') (§11)",
                details={"cache": cache_name, "path": raw_path},
            )

    relative = relative.strip("/")
    if not relative or ".." in relative.split("/"):
        raise ContainerCreateFailedError(
            f"cache {cache_name!r}: path {raw_path!r} is not a valid $HOME-relative path (§11)",
            details={"cache": cache_name, "path": raw_path},
        )
    return relative


def cache_volume_name(*, repository_id: str, cache_name: str) -> str:
    """Repository-scoped (see module docstring): every workspace opened
    for `repository_id` resolves to the same Docker volume for a cache of
    this `name`, independent of `workspace_id`."""
    return f"mcp-cache-{repository_id}-{cache_name}"


def resolve_caches(
    *, repository_id: str, project_caches: list[ProjectCacheEntry]
) -> list[ResolvedCache]:
    return [
        ResolvedCache(
            name=entry.name,
            volume_name=cache_volume_name(repository_id=repository_id, cache_name=entry.name),
            home_relative_path=(relative := _home_relative_path(entry.path, cache_name=entry.name)),
            container_path=f"{DEFAULT_CONTAINER_HOME}/{relative}",
        )
        for entry in project_caches
    ]


def build_cache_mounts(caches: list[ResolvedCache]) -> list[Mount]:
    """§11: "Cache volumes SHALL be owned by the container user's uid" —
    a Docker named volume is created root-owned by default, so ownership
    is `entrypoint.sh`'s job (via `MCP_CACHE_PATHS`, S30), run once as
    root before the container drops privileges; this function only
    builds the mount itself, never read-only (a cache has to be
    writable by the toolchain using it)."""
    return [
        Mount(target=cache.container_path, source=cache.volume_name, type="volume", read_only=False)
        for cache in caches
    ]
