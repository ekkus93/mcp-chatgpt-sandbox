"""Filesystem tool primitives — read/write/list/delete on paths already
validated by `FilesystemSecurity` (§13, §12.2, §47)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import (
    FileConflictError,
    InvalidArgumentError,
    PathGitMetadataError,
    PathOutsideRepositoryError,
)
from dev_workspace_mcp.filesystem.security import FilesystemSecurity


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ListEntry:
    path: str
    """Repository-relative, POSIX-style, regardless of host OS."""
    type: str
    """"file" | "directory" | "symlink"."""
    size: int | None
    modified_at: str | None
    is_symlink: bool


@dataclass(frozen=True, slots=True)
class ReadResult:
    content: str | None
    """`None` when `is_binary` is true — never guess at decoding it."""
    sha256: str
    """Of the file's full, untruncated content — usable as `expected_sha256`
    on a later write regardless of any range/truncation applied here."""
    size: int
    """Full file size in bytes, independent of any range/truncation applied."""
    truncated: bool
    is_binary: bool


@dataclass(frozen=True, slots=True)
class WriteResult:
    sha256: str
    size: int


class FilesystemService:
    """Bound to one repository (`security`) and one set of size limits
    (`config`, §12.2)."""

    def __init__(self, security: FilesystemSecurity, config: FilesystemConfig) -> None:
        self._security = security
        self._config = config

    # --- list_files -----------------------------------------------------------

    def list_files(
        self, relative_path: str = "", *, recursive: bool = False, max_entries: int | None = None
    ) -> list[ListEntry]:
        start = self._security.resolve(relative_path)
        if not start.is_dir():
            raise InvalidArgumentError(
                f"not a directory: {relative_path!r}", details={"path": relative_path}
            )

        limit = self._config.max_listing_entries
        if max_entries is not None:
            limit = min(max_entries, limit)

        entries: list[ListEntry] = []
        candidates = sorted(start.rglob("*") if recursive else start.iterdir())
        for candidate in candidates:
            if len(entries) >= limit:
                break
            if self._is_git_metadata(candidate):
                continue
            entries.append(self._describe(candidate))
        return entries

    def _is_git_metadata(self, path: Path) -> bool:
        """§12.1: excluded from listings by default (§13.1).

        HR-012 (`docs/HARDENING_SPEC.md` §7): an escaping symlink
        (`PathOutsideRepositoryError`) is *not* Git metadata — it is not
        excluded here at all. A real regression: this used to let
        `resolve()`'s `PathOutsideRepositoryError` propagate straight out
        of `list_files`'s loop, aborting the *entire* listing over one
        symlink (an ordinary `.venv/bin/python -> /usr/bin/python3` is
        enough). `_describe` below is what reports it as a plain entry
        instead, per HR-012's actual requirement — this function's only
        job is the Git-metadata question.
        """
        try:
            self._security.resolve(path.relative_to(self._security.root).as_posix())
        except PathGitMetadataError:
            return True
        except PathOutsideRepositoryError:
            return False
        return False

    def _describe(self, path: Path) -> ListEntry:
        relative = path.relative_to(self._security.root).as_posix()
        is_symlink = path.is_symlink()
        if is_symlink:
            # A dangling symlink, or one that escapes the repository to a
            # target that genuinely exists (§7.1/AT-002, HR-012): both
            # reported as a plain entry, with no stat-able size/mtime.
            # For the escaping-to-a-real-target case specifically, this
            # also avoids the alternative below (`path.stat()` would
            # follow the symlink and report the *host* target's real
            # size/mtime) — a disclosure HR-012 doesn't ask for and this
            # daemon has no reason to allow.
            target_within_root = path.exists() and self._security.is_within_root(path.resolve())
            if not target_within_root:
                return ListEntry(
                    path=relative, type="symlink", size=None, modified_at=None, is_symlink=True
                )
        stat = path.stat()
        kind = "directory" if path.is_dir() else "file"
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        size = None if kind == "directory" else stat.st_size
        return ListEntry(
            path=relative, type=kind, size=size, modified_at=modified_at, is_symlink=is_symlink
        )

    # --- read_file --------------------------------------------------------------

    def read_file(
        self,
        relative_path: str,
        *,
        byte_range: tuple[int, int] | None = None,
        line_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> ReadResult:
        if byte_range is not None and line_range is not None:
            raise InvalidArgumentError(
                "byte_range and line_range are mutually exclusive",
                details={"path": relative_path},
            )

        # HR-011: race-free open (`FilesystemSecurity.open_validated`) —
        # `is_file()`/`read_bytes()` against the path string again, after
        # `resolve()` already validated it, is exactly the window a
        # container racing to swap a path component to an escaping
        # symlink can win. `fstat` on the opened fd (not a fresh `stat()`
        # on the path) is what makes the "is it a regular file" check
        # itself race-free too.
        fd = self._security.open_validated(relative_path, os.O_RDONLY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise InvalidArgumentError(
                    f"not a file: {relative_path!r}", details={"path": relative_path}
                )
            with os.fdopen(fd, "rb", closefd=True) as f:
                fd = -1  # ownership transferred to `f`; nothing left for `finally` to close
                data = f.read()
        finally:
            if fd != -1:
                os.close(fd)

        full_sha = sha256_of(data)
        total_size = len(data)

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if byte_range is not None or line_range is not None:
                raise InvalidArgumentError(
                    f"cannot apply a byte/line range to a binary file: {relative_path!r}",
                    details={"path": relative_path},
                ) from None
            return ReadResult(
                content=None, sha256=full_sha, size=total_size, truncated=False, is_binary=True
            )

        if line_range is not None:
            start, end = line_range
            lines = text.splitlines(keepends=True)
            text = "".join(lines[start:end])
        elif byte_range is not None:
            start, end = byte_range
            text = text.encode("utf-8")[start:end].decode("utf-8", errors="replace")

        limit = self._config.max_read_bytes
        if max_bytes is not None:
            limit = min(max_bytes, limit)
        encoded = text.encode("utf-8")
        truncated = len(encoded) > limit
        if truncated:
            text = encoded[:limit].decode("utf-8", errors="ignore")

        return ReadResult(
            content=text, sha256=full_sha, size=total_size, truncated=truncated, is_binary=False
        )

    # --- write_file -----------------------------------------------------------

    def write_file(
        self, relative_path: str, content: str, *, expected_sha256: str | None = None
    ) -> WriteResult:
        path = self._security.resolve(relative_path)
        data = content.encode("utf-8")

        if len(data) > self._config.max_write_bytes:
            raise InvalidArgumentError(
                f"write exceeds the configured limit "
                f"({len(data)} > {self._config.max_write_bytes} bytes)",
                details={"path": relative_path, "size": len(data)},
            )

        # HR-011: `mkdir -p` against the resolved path string has its own,
        # narrower residual race (a pre-existing malicious symlink at an
        # intermediate component could make it create directories through
        # it) — the `open_validated`/`verify_fd_within_root` calls below
        # still independently guarantee no *data* is ever written outside
        # the repository root even in that case, which is HR-011's actual
        # property; closing the mkdir side effect too would need the full
        # per-component `O_NOFOLLOW` walk HR-011 offers as the alternative
        # to this fd+readlink technique, deferred as a documented gap.
        path.parent.mkdir(parents=True, exist_ok=True)

        # Race-free create-or-open, distinguishing the two (unlike a
        # single `O_CREAT` open) because `expected_sha256=None` and
        # "file exists but is empty" (`sha256_of(b"")`) are genuinely
        # different states the original `path.is_file()` check preserved.
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            existed_before = False
        except FileExistsError:
            fd = os.open(path, os.O_RDWR)
            existed_before = True

        # Checked with the raw fd (not `verify_fd_within_root`, which
        # would close `fd` and raise immediately): a write that escaped
        # may have just *created* a file at that escaped location, and
        # cleaning it up needs the kernel-reported real path, not the
        # racy `path` string again — the `except` block below does that
        # before raising the client-facing error.
        actual = self._security.resolve_open_fd(fd)
        escaped = not self._security.is_within_root(actual)

        try:
            if escaped:
                raise PathOutsideRepositoryError(
                    f"path escaped the repository root between validation and open: "
                    f"{relative_path!r}",
                    details={"path": relative_path},
                )
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise InvalidArgumentError(
                    f"is a directory: {relative_path!r}", details={"path": relative_path}
                )

            if expected_sha256 is not None:
                current: str | None = None
                if existed_before:
                    # The full, untruncated existing content — matching
                    # `ReadResult.sha256`'s own invariant (always the hash
                    # of the whole file, regardless of any read/write size
                    # limit) so a `expected_sha256` from a prior real
                    # `read_file` call compares correctly.
                    existing_size = os.fstat(fd).st_size
                    current = sha256_of(os.pread(fd, existing_size, 0))
                if current != expected_sha256:
                    raise FileConflictError(
                        f"file changed since it was read: {relative_path!r}",
                        details={
                            "path": relative_path,
                            "expected_sha256": expected_sha256,
                            "actual_sha256": current,
                        },
                    )

            os.ftruncate(fd, 0)
            os.pwrite(fd, data, 0)
        except BaseException:
            os.close(fd)
            if not existed_before:
                # We created this file above (either at `path`, or — if
                # `escaped` — at `actual`, the kernel-reported real
                # location a race redirected the create to) and the
                # write never committed — remove it so a rejected write
                # has no side effect, matching the original behavior
                # (which never touched disk before this same check).
                # Unlinking `actual` rather than `path` again is what
                # makes this correct even when they differ: cleaning up
                # via the racy string a second time can silently miss
                # the file a race already moved elsewhere — confirmed
                # empirically (a stray file was left behind outside the
                # repository on early versions of this fix that unlinked
                # `path` here instead).
                with contextlib.suppress(OSError):
                    actual.unlink()
            raise
        else:
            os.close(fd)

        return WriteResult(sha256=sha256_of(data), size=len(data))

    # --- delete_file ------------------------------------------------------------

    def delete_file(self, relative_path: str, *, recursive: bool = False) -> None:
        # HR-003 (`docs/HARDENING_SPEC.md` §5): the repository root is
        # never a valid delete target. `_reject_in_tree_git_metadata`
        # protects `.git` by path *component*, which the root itself
        # bypasses — its own relative path has zero components, so
        # ".git" is never among them — and `shutil.rmtree` on the root
        # takes the working tree and `.git` together, with nothing left
        # locally recoverable. Checked regardless of `recursive`: a
        # non-recursive delete of a directory already raises below, but
        # the root check has to come first so that failure reads as "the
        # root can't be deleted," not a generic "pass recursive" prompt.
        path = self._security.resolve(relative_path)
        if path == self._security.root:
            raise InvalidArgumentError(
                "the repository root cannot be deleted (§12.1/HR-003)",
                details={"path": relative_path},
            )

        # HR-011: race-free delete. `open_parent_validated` re-verifies
        # the containing directory against what was actually opened, not
        # the path string again; `os.unlink`/`shutil.rmtree`'s own
        # `dir_fd` parameter then resolves `name` relative to that pinned
        # fd with no further path-string re-resolution at all — closing
        # the race for the delete syscalls themselves, not only the
        # validation step before them. (`shutil.rmtree` on Linux already
        # walks a directory tree via `dir_fd`-relative operations
        # internally once it has its own top-level fd — verified against
        # this Python's `shutil._use_fd_functions` — so passing `dir_fd`
        # here extends that same safety to the *initial* hop too.)
        parent_fd, name = self._security.open_parent_validated(relative_path)
        try:
            try:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise InvalidArgumentError(
                    f"does not exist: {relative_path!r}", details={"path": relative_path}
                ) from None

            if stat.S_ISDIR(st.st_mode):
                if not recursive:
                    raise InvalidArgumentError(
                        f"{relative_path!r} is a directory; recursive deletion must be explicit",
                        details={"path": relative_path},
                    )
                # HR-003: refuse to sweep a nested `.git` (a submodule or
                # vendored checkout) into a recursive delete of its
                # parent. `_reject_in_tree_git_metadata` already blocks
                # *targeting* such a path directly; this closes the gap
                # where the target is the containing directory instead.
                # The scan itself still walks the resolved path string —
                # a narrower, already-accepted residual race matching
                # H4's own scope, distinct from the escape race this
                # function's `dir_fd` use closes.
                if any(path.rglob(".git")):
                    raise PathGitMetadataError(
                        f"{relative_path!r} contains a nested repository's Git "
                        "metadata; refusing to delete it (§12.1/HR-003)",
                        details={"path": relative_path},
                    )
                shutil.rmtree(name, dir_fd=parent_fd)
            else:
                os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
