"""Filesystem tool primitives — read/write/list/delete on paths already
validated by `FilesystemSecurity` (§13, §12.2, §47)."""

from __future__ import annotations

import contextlib
import hashlib
import logging
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
from dev_workspace_mcp.utf8_boundary import trim_incomplete_trailing_char


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: H29 (`docs/HARDENING_TODO.md`, HD3): `read_file`'s streaming pass over
#: whatever lies past its bounded content buffer, for `full_sha` — large
#: enough to keep syscall overhead low, small relative to a multi-gigabyte
#: file (the whole point of never holding it all in memory at once).
_READ_FILE_HASH_CHUNK_BYTES = 1024 * 1024


#: G3R-008 (`docs/GAP3_SPEC.md`): returned-entry count is not a work
#: bound when candidates can be skipped. Keep a separate derived budget
#: large enough for ordinary churn but finite under pathological trees.
#: Derive it from the daemon's configured maximum listing size, not the
#: caller's per-request `max_entries`, so asking for one entry does not
#: make a handful of concurrent vanishes spuriously fatal.
_LISTING_CANDIDATES_PER_CONFIGURED_ENTRY = 10


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


def _write_all(fd: int, data: bytes) -> None:
    """G2R-010 (`docs/GAP2_SPEC.md`): the same discipline `_hash_pread`
    above already applies to reads — a single `os.write`/`os.pwrite` can
    transfer fewer bytes than requested (POSIX permits transferring
    fewer bytes than requested for any reason, not only for the sizes
    `_hash_pread`'s own 2 GiB kernel-cap note discusses), and
    `write_file`'s old single-call `os.pwrite(fd, data, 0)` never checked
    its return value — a short write there silently reported a
    `sha256`/`size` for the buffer it *intended* to write, not what
    actually reached disk. Looped until every byte is confirmed written."""
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])


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

        if limit <= 0:
            return []

        entries: list[ListEntry] = []
        candidates_examined = 0
        candidate_limit = (
            self._config.max_listing_entries * _LISTING_CANDIDATES_PER_CONFIGURED_ENTRY
        )
        pending_directories = [start]
        next_directory = 0

        # G3R-008 / G3AT-008: traversal work and returned entries are two
        # different budgets. `Path.rglob()` cannot prune a directory before
        # its own recursion has already committed to descending into it, so
        # filtering `.git` in the outer loop still walked `.git/objects`.
        # Scan one directory at a time instead. A Git-metadata directory is
        # rejected before it can be queued for descent, and symlinked
        # directories are never followed (`follow_symlinks=False`).
        while next_directory < len(pending_directories):
            directory = pending_directories[next_directory]
            next_directory += 1
            directory_fd = self._open_listing_directory(directory)
            if directory_fd is None:
                continue

            try:
                iterator = os.scandir(directory_fd)
                with iterator:
                    while candidates_examined < candidate_limit:
                        try:
                            dir_entry = next(iterator)
                        except StopIteration:
                            break
                        except OSError:
                            # Concurrent mutation/permission change while scanning
                            # this directory: stop descending this directory only;
                            # siblings already queued remain eligible.
                            break

                        candidates_examined += 1
                        # `os.scandir(fd)` intentionally returns names relative
                        # to the pinned directory fd. Reconstruct the display/
                        # validation path from the already-authorized directory
                        # instead of trusting a separately resolved host path.
                        candidate = directory / dir_entry.name
                        if self._is_git_metadata(candidate):
                            continue

                        should_descend = False
                        if recursive:
                            # Concurrent mutation may make a directory entry
                            # unstatable after enumeration. G2R-009 already
                            # requires listing to continue; not descending an
                            # identity we can no longer prove is a directory
                            # is the safe choice.
                            with contextlib.suppress(OSError):
                                should_descend = dir_entry.is_dir(follow_symlinks=False)

                        described = self._describe(candidate)
                        # G2R-009 (`docs/GAP2_SPEC.md`): `None` means the
                        # candidate vanished between enumeration and description.
                        # It still consumed candidate work, but it does not consume
                        # a returned-entry slot.
                        if described is not None:
                            entries.append(described)
                            if len(entries) >= limit:
                                return entries

                        if should_descend:
                            pending_directories.append(candidate)

                    else:
                        # Do not pull one more DirEntry merely to discover whether
                        # this iterator happens to be exhausted: that would perform
                        # work beyond the derived ceiling. If the requested
                        # visible-entry count was not reached when the budget
                        # itself is exhausted, return no silently-partial success.
                        raise InvalidArgumentError(
                            "listing traversal exhausted the derived candidate-work limit",
                            details={
                                "path": relative_path,
                                "recursive": recursive,
                                "max_entries": limit,
                                "entries_collected": len(entries),
                                "candidate_limit": candidate_limit,
                                "candidates_examined": candidates_examined,
                            },
                        )
            finally:
                os.close(directory_fd)

            if not recursive:
                break

        return entries

    def _open_listing_directory(self, directory: Path) -> int | None:
        """Open a queued directory without following a swapped final symlink.

        G3R-008 changes the traversal primitive, so it must not weaken the
        repository boundary while doing so. A candidate can be a real directory
        when `DirEntry.is_dir(follow_symlinks=False)` queues it and then be
        replaced by a container process before the later descent. Re-opening the
        queued path with plain `os.scandir(path)` would follow an escaping symlink
        in that window. Pin the parent with the existing race-checked security
        helper, then open the final component with `O_NOFOLLOW`; scan the resulting
        fd, never the path string.

        `None` is only the concurrent-vanish/unreadable/not-a-real-directory case.
        Repository-boundary or Git-metadata errors from `open_parent_validated`/
        `verify_fd_within_root` remain typed failures and are deliberately not
        swallowed as best effort.
        """
        if directory == self._security.root:
            try:
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError:
                return None
            self._security.verify_fd_within_root(directory_fd, "")
            return directory_fd

        relative = directory.relative_to(self._security.root).as_posix()
        try:
            parent_fd, name = self._security.open_parent_validated(relative)
        except InvalidArgumentError:
            # A directory that was previously enumerable can vanish or have an
            # intermediate component replaced before descent (G2R-009).
            return None
        try:
            try:
                directory_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except OSError:
                # Includes a final-component symlink swap (O_NOFOLLOW), a
                # concurrent vanish, or a permission change. Never retry by
                # path string: doing so would reopen the escape race above.
                return None
        finally:
            os.close(parent_fd)

        self._security.verify_fd_within_root(directory_fd, relative)
        return directory_fd

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

    def _describe(self, path: Path) -> ListEntry | None:
        """`None` means `path` no longer exists — G2R-009
        (`docs/GAP2_SPEC.md`): `list_files` enumerates a directory entry,
        then calls this once per candidate; a
        container process (via `exec`) can delete an entry in that
        window, e.g. `rm -rf build/` against the same bind-mounted
        checkout. `path.stat()` below is the one call in this method
        that does not already tolerate that — `is_symlink`/`exists`/
        `is_dir` all catch `OSError` internally and return `False`
        rather than raise, but `Path.stat()` raises `FileNotFoundError`,
        which is not a `WorkspaceError`, so unguarded this crashed the
        whole listing untyped over one concurrently-deleted entry
        instead of just omitting it. The caller (`list_files`) skips a
        `None` result rather than aborting, the same principle
        `_is_git_metadata` already applies to an escaping symlink or a
        symlink loop."""
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
        try:
            file_stat = path.stat()
        except FileNotFoundError:
            return None
        kind = "directory" if path.is_dir() else "file"
        modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=UTC).isoformat()
        size = None if kind == "directory" else file_stat.st_size
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
                if line_range is not None:
                    # `line_range` genuinely needs the whole content
                    # decoded, to slice at an arbitrary *line* boundary —
                    # unlike `byte_range` below, there is no way to know
                    # where line N starts without scanning (RR-020).
                    data = f.read()
                    full_sha = sha256_of(data)
                    total_size = len(data)
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        raise InvalidArgumentError(
                            f"cannot apply a byte/line range to a binary file: {relative_path!r}",
                            details={"path": relative_path},
                        ) from None
                    start, end = line_range
                    lines = text.splitlines(keepends=True)
                    text = "".join(lines[start:end])
                    encoded = text.encode("utf-8")
                    truncated = len(encoded) > limit
                    if truncated:
                        text = encoded[:limit].decode("utf-8", errors="ignore")
                    return ReadResult(
                        content=text,
                        sha256=full_sha,
                        size=total_size,
                        truncated=truncated,
                        is_binary=False,
                    )

                if byte_range is not None:
                    # RR-020 (`docs/REMEDIATION_SPEC.md` §13): `seek`/`read`
                    # the requested span directly — the H29 rationale for
                    # reading the whole file ("needs the full content
                    # decoded to slice at an arbitrary position") does not
                    # actually apply here: the slice is applied to raw
                    # bytes, so `seek`/`read` is exactly equivalent, and
                    # `read_file(path, byte_range=(0, 1))` on a 10 GB file
                    # used to load all 10 GB just to return one byte.
                    #
                    # `sha256`/`size` still need every byte of the *whole*
                    # file regardless of the requested span (§13.2) —
                    # streamed first, in bounded chunks, same as the
                    # common case below (and this pass is what makes
                    # `end=None`, "to the end of the file", resolvable
                    # without a second read).
                    hasher = hashlib.sha256()
                    total_size = 0
                    while chunk := f.read(_READ_FILE_HASH_CHUNK_BYTES):
                        hasher.update(chunk)
                        total_size += len(chunk)
                    full_sha = hasher.hexdigest()

                    start, end = byte_range
                    start = max(start, 0)
                    effective_end = total_size if end is None else end
                    span_len = max(effective_end - start, 0)
                    read_len = min(span_len, limit)
                    f.seek(start)
                    span_bytes = f.read(read_len) if read_len > 0 else b""
                    # VR-002 (`docs/REVIEW_SPEC.md` §4): `truncated`
                    # SHALL compare bytes actually withheld against
                    # `limit`, not the requested span (`span_len`) —
                    # `end` past `total_size` (or `end=None`'s own
                    # resolution, before this line ever runs) made the
                    # old `span_len > limit` flag `True` on content that
                    # was already complete, because it never looked at
                    # how much of the file actually existed past `start`.
                    available_len = max(min(effective_end, total_size) - start, 0)
                    truncated = available_len > limit
                    # A split character at the client's own chosen
                    # start/end is tolerated the same way it always was —
                    # `errors="replace"`, not a rejection: unlike the
                    # common case's own `limit` boundary (this daemon's
                    # own choice, where trimming to a clean character is
                    # the right thing), an arbitrary client-chosen byte
                    # offset has no "next call" to defer a split character
                    # to, and no obligation to land on one at all.
                    text = span_bytes.decode("utf-8", errors="replace")
                    return ReadResult(
                        content=text,
                        sha256=full_sha,
                        size=total_size,
                        truncated=truncated,
                        is_binary=False,
                    )

                # H29: the common case — bound the *content* buffer by
                # `limit`, known before reading, rather than reading the
                # whole file into memory first and truncating the
                # response afterward. The remainder (if any) is still
                # streamed through the hasher in bounded chunks, never
                # held alongside `data`: §13.2 requires `sha256` to be
                # the *whole* file's hash regardless of truncation
                # (`write_file`'s `expected_sha256` conflict check
                # depends on this being correct), so every byte is still
                # read and hashed — just never all held in memory at
                # once. `total_size` comes from what was actually counted
                # while streaming, the same "size == what was really
                # read" semantics as before (and more accurate than a
                # pre-read `fstat` under a concurrently-growing file,
                # which this daemon does not otherwise guard against here
                # — see HR-011's own file-content race, a different,
                # already-closed concern).
                data = f.read(limit + 1)  # +1: detect "more" without slurping it
                truncated = len(data) > limit
                # RR-018 (`docs/REMEDIATION_SPEC.md` §13): the peek byte
                # is never part of `content` — decoding `data` whole
                # (including it) used to fail whenever `limit` itself, or
                # the peek byte, landed mid-character, misreporting an
                # ordinary text file as binary with `truncated=False`,
                # denying the client even the signal to retry with a
                # range. Trimmed to a clean character boundary only when
                # `truncated` — an *untruncated* `content_bytes` already
                # is the genuine end of the file, and a real dangling
                # partial sequence there means the file itself isn't
                # valid UTF-8, which trimming must not hide.
                content_bytes = data[:limit]
                if truncated:
                    content_bytes = trim_incomplete_trailing_char(content_bytes)
                hasher = hashlib.sha256(data)
                total_size = len(data)
                while chunk := f.read(_READ_FILE_HASH_CHUNK_BYTES):
                    hasher.update(chunk)
                    total_size += len(chunk)
                full_sha = hasher.hexdigest()
        finally:
            if fd != -1:
                os.close(fd)

        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # RR-018: `truncated` is reported correctly here too — a
            # genuinely binary file that happens to be larger than
            # `limit` is still truncated, and the client still deserves
            # that signal even though there is no text content to bound.
            return ReadResult(
                content=None, sha256=full_sha, size=total_size, truncated=truncated, is_binary=True
            )

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

        # G2R-010 (`docs/GAP2_SPEC.md`): write to a fresh temporary file
        # in the same directory, then `os.replace` it over the target —
        # replaces the old truncate-then-`pwrite` sequence directly
        # against the target's own fd, which had two real defects: a
        # write failing partway (disk full) left the *previous* content
        # destroyed (already truncated to empty) rather than untouched,
        # since the old file's own data was gone before the new data
        # ever landed; and a short `os.pwrite` (POSIX permits transferring
        # fewer bytes than requested) went unchecked, so `WriteResult`
        # could report a `sha256`/`size` for the *intended* buffer that
        # never matched what actually reached disk. `os.replace` is a
        # single atomic rename: either the whole new file lands under
        # the target name, or nothing changes — there is no partial
        # state to fail into.
        #
        # A second property, not merely incidental: `rename(2)`/
        # `renameat(2)` never follows a symlink at the *destination*
        # name — it replaces the symlink entry itself. So even if the
        # target name were a symlink a racing container process swapped
        # in (`git_dir_mode: read-write`'s in-tree `.git` being the
        # concrete concern G2R-005 closed for reads/opens), the final
        # `os.replace` below cannot be redirected into writing through
        # it the way the old direct-open sequence could. The *existing*
        # file (if any) is still opened and verified below, for the
        # `expected_sha256` read — that half of the race is unrelated to
        # this atomicity property and still needs `verify_resolved_path`.
        #
        # `parent_fd` itself needs the identical re-verification, and
        # missing this the first time through was a real regression
        # caught by this task's own revert-verification (X5's existing
        # race tests failed against this draft): `path.parent` is a
        # plain string, re-resolved fresh by this `os.open` — if the
        # intermediate component named `path.parent` was swapped to a
        # symlink into `.git` between the `resolve()` call above and
        # this line, `O_DIRECTORY` does not stop the open from following
        # it (it only requires the *final* resolved target to be a
        # directory, which `.git` is). Every operation below is relative
        # to `parent_fd`, so an unverified `parent_fd` would silently
        # reopen G2R-005's exact gap one level up.
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            actual_parent = self._security.resolve_open_fd(parent_fd)
            self._security.verify_resolved_path(actual_parent, relative_path)

            name = path.name
            # No `existed_before` bookkeeping to thread through, unlike
            # the old truncate-in-place sequence: the atomic `os.replace`
            # below never distinguishes create from update — a failure
            # anywhere before it only ever affects the temp file, never
            # the real target, whether or not one existed.
            try:
                # `O_RDONLY` alone, unlike the old `O_RDWR`, does not
                # raise `IsADirectoryError` merely from *opening* a
                # directory on Linux — reading its content is what fails,
                # and this fd is never read from directly (`_hash_pread`
                # only runs after the `stat.S_ISDIR` check just below).
                # Checked explicitly rather than relying on open() to
                # reject it, so the error surfaces here with a clear
                # message instead of confusingly out of `os.replace`
                # further down (which also rejects a directory target,
                # but with an unhelpful `IsADirectoryError` naming the
                # temp filename, not `relative_path`).
                existing_fd: int | None = os.open(name, os.O_RDONLY, dir_fd=parent_fd)
            except FileNotFoundError:
                existing_fd = None
            except PermissionError as exc:
                raise InvalidArgumentError(
                    f"permission denied: {relative_path!r}", details={"path": relative_path}
                ) from exc

            current: str | None = None
            if existing_fd is not None:
                try:
                    if stat.S_ISDIR(os.fstat(existing_fd).st_mode):
                        raise InvalidArgumentError(
                            f"is a directory: {relative_path!r}", details={"path": relative_path}
                        )
                    # RR-006 / G2R-005: the same post-open re-verification
                    # `read_file`/`open_validated` apply — the existing
                    # entry at this name could be a symlink a race just
                    # planted, and `os.open` above followed it.
                    actual = self._security.resolve_open_fd(existing_fd)
                    self._security.verify_resolved_path(actual, relative_path)
                    if expected_sha256 is not None:
                        # The full, untruncated existing content —
                        # matching `ReadResult.sha256`'s own invariant
                        # (always the hash of the whole file, regardless
                        # of any read/write size limit) so an
                        # `expected_sha256` from a prior real `read_file`
                        # call compares correctly. `_hash_pread`, not a
                        # single `os.pread` (RR-006): a lone call can
                        # under-read a large file, hashing a truncated
                        # prefix.
                        existing_size = os.fstat(existing_fd).st_size
                        current = _hash_pread(existing_fd, existing_size)
                finally:
                    os.close(existing_fd)

            if expected_sha256 is not None and current != expected_sha256:
                raise FileConflictError(
                    f"file changed since it was read: {relative_path!r}",
                    details={
                        "path": relative_path,
                        "expected_sha256": expected_sha256,
                        "actual_sha256": current,
                    },
                )

            tmp_name = f".{name}.mcp-write-tmp-{os.urandom(8).hex()}"
            tmp_fd = os.open(tmp_name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=parent_fd)
            try:
                try:
                    _write_all(tmp_fd, data)
                finally:
                    os.close(tmp_fd)
                os.replace(tmp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except BaseException:
                # G3R-013 / G3AT-013: every failure before a successful
                # replacement gets the same dir-fd-relative temp cleanup.
                # If cleanup itself fails, keep the original write/replace
                # exception as the caller-visible error and diagnose the
                # orphan by its name under the already-validated parent fd.
                try:
                    os.unlink(tmp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    logging.getLogger(__name__).warning(
                        "write_file: failed to remove atomic-write temp file %r for %r: %s",
                        tmp_name,
                        relative_path,
                        cleanup_exc,
                    )
                raise
        finally:
            os.close(parent_fd)

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
