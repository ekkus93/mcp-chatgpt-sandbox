"""Filesystem MCP tools: `list_files`, `read_file`, `write_file`,
`patch_file`, `delete_file`, `search_files` (§13, §34.1).

`workspace_id` is resolved to a repository root via the state database
(§67) — the `repository` row a workspace's creation upserts (S6). These
tools do not depend on the `workspace_open` tool (S11) existing; they only
need that a workspace has been created at some point, which is already
true of anything `ensure_workspace` has touched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server import MCPServer

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import (
    FileConflictError,
    InvalidArgumentError,
    translate_workspace_errors,
)
from dev_workspace_mcp.filesystem.patch import PatchFormatError, apply_unified_diff
from dev_workspace_mcp.filesystem.search import SearchMode
from dev_workspace_mcp.filesystem.search import search_files as _search_files
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.manager import resolve_repository_root, touch_last_used


def _resolve_repository_root(
    db: sqlite3.Connection, workspace_id: str, registry: RepositoryRegistry
) -> Path:
    # §8.1: every one of this module's six tools resolves through here,
    # so touching once here covers all of them — a filesystem write/read
    # is real usage and must count against idle-stop the same as `exec`.
    # HR-016: `resolve_repository_root` re-validates against `registry`
    # (the live RepositoryRegistry) — see its own docstring.
    root = resolve_repository_root(db, workspace_id, registry)
    touch_last_used(db, workspace_id)
    return root


# --- tool I/O shapes -----------------------------------------------------------------
#
# Plain @dataclass, deliberately not @dataclass(slots=True): a slotted
# dataclass with an Optional field and no explicit default breaks this MCP
# SDK's output-schema generation — it reads the slot descriptor itself as
# the field's default value and fails to serialize it. Confirmed with the
# SDK installed here (`mcp` 2.1.1); worth re-testing before reintroducing
# slots if the SDK version changes.


@dataclass
class ListFilesEntry:
    path: str
    type: str
    size: int | None
    modified_at: str | None
    is_symlink: bool


@dataclass
class ReadFileOutput:
    content: str | None
    sha256: str
    size: int
    truncated: bool
    is_binary: bool


@dataclass
class WriteFileOutput:
    sha256: str
    size: int


@dataclass
class RejectedHunkOutput:
    old_start: int
    reason: str


@dataclass
class PatchFileOutput:
    applied: bool
    old_sha256: str
    sha256: str | None
    """The new content's hash — `None` when `applied` is False."""
    rejected_hunks: list[RejectedHunkOutput] = field(default_factory=list)


@dataclass
class DeleteFileOutput:
    deleted: bool


@dataclass
class SearchMatchOutput:
    path: str
    line_number: int | None
    line_text: str | None


