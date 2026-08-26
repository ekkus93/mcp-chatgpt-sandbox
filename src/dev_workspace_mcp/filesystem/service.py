"""Filesystem tool primitives — read/write/list/delete on paths already
validated by `FilesystemSecurity` (§13, §12.2, §47)."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import FileConflictError, InvalidArgumentError, PathGitMetadataError
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
        """§12.1: excluded from listings by default (§13.1)."""
        try:
            self._security.resolve(path.relative_to(self._security.root).as_posix())
        except PathGitMetadataError:
            return True
        return False

    def _describe(self, path: Path) -> ListEntry:
        relative = path.relative_to(self._security.root).as_posix()
        is_symlink = path.is_symlink()
        if is_symlink and not path.exists():
            # A dangling symlink (including one that escapes the
            # repository, §7.1/AT-002): reported as an entry, but with no
            # stat-able size/mtime.
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

        path = self._security.resolve(relative_path)
        if not path.is_file():
            raise InvalidArgumentError(
                f"not a file: {relative_path!r}", details={"path": relative_path}
            )

        data = path.read_bytes()
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

        if expected_sha256 is not None:
            current = sha256_of(path.read_bytes()) if path.is_file() else None
            if current != expected_sha256:
                raise FileConflictError(
                    f"file changed since it was read: {relative_path!r}",
                    details={
                        "path": relative_path,
                        "expected_sha256": expected_sha256,
                        "actual_sha256": current,
                    },
                )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return WriteResult(sha256=sha256_of(data), size=len(data))

    # --- delete_file ------------------------------------------------------------

    def delete_file(self, relative_path: str, *, recursive: bool = False) -> None:
        path = self._security.resolve(relative_path)
        if not path.exists() and not path.is_symlink():
            raise InvalidArgumentError(
                f"does not exist: {relative_path!r}", details={"path": relative_path}
            )
        if path.is_dir() and not path.is_symlink():
            if not recursive:
                raise InvalidArgumentError(
                    f"{relative_path!r} is a directory; recursive deletion must be explicit",
                    details={"path": relative_path},
                )
            shutil.rmtree(path)
        else:
            path.unlink()
