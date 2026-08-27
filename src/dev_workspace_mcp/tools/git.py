"""`git_status` (§17.2, S20), `git_diff` / `git_log` (§17.3, §46, S21) —
§34.1's host-side structured Git reads.

Host-side (§20.1): each needs the repository root and nothing else — no
Docker, no container, no execution service. `register()` therefore takes
only `db`, unlike every Docker-backed tool module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mcp.server import MCPServer

from dev_workspace_mcp.errors import translate_workspace_errors
from dev_workspace_mcp.git.diff import read_git_diff
from dev_workspace_mcp.git.log import DEFAULT_MAX_LOG_ENTRIES, read_git_log
from dev_workspace_mcp.git.status import read_git_status
from dev_workspace_mcp.workspace.manager import resolve_repository_root


@dataclass
class GitStatusOutput:
    branch: str | None
    head_sha: str | None
    upstream: str | None
    ahead: int
    behind: int
    detached_head: bool
    clean: bool
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicted: list[str] = field(default_factory=list)
    interrupted_operation: str | None = None


@dataclass
class GitDiffOutput:
    content: str
    truncated: bool
    total_bytes: int


@dataclass
class GitLogEntryOutput:
    sha: str
    author: str
    date: str
    subject: str


def register(server: MCPServer, *, db: sqlite3.Connection) -> None:
    @server.tool(
        name="git_status",
        description="Structured repository status: branch, head, ahead/behind, "
        "staged/unstaged/untracked/conflicted paths (§17.2).",
    )
    @translate_workspace_errors
    async def git_status_tool(workspace_id: str) -> GitStatusOutput:
        # Read-only, host-side, never touches the container — deliberately
        # does not call `touch_last_used` (§8.1's idle-stop is measured
        # against container usage; this tool never uses the container).
        repo_path = resolve_repository_root(db, workspace_id)
        status = read_git_status(repo_path)
        return GitStatusOutput(
            branch=status.branch,
            head_sha=status.head_sha,
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            detached_head=status.detached_head,
            clean=status.clean,
            staged=status.staged,
            unstaged=status.unstaged,
            untracked=status.untracked,
            conflicted=status.conflicted,
            interrupted_operation=(
                status.interrupted_operation.value
                if status.interrupted_operation is not None
                else None
            ),
        )

    @server.tool(
        name="git_diff",
        description="Working-tree, staged, or commit-to-commit diff; path-filtered, "
        "stat-only mode, bounded output (§17.3).",
    )
    @translate_workspace_errors
    async def git_diff_tool(
        workspace_id: str,
        staged: bool = False,
        base: str | None = None,
        head: str | None = None,
        paths: list[str] | None = None,
        stat_only: bool = False,
        max_bytes: int | None = None,
        offset: int = 0,
    ) -> GitDiffOutput:
        repo_path = resolve_repository_root(db, workspace_id)
        result = read_git_diff(
            repo_path,
            staged=staged,
            base=base,
            head=head,
            paths=paths,
            stat_only=stat_only,
            max_bytes=max_bytes,
            offset=offset,
        )
        return GitDiffOutput(
            content=result.content, truncated=result.truncated, total_bytes=result.total_bytes
        )

    @server.tool(name="git_log", description="Bounded commit history (§17.3).")
    @translate_workspace_errors
    async def git_log_tool(
        workspace_id: str,
        max_entries: int = DEFAULT_MAX_LOG_ENTRIES,
        paths: list[str] | None = None,
        ref: str | None = None,
    ) -> list[GitLogEntryOutput]:
        repo_path = resolve_repository_root(db, workspace_id)
        entries = read_git_log(repo_path, max_entries=max_entries, paths=paths, ref=ref)
        return [
            GitLogEntryOutput(sha=e.sha, author=e.author, date=e.date, subject=e.subject)
            for e in entries
        ]
