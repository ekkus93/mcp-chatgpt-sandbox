"""Filesystem path validation and Git-metadata exclusion (§12, §12.1).

Runs host-side in the daemon (§6.3). Every filesystem tool (S10) resolves
a client-supplied path through `FilesystemSecurity.resolve` before it
ever touches disk — this is the boundary AT-001, AT-002, and AT-033 test.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from dev_workspace_mcp.errors import PathGitMetadataError, PathOutsideRepositoryError
from dev_workspace_mcp.workspace.mounts import resolve_git_dir


class FilesystemSecurity:
    """Bound to one repository. Resolves and validates repository-relative
    paths for the lifetime of the instance.

    The repository's external Git directory (a linked worktree's or
    submodule's gitfile target, §18.2 rule 3) is resolved once at
    construction — best-effort: if it cannot be determined, exclusion
    rule 3 is simply not applied, rather than failing every unrelated
    filesystem request over it. Rules 1 and 2 (`.git` as a literal path
    component within the working tree) do not depend on this and always
    apply.
    """

    def __init__(self, repository_root: Path, *, authorized_roots: list[Path]) -> None:
        self._root = repository_root.resolve()
        self._external_git_dir = _external_git_dir(self._root, authorized_roots)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str) -> Path:
        """Resolve `relative_path` against the repository root and enforce
        §12/§12.1. Raises `PathOutsideRepositoryError` (AT-001, AT-002) or
        `PathGitMetadataError` (AT-033); otherwise returns the canonical
        host path.

        The external-Git-directory check (rule 3) runs *before* the
        containment check: its target is, by construction, always outside
        `self._root` (an in-tree `.git` is rule 1/2's concern, not rule
        3's), so checking containment first would report every rule-3 hit
        as a generic escape rather than the more specific "this is Git
        metadata."
        """
        candidate = self._resolve_candidate(relative_path)

        if self._external_git_dir is not None and (
            candidate == self._external_git_dir or candidate.is_relative_to(self._external_git_dir)
        ):
            raise PathGitMetadataError(
                f"path resolves into the repository's Git directory: {relative_path!r}",
                details={"path": relative_path},
            )

        if candidate != self._root and not candidate.is_relative_to(self._root):
            raise PathOutsideRepositoryError(
                f"path escapes the repository root: {relative_path!r}",
                details={"path": relative_path},
            )

        self._reject_in_tree_git_metadata(candidate, relative_path)
        return candidate

    def open_validated(self, relative_path: str, flags: int, *, mode: int = 0o666) -> int:
        """HR-011 (`docs/HARDENING_SPEC.md` §7): open `relative_path`
        race-free. `resolve()` validates by resolving symlinks and
        checking containment on a *path string* — but every caller that
        then re-opens that string (`path.read_bytes()`,
        `path.write_bytes()`, ...) leaves a window between validation and
        the actual I/O in which the container (via `exec`) can swap a
        path component to an escaping symlink. Reproduced: a host private
        key returned via `read_file` on the very first race iteration.

        Closes it by re-verifying against what was **actually opened**,
        not the path string again: `/proc/self/fd/<fd>` is the kernel's
        own record of an open file descriptor's canonical path. An
        `open()` is atomic, so whatever this reports is genuinely what
        got opened, immune to any race that happens after the fact — a
        component swapped mid-resolution would already have to have
        redirected the `open()` call itself to be reflected here, and if
        it did, this check catches it before any data is read or
        written. Linux-only (`/proc`), matching this daemon's existing
        Linux-only assumptions elsewhere (`docker/default/entrypoint.sh`'s
        `setpriv`/`useradd`).
        """
        resolved = self.resolve(relative_path)
        fd = os.open(resolved, flags, mode)
        self.verify_fd_within_root(fd, relative_path)
        return fd

    def open_parent_validated(self, relative_path: str) -> tuple[int, str]:
        """The same race closure as `open_validated`, for callers that
        need to operate on the *parent* directory via a `dir_fd` —
        `os.unlink(name, dir_fd=...)`/`shutil.rmtree(name, dir_fd=...)`
        (`delete_file`) resolve `name` relative to the fd with no further
        path-string re-resolution at all, closing the race for the
        delete itself, not only the validation step. Returns `(parent_fd,
        final_component_name)`; the caller is responsible for closing
        `parent_fd`."""
        resolved = self.resolve(relative_path)
        parent_fd = os.open(resolved.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.verify_fd_within_root(parent_fd, relative_path)
        return parent_fd, resolved.name

    def resolve_open_fd(self, fd: int) -> Path:
        """The kernel's own record of `fd`'s canonical path
        (`/proc/self/fd/<fd>`) — the same source `open_validated`'s own
        containment check reads, exposed here for a caller with a more
        involved open sequence than a single call (`write_file`'s
        create-vs-update branch below) that needs the *actual* resolved
        path locally — e.g. to clean up a side effect created at the
        escaped location — without that internal host path ever
        reaching a client-facing error's `details` (§31 errors are
        serialized back to the client; the client only ever sees the
        `relative_path` it supplied, never a resolved host path)."""
        return Path(os.readlink(f"/proc/self/fd/{fd}"))

    def is_within_root(self, path: Path) -> bool:
        return path == self._root or path.is_relative_to(self._root)

    def verify_fd_within_root(self, fd: int, relative_path: str) -> None:
        """Public for callers with their own multi-step open sequence
        that don't need the actual escaped path (unlike `write_file`,
        which uses `resolve_open_fd`/`is_within_root` directly — see
        their own docstrings). Closes `fd` and raises
        `PathOutsideRepositoryError` if it escaped, otherwise a no-op."""
        try:
            actual = self.resolve_open_fd(fd)
        except OSError:
            os.close(fd)
            raise
        if not self.is_within_root(actual):
            os.close(fd)
            raise PathOutsideRepositoryError(
                f"path escaped the repository root between validation and open: {relative_path!r}",
                details={"path": relative_path},
            )

    def _resolve_candidate(self, relative_path: str) -> Path:
        # A leading "/" is stripped, not honoured: pathlib's own `/`
        # operator *replaces* the left side entirely when the right side
        # is absolute (`Path("/root") / PurePosixPath("/etc/passwd")` ==
        # `Path("/etc/passwd")`) — silently discarding the repository
        # root. A client-supplied path is always repository-relative,
        # never absolute, regardless of what it looks like.
        normalized = relative_path.replace("\\", "/").lstrip("/")
        # Path.resolve() then normalizes "." / ".." and follows symlinks,
        # which is what turns an escaping symlink into a path outside the
        # root (AT-002) rather than one that merely looks contained.
        return (self._root / PurePosixPath(normalized)).resolve()

    def _reject_in_tree_git_metadata(self, candidate: Path, original: str) -> None:
        """Rules 1 and 2: `.git` at the root, or a `.git` path component
        at any depth. Only meaningful once containment already holds —
        `relative_to` below cannot fail here."""
        relative_parts = candidate.relative_to(self._root).parts
        if ".git" in relative_parts:
            raise PathGitMetadataError(
                f"path is repository Git metadata, not a project file: {original!r}",
                details={"path": original},
            )


def _external_git_dir(root: Path, authorized_roots: list[Path]) -> Path | None:
    """The repository's actual Git directory, if it can be determined and
    lies outside `root` (a linked worktree or submodule gitfile, §18.2).
    `None` for an ordinary in-tree repository, and also `None` — rather
    than raising — if resolution fails for any reason: this is additive
    protection, not a reason to fail an unrelated filesystem request.
    """
    try:
        info = resolve_git_dir(root, workspace_path="/workspace", authorized_roots=authorized_roots)
    except Exception:
        return None
    return None if info.is_ordinary_in_tree else info.host_path
