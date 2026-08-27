"""`git_status` (§17.2, §34.1) — S20.

Host-side (§20.1): needs the repository root and nothing else — no
Docker, no container, no execution service. `register()` therefore takes
only `db`, unlike every Docker-backed tool module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mcp.server import MCPServer

from dev_workspace_mcp.errors import translate_workspace_errors
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
