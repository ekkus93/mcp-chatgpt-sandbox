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


#: H29 (`docs/HARDENING_TODO.md`, HD3): `read_file`'s streaming pass over
#: whatever lies past its bounded content buffer, for `full_sha` — large
#: enough to keep syscall overhead low, small relative to a multi-gigabyte
#: file (the whole point of never holding it all in memory at once).
_READ_FILE_HASH_CHUNK_BYTES = 1024 * 1024


def _hash_pread(fd: int, total_size: int, *, chunk_size: int = _READ_FILE_HASH_CHUNK_BYTES) -> str:
    """RR-006 (`docs/REMEDIATION_SPEC.md` §5): `write_file`'s
    `expected_sha256` check used a single `os.pread(fd, total_size, 0)` —
    but a single `pread(2)` can transfer fewer bytes than requested for
    any reason, and the Linux kernel caps one call at `0x7ffff000` bytes
    (just under 2 GiB) regardless. A file over that size silently hashed
    a truncated prefix, reporting the wrong "current" hash and raising a
    spurious `FileConflictError` against a client's correct
    `expected_sha256` for a large but genuinely unchanged file. Looped
    the same way `read_file`'s own hash pass already is (`chunk_size`
    exposed only so a test can force multiple iterations without an
    actual multi-gigabyte fixture)."""
    hasher = hashlib.sha256()
    offset = 0
    while offset < total_size:
        chunk = os.pread(fd, min(chunk_size, total_size - offset), offset)
        if not chunk:
            break  # file shrank concurrently underneath us; hash what was actually read
        hasher.update(chunk)
        offset += len(chunk)
    return hasher.hexdigest()


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

        RR-007 (`docs/REMEDIATION_SPEC.md` §5): a symlink loop is the
        same story, one exception type further out. `Path.resolve()`
        (which `self._security.resolve()` uses internally) detects a
        cycle itself and raises `RuntimeError` — confirmed empirically
        against CPython 3.12 — which is neither an `OSError` nor a
        `WorkspaceError`, so it used to propagate uncaught here too,
        aborting the whole listing over two mutually-referencing
        symlinks (trivially creatable via `exec`). Treated the same way
        as an escaping symlink: not Git metadata, reported as a plain
        entry by `_describe` instead (which does not itself re-trigger
        this — `path.exists()` on a loop returns `False`, an `OSError`
        it already catches internally, short-circuiting before its own
        `path.resolve()` call would run).
        """
        try:
            self._security.resolve(path.relative_to(self._security.root).as_posix())
        except PathGitMetadataError:
            return True
        except (PathOutsideRepositoryError, RuntimeError):
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

        # H29 (`docs/HARDENING_TODO.md`, HD3): computed before the read,
        # not derived from `len(data)` after unconditionally reading the
        # whole file — the bound this task exists to apply.
        limit = self._config.max_read_bytes
        if max_bytes is not None:
            limit = min(max_bytes, limit)

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
                if byte_range is None and line_range is None:
                    # H29: the common case — bound the *content* buffer
                    # by `limit`, known before reading, rather than
                    # reading the whole file into memory first and
                    # truncating the response afterward. The remainder
                    # (if any) is still streamed through the hasher in
                    # bounded chunks, never held alongside `data`: §13.2
                    # requires `sha256` to be the *whole* file's hash
                    # regardless of truncation (`write_file`'s
                    # `expected_sha256` conflict check depends on this
                    # being correct), so every byte is still read and
                    # hashed — just never all held in memory at once.
                    # `total_size` comes from what was actually counted
                    # while streaming, the same "size == what was really
                    # read" semantics as before (and more accurate than a
                    # pre-read `fstat` under a concurrently-growing file,
                    # which this daemon does not otherwise guard against
                    # here — see HR-011's own file-content race, a
                    # different, already-closed concern).
                    data = f.read(limit + 1)  # +1: detect "more" without slurping it
                    hasher = hashlib.sha256(data)
                    total_size = len(data)
                    while chunk := f.read(_READ_FILE_HASH_CHUNK_BYTES):
                        hasher.update(chunk)
                        total_size += len(chunk)
                    full_sha = hasher.hexdigest()
                else:
                    # byte_range/line_range can name a window anywhere in
                    # the file — needs the full content decoded to slice
                    # at an arbitrary position, unlike the case above.
                    data = f.read()
                    full_sha = sha256_of(data)
                    total_size = len(data)
        finally:
            if fd != -1:
                os.close(fd)

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

        # `limit` was already computed above, before the read.
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

        # H28 (`docs/HARDENING_TODO.md`, HR-030): closes the mkdir-side
        # gap this used to leave open — see `ensure_parent_directories`'s
        # own docstring. A previously-legitimate layout with an in-bounds
        # symlinked intermediate directory used as a `write_file` target
        # is now rejected too, not only a malicious one: distinguishing
        # "existed benignly before this call" from "just swapped in by a
        # racing attacker" is not possible from a single `mkdir`/`open`
        # sequence (both look identical — `FileExistsError` either way),
        # so accepting either would silently reopen the exact race this
        # closes. A deliberate, documented tradeoff, not an oversight.
        self._security.ensure_parent_directories(relative_path)

        # Race-free create-or-open, distinguishing the two (unlike a
        # single `O_CREAT` open) because `expected_sha256=None` and
        # "file exists but is empty" (`sha256_of(b"")`) are genuinely
        # different states the original `path.is_file()` check preserved.
        # RR-006 (`docs/REMEDIATION_SPEC.md` §5): both raw `os.open`
        # calls are classified — an untranslated `IsADirectoryError`/
        # `PermissionError` used to crash the tool call outright rather
        # than reach the client as a typed error.
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            existed_before = False
        except FileExistsError:
            try:
                fd = os.open(path, os.O_RDWR)
            except IsADirectoryError as exc:
                raise InvalidArgumentError(
                    f"is a directory: {relative_path!r}", details={"path": relative_path}
                ) from exc
            except PermissionError as exc:
                raise InvalidArgumentError(
                    f"permission denied: {relative_path!r}", details={"path": relative_path}
                ) from exc
            existed_before = True
        except PermissionError as exc:
            raise InvalidArgumentError(
                f"permission denied: {relative_path!r}", details={"path": relative_path}
            ) from exc

        actual: Path | None = None
        try:
            # Checked with the raw fd (not `verify_fd_within_root`, which
            # would close `fd` and raise immediately): a write that
            # escaped may have just *created* a file at that escaped
            # location, and cleaning it up needs the kernel-reported real
            # path, not the racy `path` string again — the `except` block
            # below does that before raising the client-facing error.
            # RR-006: this read (and the `is_within_root` check depending
            # on it) now runs *inside* the `try`, not before it — `fd` is
            # a real, just-opened descriptor, so `resolve_open_fd`
            # reading `/proc/self/fd/<fd>` cannot fail in practice, but
            # leaving it outside the `try` meant an OSError from it would
            # leak `fd` uncaught rather than reach the `except` below.
            # (`stat.S_ISDIR` used to be checked here too — genuinely
            # dead code: opening an existing directory with `O_RDWR`
            # above already raises `IsADirectoryError`, now classified,
            # before this line could ever run against a directory's fd.)
            actual = self._security.resolve_open_fd(fd)
            if not self._security.is_within_root(actual):
                raise PathOutsideRepositoryError(
                    f"path escaped the repository root between validation and open: "
                    f"{relative_path!r}",
                    details={"path": relative_path},
                )

            if expected_sha256 is not None:
                current: str | None = None
                if existed_before:
                    # The full, untruncated existing content — matching
                    # `ReadResult.sha256`'s own invariant (always the hash
                    # of the whole file, regardless of any read/write size
                    # limit) so a `expected_sha256` from a prior real
                    # `read_file` call compares correctly. `_hash_pread`,
                    # not a single `os.pread` (RR-006): a lone call can
                    # under-read a large file, hashing a truncated prefix.
                    existing_size = os.fstat(fd).st_size
                    current = _hash_pread(fd, existing_size)
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
            if not existed_before and actual is not None:
                # We created this file above (either at `path`, or — if
                # it escaped — at `actual`, the kernel-reported real
                # location a race redirected the create to) and the
                # write never committed — remove it so a rejected write
                # has no side effect, matching the original behavior
                # (which never touched disk before this same check).
                # Using `actual`'s parent as a `dir_fd` (RR-006), not
                # `actual.unlink()`'s own fresh path-string resolution of
                # every component: unlinking `actual` rather than `path`
                # again is what makes this correct even when they differ
                # (cleaning up via the racy string a second time can
                # silently miss the file a race already moved elsewhere —
                # confirmed empirically on an early version of the
                # original H9 fix that unlinked `path` here instead), and
                # resolving only the immediate parent by `dir_fd` rather
                # than the whole string again narrows that same class of
                # re-resolution race by one more component.
                # `actual is None` only when `resolve_open_fd` itself
                # failed above — genuinely can't happen for a freshly
                # opened fd on Linux, but if it somehow did, guessing at
                # a path to clean up would reopen exactly the race this
                # closes, so it is deliberately left unattempted instead.
                with contextlib.suppress(OSError):
                    parent_fd = os.open(actual.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.unlink(actual.name, dir_fd=parent_fd)
                    finally:
                        os.close(parent_fd)
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