# --- registration ----------------------------------------------------------------------


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    registry: RepositoryRegistry,
    filesystem_config: FilesystemConfig,
    repository_roots: list[Path],
) -> None:
    def service_for(workspace_id: str) -> FilesystemService:
        root = _resolve_repository_root(db, workspace_id, registry)
        security = FilesystemSecurity(root, authorized_roots=repository_roots)
        return FilesystemService(security, filesystem_config)

    @server.tool(name="list_files", description="List files and directories (§13.1).")
    @translate_workspace_errors
    async def list_files(
        workspace_id: str,
        path: str = "",
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> list[ListFilesEntry]:
        entries = service_for(workspace_id).list_files(
            path, recursive=recursive, max_entries=max_entries
        )
        return [
            ListFilesEntry(
                path=e.path,
                type=e.type,
                size=e.size,
                modified_at=e.modified_at,
                is_symlink=e.is_symlink,
            )
            for e in entries
        ]

    @server.tool(name="read_file", description="Read a repository file (§13.2).")
    @translate_workspace_errors
    async def read_file(
        workspace_id: str,
        path: str,
        byte_start: int | None = None,
        byte_end: int | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        max_bytes: int | None = None,
    ) -> ReadFileOutput:
        byte_range = (
            None if byte_start is None and byte_end is None else (byte_start or 0, byte_end)
        )
        line_range = (
            None if line_start is None and line_end is None else (line_start or 0, line_end)
        )
        result = service_for(workspace_id).read_file(
            path,
            byte_range=byte_range,  # type: ignore[arg-type]
            line_range=line_range,  # type: ignore[arg-type]
            max_bytes=max_bytes,
        )
        return ReadFileOutput(
            content=result.content,
            sha256=result.sha256,
            size=result.size,
            truncated=result.truncated,
            is_binary=result.is_binary,
        )

    @server.tool(name="write_file", description="Write complete file contents (§13.3).")
    @translate_workspace_errors
    async def write_file(
        workspace_id: str, path: str, content: str, expected_sha256: str | None = None
    ) -> WriteFileOutput:
        result = service_for(workspace_id).write_file(
            path, content, expected_sha256=expected_sha256
        )
        return WriteFileOutput(sha256=result.sha256, size=result.size)

    @server.tool(name="patch_file", description="Apply a unified diff to a file (§13.4).")
    @translate_workspace_errors
    async def patch_file(
        workspace_id: str, path: str, diff: str, expected_sha256: str | None = None
    ) -> PatchFileOutput:
        # HR-005 (`docs/HARDENING_SPEC.md` §5): the declared limit on the
        # incoming diff payload was defined and never read — enforced here,
        # before the file is even read, so an oversized diff fails cheaply.
        diff_bytes = len(diff.encode("utf-8"))
        if diff_bytes > filesystem_config.max_patch_bytes:
            raise InvalidArgumentError(
                f"diff exceeds the configured limit "
                f"({diff_bytes} > {filesystem_config.max_patch_bytes} bytes)",
                details={"path": path, "size": diff_bytes},
            )

        svc = service_for(workspace_id)
        current = svc.read_file(path)
        if current.is_binary:
            raise InvalidArgumentError(
                f"cannot patch a binary file: {path!r}", details={"path": path}
            )
        # HR-004: a truncated read must never become a write of the
        # truncated content over the whole file. A real regression:
        # `current.content` here used to be silently the first
        # `max_read_bytes` of the file whenever it exceeded that limit,
        # and the patched (truncated) result was then written back over
        # the entire file — destroying everything past the truncation
        # point while reporting `applied: True`.
        if current.truncated:
            raise InvalidArgumentError(
                f"file is larger than the configured read limit and cannot be "
                f"safely patched as a whole: {path!r}",
                details={"path": path, "size": current.size},
            )
        if expected_sha256 is not None and current.sha256 != expected_sha256:
            raise FileConflictError(
                f"file changed since it was read: {path!r}",
                details={
                    "path": path,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": current.sha256,
                },
            )

        try:
            patched = apply_unified_diff(current.content or "", diff)
        except PatchFormatError as exc:
            raise InvalidArgumentError(str(exc), details={"path": path}) from exc

        if not patched.applied:
            return PatchFileOutput(
                applied=False,
                old_sha256=current.sha256,
                sha256=None,
                rejected_hunks=[
                    RejectedHunkOutput(old_start=h.old_start, reason=h.reason)
                    for h in patched.rejected_hunks
                ],
            )

        # HR-004: pass the sha256 just read through as the write's own
        # `expected_sha256` — closing the read/write window in this
        # function's own optimistic-concurrency guard, independent of
        # whether the *client* supplied one. `ReadResult.sha256` is
        # always the full, untruncated content's hash (see its own
        # docstring), so this is safe even though `current.truncated` is
        # already refused above.
        write_result = svc.write_file(
            path, patched.new_content or "", expected_sha256=current.sha256
        )
        return PatchFileOutput(applied=True, old_sha256=current.sha256, sha256=write_result.sha256)

    @server.tool(name="delete_file", description="Delete a repository file or directory (§13.5).")
    @translate_workspace_errors
    async def delete_file(
        workspace_id: str, path: str, recursive: bool = False
    ) -> DeleteFileOutput:
        service_for(workspace_id).delete_file(path, recursive=recursive)
        return DeleteFileOutput(deleted=True)

    @server.tool(name="search_files", description="Search file names and contents (§13.7).")
    @translate_workspace_errors
    async def search_files(
        workspace_id: str,
        pattern: str,
        mode: SearchMode = "text",
        path: str = "",
        max_results: int | None = None,
        include_ignored: bool = False,
    ) -> list[SearchMatchOutput]:
        root = _resolve_repository_root(db, workspace_id, registry)
        security = FilesystemSecurity(root, authorized_roots=repository_roots)
        limit = filesystem_config.max_search_results
        if max_results is not None:
            limit = min(max_results, limit)
        results = _search_files(
            security,
            pattern=pattern,
            mode=mode,
            path=path,
            max_results=limit,
            include_ignored=include_ignored,
        )
        return [
            SearchMatchOutput(path=m.path, line_number=m.line_number, line_text=m.line_text)
            for m in results
        ]
