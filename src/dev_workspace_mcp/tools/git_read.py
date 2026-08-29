"""`git_status` (§17.2, S20), `git_diff` / `git_log` (§17.3, §46, S21) —
the three structured *read* tools' full bodies.

Extracted from `tools/git.py` — see `git_commit.py`'s module docstring
for the full rationale behind the split; the same applies here. The
three read tools stay together in one module (unlike `git_commit`/
`git_pull`/`git_push`, each in their own) since each is small — bundling
them serves the same "no file holds too much" goal without three
near-empty files.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from dev_workspace_mcp.config.models import ContainerConfig, ExecutionConfig
from dev_workspace_mcp.errors import WorkspaceNotRunningError
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.diff import read_git_diff
from dev_workspace_mcp.git.log import read_git_log
from dev_workspace_mcp.git.status import read_git_status
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    get_authorized,
    resolve_repository_root,
    touch_last_used,
)


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
    next_offset: int


@dataclass
class GitLogEntryOutput:
    sha: str
    author: str
    date: str
    subject: str


async def run_git_status(
    workspace_id: str,
    *,
    manager: DockerWorkspaceManager,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> GitStatusOutput:
    """§17.2: the `git_status` tool's full body — module-level (R27,
    `docs/REMEDIATION_TODO.md`), the same pattern `run_git_commit`/
    `run_git_pull`/`run_git_push` already use. Pre-H36 this was a
    thin pass-through R27 deliberately left alone; H36
    (`docs/HARDENING_TODO.md`) moved the read in-container, which means
    it now needs a `WorkspaceRecord` (state, container id) the way every
    other in-container tool does — no longer a trivial wrapper.
    `register()`'s `git_status_tool` is a thin wrapper: check Docker
    reachability, then call this."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    # §8.1: post-H36 this reads through the container, same as every
    # other in-container Git tool — real usage, not idle.
    touch_last_used(db, workspace_id)

    repo_path = resolve_repository_root(db, workspace_id, registry)
    status = await read_git_status(
        repo_path,
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
    )
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
            status.interrupted_operation.value if status.interrupted_operation is not None else None
        ),
    )


async def run_git_diff(
    workspace_id: str,
    staged: bool,
    base: str | None,
    head: str | None,
    paths: list[str] | None,
    stat_only: bool,
    max_bytes: int | None,
    offset: int,
    *,
    manager: DockerWorkspaceManager,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> GitDiffOutput:
    """§17.3: the `git_diff` tool's full body — module-level (R27), the
    same reason `run_git_status` above is. `register()`'s `git_diff_tool`
    is a thin wrapper: check Docker reachability, then call this."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    touch_last_used(db, workspace_id)  # §8.1: post-H36, real container usage

    repo_path = resolve_repository_root(db, workspace_id, registry)
    result = await read_git_diff(
        repo_path,
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        staged=staged,
        base=base,
        head=head,
        paths=paths,
        stat_only=stat_only,
        max_bytes=max_bytes,
        offset=offset,
    )
    return GitDiffOutput(
        content=result.content,
        truncated=result.truncated,
        total_bytes=result.total_bytes,
        next_offset=result.next_offset,
    )


async def run_git_log(
    workspace_id: str,
    max_entries: int,
    paths: list[str] | None,
    ref: str | None,
    *,
    manager: DockerWorkspaceManager,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> list[GitLogEntryOutput]:
    """§17.3: the `git_log` tool's full body — module-level (R27), the
    same reason `run_git_status`/`run_git_diff` above are. `register()`'s
    `git_log_tool` is a thin wrapper: check Docker reachability, then
    call this."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    touch_last_used(db, workspace_id)  # §8.1: post-H36, real container usage

    repo_path = resolve_repository_root(db, workspace_id, registry)
    entries = await read_git_log(
        repo_path,
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        max_entries=max_entries,
        paths=paths,
        ref=ref,
    )
    return [
        GitLogEntryOutput(sha=e.sha, author=e.author, date=e.date, subject=e.subject)
        for e in entries
    ]
