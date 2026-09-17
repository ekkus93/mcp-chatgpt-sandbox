"""Filesystem path validation and Git-metadata exclusion (§12, §12.1).

Runs host-side in the daemon (§6.3). Every filesystem tool (S10) resolves
a client-supplied path through `FilesystemSecurity.resolve` before it
ever touches disk — this is the boundary AT-001, AT-002, and AT-033 test.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
from pathlib import Path, PurePosixPath

from mcp_chatgpt_sandbox.errors import (
    InvalidArgumentError,
    PathGitMetadataError,
    PathOutsideRepositoryError,
)
from mcp_chatgpt_sandbox.workspace.mounts import resolve_git_dir

_logger = logging.getLogger(__name__)


def _open_classified(path: Path, flags: int, mode: int, *, relative_path: str) -> int:
    """Every raw `os.open` this module makes against a resolved `Path`
    lands here. RR-006 (`docs/REMEDIATION_SPEC.md` §5): classifies the
    handful of `OSError` subclasses a client-supplied path can
    legitimately trigger (`FileNotFoundError`/`IsADirectoryError`/
    `NotADirectoryError`/`PermissionError`) into a typed
    `InvalidArgumentError`, instead of letting them propagate as raw
    `OSError` subclasses — `translate_workspace_errors` only catches
    `WorkspaceError`, so an untranslated one crashes the tool call
    instead of reaching the client as a clean typed error. Reproduced
    empirically: `read_file(missing)` / `write_file(onto a directory)` /
    `delete_file(missing parent)` all crashed before this fix; all three
    returned `InvalidArgumentError` before H9's fd rewrite regressed
    them (`git show 4bfff54`)."""
    try:
        return os.open(path, flags, mode)
    except FileNotFoundError as exc:
        raise InvalidArgumentError(
            f"does not exist: {relative_path!r}", details={"path": relative_path}
        ) from exc
    except IsADirectoryError as exc:
        raise InvalidArgumentError(
            f"is a directory: {relative_path!r}", details={"path": relative_path}
        ) from exc
    except NotADirectoryError as exc:
        raise InvalidArgumentError(
            f"a path component is not a directory: {relative_path!r}",
            details={"path": relative_path},
        ) from exc
    except PermissionError as exc:
        raise InvalidArgumentError(
            f"permission denied: {relative_path!r}", details={"path": relative_path}
        ) from exc


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
        fd = _open_classified(resolved, flags, mode, relative_path=relative_path)
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
        `parent_fd`.

        RR-006 (`docs/REMEDIATION_SPEC.md` §5): only the *parent* is
        resolved through `self.resolve()` (which follows symlinks, as it
        must — every intermediate component's own containment still has
        to hold). `final_component_name` is the client's literal final
        path segment, never run through symlink-following resolution.
        Reproduced empirically that the old version — which resolved the
        *whole* path, including the final component — silently
        substituted a symlink's *target*'s parent+name for the *link*'s
        own: `delete_file` on an in-repo symlink deleted the target and
        left the link dangling, the opposite of `os.unlink`'s (and
        `os.stat(..., follow_symlinks=False)`'s) own never-follow-the-
        final-symlink semantics, which the rest of this call already
        depends on and which this bug silently defeated."""
        normalized = relative_path.replace("\\", "/").lstrip("/")
        pure = PurePosixPath(normalized)
        final_name = pure.name
        if not final_name or final_name in (".", ".."):
            raise PathOutsideRepositoryError(
                f"path escapes the repository root: {relative_path!r}",
                details={"path": relative_path},
            )
        if final_name == ".git":
            # Defense in depth: `delete_file` (this method's one current
            # caller) already runs the full `self.resolve(relative_path)`
            # first, whose `_reject_in_tree_git_metadata` covers this —
            # but that full resolve is this class's documented contract
            # for *every* caller (`Every filesystem tool ... resolves a
            # client-supplied path through FilesystemSecurity.resolve
            # before it ever touches disk`), which the final component
            # here deliberately does *not* go through (that is the whole
            # point of this method, per RR-006 above). A future caller
            # that skips the up-front `resolve()` should not silently
            # regain access to `.git` through this path.
            raise PathGitMetadataError(
                f"path is repository Git metadata, not a project file: {relative_path!r}",
                details={"path": relative_path},
            )
        parent_relative = pure.parent.as_posix()
        resolved_parent = self.resolve("" if parent_relative == "." else parent_relative)
        parent_fd = _open_classified(
            resolved_parent,
            os.O_RDONLY | os.O_DIRECTORY,
            0,
            relative_path=relative_path,
        )
        self.verify_fd_within_root(parent_fd, relative_path)
        return parent_fd, final_name

    def ensure_parent_directories(self, relative_path: str) -> None:
        """HR-011 (`docs/HARDENING_SPEC.md` §7): closes `write_file`'s
        one remaining documented gap — a plain `path.parent.mkdir(parents=True,
        exist_ok=True)` against the resolved path *string* would follow a
        pre-existing malicious symlink at an intermediate component,
        creating directories through it (the existing `open_validated`/
        `resolve_open_fd` checks elsewhere still guarantee no *data* is
        ever written outside the root even so — that's HR-011's actual
        property — but this `mkdir` side effect itself was not covered).

        The full per-component `O_NOFOLLOW` walk HR-011 names as the
        alternative to the fd+readlink technique: starting from the
        repository root's own fd (trusted — `self._root` is already a
        resolved, validated path), each intermediate component is
        created with `os.mkdir(..., dir_fd=parent_fd)` (a no-op if it
        already exists as a real directory) and then *opened* with
        `O_NOFOLLOW` before descending into it — atomically refusing a
        symlink at that name, whether it was already there or was
        swapped in between the `mkdir` and the `open` (the kernel checks
        what is actually at that name at the moment of the `open`
        syscall itself, the same atomicity guarantee `open_validated`'s
        own containment check relies on)."""
        normalized = relative_path.replace("\\", "/").lstrip("/")
        parts = [p for p in PurePosixPath(normalized).parent.parts if p not in ("", ".")]

        dir_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts:
                if part == "..":
                    raise PathOutsideRepositoryError(
                        f"path escapes the repository root: {relative_path!r}",
                        details={"path": relative_path},
                    )
                # already a real directory, or a symlink — O_NOFOLLOW below decides
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, mode=0o777, dir_fd=dir_fd)
                try:
                    next_fd = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                    )
                except OSError as exc:
                    # RR-006 (`docs/REMEDIATION_SPEC.md` §5): a symlink at
                    # this component — real or just swapped in by a racing
                    # attacker — is what `O_NOFOLLOW` refuses to enter,
                    # HR-011's actual concern, correctly reported as an
                    # escape. A plain non-directory *file* already at this
                    # name is a different, benign condition — a client
                    # path with a stray file blocking it, nothing escaped.
                    # Verified empirically that both raise the identical
                    # `NotADirectoryError`/`ENOTDIR` here, so which one
                    # actually happened is checked directly (`lstat`)
                    # rather than guessed at from the exception alone.
                    # Either way the walk still refuses to proceed — this
                    # only affects which error the client sees, never
                    # whether the mkdir/descend is allowed to continue —
                    # so an `lstat` that itself fails defaults to the more
                    # serious "escape" wording rather than the benign one.
                    is_symlink = True
                    if isinstance(exc, NotADirectoryError):
                        with contextlib.suppress(OSError):
                            is_symlink = stat.S_ISLNK(os.lstat(part, dir_fd=dir_fd).st_mode)
                    if not is_symlink:
                        raise InvalidArgumentError(
                            f"a path component is not a directory: {relative_path!r}",
                            details={"path": relative_path},
                        ) from exc
                    raise PathOutsideRepositoryError(
                        f"path escapes the repository root: {relative_path!r}",
                        details={"path": relative_path},
                    ) from exc
                os.close(dir_fd)
                dir_fd = next_fd
        finally:
            os.close(dir_fd)

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

    def verify_resolved_path(self, candidate: Path, relative_path: str) -> None:
        """G2R-005 (`docs/GAP2_SPEC.md`): `resolve()` enforces three
        rules against a path *string* — the external-Git-directory
        check (rule 3), containment, and `_reject_in_tree_git_metadata`
        (rules 1/2). The post-open re-checks below (`verify_fd_within_
        root`, `write_file`'s own inline sequence) used to re-verify
        containment only, on the kernel-reported real path from
        `resolve_open_fd` — which is correct for what it checks, but
        left rules 1–3 unchecked at the one point that actually matters
        for them: *after* the race window `resolve()` cannot see across.

        Under the shipped default `git_dir_mode: read-write`, `.git`
        lives *inside* the repository root, so containment alone does
        not catch a race that redirects the open into it — reproduced:
        a container process replacing the target with a symlink into
        `.git/config` (read) or `.git/hooks/pre-commit` (write) between
        `resolve()` and the actual `open()` produced an fd whose real
        path is inside root, which the old containment-only check
        passed. This closes that gap by applying the identical rule set
        `resolve()` applies, to the fd's real path instead of the
        pre-open string — deliberately duplicated rather than shared
        with `resolve()` itself (which operates on a string and raises
        a differently-worded containment message, `"escapes"` vs.
        `"escaped ... between validation and open"` — the post-open
        wording is diagnostically meaningful, since it tells a caller
        this was caught after a race rather than rejected outright, and
        is preserved here rather than merged away).

        Raises `PathGitMetadataError` or `PathOutsideRepositoryError`.
        Never touches `fd` — callers own that lifecycle themselves,
        matching how `is_within_root` (the predicate this replaces at
        both call sites) already worked."""
        if self._external_git_dir is not None and (
            candidate == self._external_git_dir or candidate.is_relative_to(self._external_git_dir)
        ):
            raise PathGitMetadataError(
                f"path resolves into the repository's Git directory: {relative_path!r}",
                details={"path": relative_path},
            )
        if not self.is_within_root(candidate):
            raise PathOutsideRepositoryError(
                f"path escaped the repository root between validation and open: {relative_path!r}",
                details={"path": relative_path},
            )
        self._reject_in_tree_git_metadata(candidate, relative_path)

    def verify_fd_within_root(self, fd: int, relative_path: str) -> None:
        """Public for callers with their own multi-step open sequence
        that don't need the actual escaped path (unlike `write_file`,
        which uses `resolve_open_fd`/`verify_resolved_path` directly —
        see their own docstrings). Closes `fd` and raises
        `PathOutsideRepositoryError`/`PathGitMetadataError` if it
        escaped, otherwise a no-op."""
        try:
            actual = self.resolve_open_fd(fd)
        except OSError:
            os.close(fd)
            raise
        try:
            self.verify_resolved_path(actual, relative_path)
        except (PathOutsideRepositoryError, PathGitMetadataError):
            os.close(fd)
            raise

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
        # H31 (`docs/HARDENING_TODO.md`, HR-031): still swallowed —
        # additive protection failing is correctly not a reason to fail
        # an unrelated filesystem request — but now visible for
        # troubleshooting instead of silent. `root` only, not the
        # exception's own text: `resolve_git_dir` can echo repository-
        # controlled `.git`-file content, which HR-013 redaction is
        # built for `details` dicts and client-facing messages, not a
        # bare log string — logging only the host path this daemon
        # itself already trusts keeps that content out of the log
        # entirely rather than routing it through redaction after the
        # fact.
        _logger.debug("external git dir resolution failed for %s", root, exc_info=True)
        return None
    return None if info.is_ordinary_in_tree else info.host_path
