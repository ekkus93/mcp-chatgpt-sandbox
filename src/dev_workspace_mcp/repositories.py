"""Repository registry: resolves repository IDs to canonical host paths and
enforces authorization (§7, FR-001, FR-002).

Two sources feed the registry, in precedence order:

1. `repositories:` — explicit configuration. Always authorized.
2. `repository_roots:` — optional discovery (§7.1) of the immediate
   subdirectories of each root that look like a Git repository. A
   discovered entry's id is derived from its directory name; on a
   collision with an explicitly configured id (or an earlier root's
   discovery), the explicit/earlier entry wins and the later one is
   dropped rather than guessed at.

Discovery scans one level deep — each example in §7 has repositories as
direct children of a root, and an unbounded recursive walk would be the
full-tree scan §48 warns against for a different tool, for the same reason.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.errors import RepositoryNotAllowedError

_SLUG_RUN_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str | None:
    """Directory name -> a valid `repositories[].id` (§7's `^[a-z0-9][a-z0-9-]*$`),
    or `None` if nothing usable survives."""
    slug = _SLUG_RUN_RE.sub("-", name.lower()).strip("-")
    return slug or None


def _looks_like_git_repository(path: Path) -> bool:
    return (path / ".git").exists()


def _discover_under_root(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield `(id, canonical_path)` for each immediate subdirectory of
    `root` that is a Git repository, after resolving symlinks and
    confirming the result still lies under `root` (§7.1, AT-022)."""
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        return

    for entry in sorted(resolved_root.iterdir()):
        if not entry.is_dir() or not _looks_like_git_repository(entry):
            continue

        resolved_entry = entry.resolve()
        if resolved_entry == resolved_root or not resolved_entry.is_relative_to(resolved_root):
            continue  # symlink escape (§7.1): not treated as authorized (AT-022)

        slug = _slugify(entry.name)
        if slug is not None:
            yield slug, resolved_entry


@dataclass(frozen=True, slots=True)
class RepositoryRecord:
    """A repository the registry has authorized (§7.2's identity fields
    that are knowable without reading Git state — see `workspace_info`,
    S11, for the rest)."""

    id: str
    canonical_path: Path
    github: str | None
    """`None` for a discovered repository: its GitHub identity is not
    knowable without reading Git state (Phase 3), and GitHub sync is keyed
    off explicit repository configuration (§19) regardless."""


class RepositoryRegistry:
    """Resolves and authorizes repository IDs (FR-001, FR-002)."""

    def __init__(self, config: ServerConfig) -> None:
        self._records: dict[str, RepositoryRecord] = {}

        for repo in config.repositories:
            self._records[repo.id] = self._from_config(repo)

        for root in config.repository_roots:
            for slug, canonical_path in _discover_under_root(root):
                if slug in self._records:
                    continue  # explicit config, or an earlier root, wins
                self._records[slug] = RepositoryRecord(
                    id=slug, canonical_path=canonical_path, github=None
                )

    @staticmethod
    def _from_config(repo: RepositoryConfig) -> RepositoryRecord:
        return RepositoryRecord(id=repo.id, canonical_path=repo.path.resolve(), github=repo.github)

    def get(self, repository_id: str) -> RepositoryRecord:
        """Resolve `repository_id`, or fail closed (FR-002)."""
        try:
            return self._records[repository_id]
        except KeyError:
            raise RepositoryNotAllowedError(
                f"repository not authorized: {repository_id!r}",
                details={"repository_id": repository_id},
            ) from None

    def list_all(self) -> list[RepositoryRecord]:
        return sorted(self._records.values(), key=lambda r: r.id)
