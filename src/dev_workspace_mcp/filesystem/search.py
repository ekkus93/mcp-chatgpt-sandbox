"""`search_files`: filename globbing, text search, regex search, honoring
`.gitignore` by default (§13.7, §48, §49).

`.gitignore` support here is a deliberately scoped subset, not a full
implementation of Git's matching rules: only the repository-root
`.gitignore` is read (not nested ones), and each non-comment line is
matched with `fnmatch` against either the candidate's full relative path
or its basename — no negation (`!pattern`) and no `**` semantics. This
covers the common cases (`target/`, `*.pyc`, `node_modules`) the §49
default list already names; a project relying on more elaborate
`.gitignore` rules should pass `include_ignored=True` and filter results
itself, or use `exec("git ls-files ...")`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dev_workspace_mcp.errors import (
    InvalidArgumentError,
    PathGitMetadataError,
    PathOutsideRepositoryError,
)
from dev_workspace_mcp.filesystem.security import FilesystemSecurity

SearchMode = Literal["glob", "text", "regex"]

#: §49's default-skipped directories, applied unless `include_ignored`.
DEFAULT_IGNORED_DIR_NAMES = frozenset(
    {".git", "target", "node_modules", ".venv", "dist", "build", ".gradle"}
)


@dataclass(frozen=True, slots=True)
class SearchMatch:
    path: str
    """Repository-relative, POSIX-style."""
    line_number: int | None
    """`None` for a filename-only (`glob`) match."""
    line_text: str | None


def _load_gitignore_patterns(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns = []
    for raw_line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def _matches_gitignore(relative_path: str, name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def search_files(
    security: FilesystemSecurity,
    *,
    pattern: str,
    mode: SearchMode = "text",
    path: str = "",
    max_results: int = 500,
    include_ignored: bool = False,
) -> list[SearchMatch]:
    start = security.resolve(path)
    if not start.is_dir():
        raise InvalidArgumentError(f"not a directory: {path!r}", details={"path": path})

    compiled = re.compile(pattern) if mode == "regex" else None
    gitignore_patterns = [] if include_ignored else _load_gitignore_patterns(security.root)

    results: list[SearchMatch] = []
    for candidate in sorted(start.rglob("*")):
        if len(results) >= max_results:
            break
        if not candidate.is_file():
            continue

        relative = candidate.relative_to(security.root).as_posix()

        if not include_ignored:
            parts = candidate.relative_to(security.root).parts
            if any(part in DEFAULT_IGNORED_DIR_NAMES for part in parts[:-1]):
                continue
            if _matches_gitignore(relative, candidate.name, gitignore_patterns):
                continue

        try:
            security.resolve(relative)
        except PathGitMetadataError:
            continue
        except PathOutsideRepositoryError:
            # HR-012 (`docs/HARDENING_SPEC.md` §7): a symlink escaping the
            # repository to a target that genuinely exists passes the
            # `is_file()` check above (which follows symlinks) and used
            # to reach here unguarded, crashing the *entire* search over
            # one symlink. Unlike `list_files` (which reports an escaping
            # symlink as a plain entry), a search has no reason to read
            # through it into host content — skipped, same as an entry
            # `.gitignore`-excludes.
            continue
        except RuntimeError:
            # RR-007 (`docs/REMEDIATION_SPEC.md` §5): a symlink loop
            # raises `RuntimeError` from `Path.resolve()` (confirmed
            # empirically, CPython 3.12), not `OSError`/`WorkspaceError`.
            # `candidate.is_file()` above already returns `False` for a
            # loop (an `OSError` it catches internally), so this branch
            # is never actually reached by a *pure* symlink loop today —
            # but relying on that check-ordering coincidence to protect
            # this `resolve()` call is exactly the "luck, not coverage"
            # this task exists to close, in case a future change ever
            # reorders these checks or reaches `resolve()` some other way.
            continue

        if mode == "glob":
            if fnmatch.fnmatch(candidate.name, pattern) or fnmatch.fnmatch(relative, pattern):
                results.append(SearchMatch(path=relative, line_number=None, line_text=None))
            continue

        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — silently skipped, not an error

        for line_number, line in enumerate(text.splitlines(), start=1):
            if len(results) >= max_results:
                break
            hit = (mode == "text" and pattern in line) or (
                mode == "regex" and compiled is not None and compiled.search(line) is not None
            )
            if hit:
                results.append(SearchMatch(path=relative, line_number=line_number, line_text=line))

    return results
