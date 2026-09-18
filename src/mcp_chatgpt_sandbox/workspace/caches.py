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

import hashlib
import re
from dataclasses import dataclass

from docker.types import Mount

from mcp_chatgpt_sandbox.errors import ContainerCreateFailedError
from mcp_chatgpt_sandbox.project_config import ProjectCacheEntry
from mcp_chatgpt_sandbox.workspace.container_config import DEFAULT_CONTAINER_HOME
from mcp_chatgpt_sandbox.workspace.naming import current_test_resource_namespace

_SLUG_RUN_RE = re.compile(r"[^a-z0-9]+")


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
    if ":" in relative:
        # HR-008: `build_cache_mounts`' caller joins every resolved cache
        # path into one colon-separated `MCP_CACHE_PATHS` string
        # (`tools/workspace.py`) that `entrypoint.sh` splits on `:` — a
        # path containing a literal `:` would silently become two
        # entries there instead of the one intended here.
        raise ContainerCreateFailedError(
            f"cache {cache_name!r}: path {raw_path!r} may not contain ':' (§11)",
            details={"cache": cache_name, "path": raw_path},
        )
    return relative


def cache_volume_name(*, repository_id: str, cache_name: str) -> str:
    """Repository-scoped (see module docstring): every workspace opened
    for `repository_id` resolves to the same Docker volume for a cache of
    this `name`, independent of `workspace_id`.

    HR-009 (`docs/HARDENING_SPEC.md` §4.2): hash-derived from the
    `(repository_id, cache_name)` *pair* — like `naming.container_name`
    already does for workspace identity — not plain string concatenation.
    Both fields are restricted to `[a-z0-9-]` (`project_config.py`'s own
    validation for `cache_name`; repository ids the same way), so a
    hyphen-joined string is genuinely ambiguous, not just theoretically:
    `repository_id="my-app", cache_name="cargo"` and
    `repository_id="my", cache_name="app-cargo"` both concatenate to the
    identical `"my-app-cargo"`. A NUL byte between the two fields *before*
    hashing is what actually closes the gap — it cannot appear in either
    validated component, so the pre-hash input is unambiguous; hashing an
    already-concatenated (and already-ambiguous) string would only have
    hidden the collision, not fixed it.

    PSCR-011 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`) renamed the
    prefix from `mcp-cache-` to `mcp-chatgpt-sandbox-cache-`, so volumes
    created before that change are orphaned exactly as this paragraph
    already described. Identity is unchanged: still the hash of the
    NUL-separated `(repository_id, cache_name)` pair, so HR-009's
    ambiguity stays closed.

    Changing this naming scheme means a cache volume created under an
    older (`mcp-cache-{repository_id}-{cache_name}`) scheme is no longer
    found or reused — the daemon mounts a fresh, empty volume under the
    new name instead. Not data loss (the old volume still exists in
    Docker, just unreferenced from here on — `docker volume ls` finds it,
    an operator can inspect or `docker volume rm` it manually) and not
    silently unsafe (a cache is, by §11's own design, disposable and
    rebuildable — nothing here treats it as a source of truth); still a
    real, deliberate, documented behavior change on upgrade, not an
    oversight."""
    namespace = current_test_resource_namespace()
    identity = f"{repository_id}\x00{cache_name}"
    if namespace is not None:
        identity = f"{identity}\x00{namespace}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:8]
    slug = _SLUG_RUN_RE.sub("-", f"{repository_id}-{cache_name}".lower()).strip("-")
    if namespace is None:
        return f"mcp-chatgpt-sandbox-cache-{slug}-{digest}"
    return f"mcp-chatgpt-sandbox-cache-{slug}-t{namespace}-{digest}"


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
