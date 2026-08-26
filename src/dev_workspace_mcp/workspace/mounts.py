"""Constructs the Docker mounts for a workspace container (§5.1, §5.4, §18.2).

Two mounts at most: the working tree (always, read/write), and — only when
the repository's actual Git directory needs its own entry — a second mount
governed by `git_dir_mode`. §5.4 is satisfied by construction: this module
adds nothing beyond those two, and nothing else in the system adds mounts
yet (caches land in S30, the SSH-agent socket in S23).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docker.types import Mount

from dev_workspace_mcp.errors import PathOutsideRepositoryError

GitDirMode = Literal["read-write", "read-only", "none"]


@dataclass(frozen=True, slots=True)
class GitDirInfo:
    """Where the repository's actual Git directory lives (§18.2)."""

    host_path: Path
    """Absolute host path to the Git directory that governs this checkout."""
    container_path: str
    """Where it belongs inside the container. For an ordinary repository
    this is `<workspace_path>/.git`; for a gitfile (linked worktree,
    submodule) it is the *same absolute host path*, so the `gitdir:` and
    `commondir` references inside the checkout resolve unchanged."""
    is_ordinary_in_tree: bool
    """True when `.git` is a real directory directly under the working
    tree — already covered by the working-tree bind mount, so
    `git_dir_mode: read-write` needs no second mount at all. False for a
    gitfile, which points outside the working tree regardless of mode."""


def resolve_git_dir(
    repository_root: Path, *, workspace_path: str, authorized_roots: list[Path]
) -> GitDirInfo:
    """Resolve the repository's actual Git directory (§18.2).

    An ordinary repository's `.git` is a directory directly under
    `repository_root`. A linked worktree's `.git` is a *file* containing
    `gitdir: <path>`; that directory's `commondir` file, if present, in
    turn points to the main repository's `.git` — both outside the
    checkout. A submodule's gitfile behaves the same way.
    """
    git_path = repository_root / ".git"
    ordinary_path = (repository_root / ".git").resolve()

    if git_path.is_dir():
        return GitDirInfo(
            host_path=ordinary_path,
            container_path=f"{workspace_path}/.git",
            is_ordinary_in_tree=True,
        )

    gitdir = _resolve_gitfile(git_path)
    common = _resolve_commondir(gitdir)

    if not _is_under_any(common, authorized_roots):
        raise PathOutsideRepositoryError(
            f"resolved Git directory {common} for {repository_root} is not under "
            "an authorized repository root (§18.2)",
            details={"repository_root": str(repository_root), "resolved_git_dir": str(common)},
        )

    return GitDirInfo(
        host_path=common,
        container_path=str(common),
        is_ordinary_in_tree=(common == ordinary_path),
    )


def _resolve_gitfile(git_file: Path) -> Path:
    """`.git` as a file contains `gitdir: <path>`, absolute or relative to
    the file's own directory."""
    content = git_file.read_text().strip()
    prefix = "gitdir:"
    if not content.startswith(prefix):
        raise PathOutsideRepositoryError(
            f"{git_file} is not a directory and does not look like a gitfile",
            details={"path": str(git_file)},
        )
    target = content[len(prefix) :].strip()
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = git_file.parent / target_path
    return target_path.resolve()


def _resolve_commondir(gitdir: Path) -> Path:
    """`commondir`, if present, holds a path relative to `gitdir` pointing
    at the main repository's Git directory. Its absence means `gitdir`
    already *is* the common directory (Git's own default when
    `GIT_COMMON_DIR` is unset)."""
    commondir_file = gitdir / "commondir"
    if not commondir_file.is_file():
        return gitdir
    relative = commondir_file.read_text().strip()
    return (gitdir / relative).resolve()


def _is_under_any(path: Path, roots: list[Path]) -> bool:
    return any(path == root.resolve() or path.is_relative_to(root.resolve()) for root in roots)


def build_workspace_mounts(
    *,
    host_repository_path: Path,
    workspace_path: str,
    git_dir_mode: GitDirMode,
    authorized_roots: list[Path],
) -> list[Mount]:
    """The full mount list for a workspace container: the working tree,
    plus a second mount for the Git directory whenever `git_dir_mode`
    needs one that the working-tree mount does not already provide."""
    mounts = [
        Mount(
            target=workspace_path,
            source=str(host_repository_path.resolve()),
            type="bind",
            read_only=False,
        )
    ]

    git_info = resolve_git_dir(
        host_repository_path, workspace_path=workspace_path, authorized_roots=authorized_roots
    )

    if git_dir_mode == "read-write":
        if not git_info.is_ordinary_in_tree:
            # A gitfile target lives outside the working-tree mount
            # entirely — it needs its own mount even in read-write mode.
            mounts.append(
                Mount(
                    target=git_info.container_path,
                    source=str(git_info.host_path),
                    type="bind",
                    read_only=False,
                )
            )
    elif git_dir_mode == "read-only":
        mounts.append(
            Mount(
                target=git_info.container_path,
                source=str(git_info.host_path),
                type="bind",
                read_only=True,
            )
        )
    else:  # "none": a subpath of a bind mount cannot be "not mounted" — mask it.
        mounts.append(
            Mount(target=git_info.container_path, source=None, type="tmpfs", read_only=True)
        )

    return mounts
