"""`git_status` (§17.2, S20), `git_diff` / `git_log` (§17.3, §46, S21) —
§34.1's host-side structured Git reads — plus `git_commit` (§17.4, §20.6,
§45, §18.9, S22) and `git_pull` (§20.5, §18.8, S26), the structured
*mutating* tools.

The reads are host-side (§20.1): each needs the repository root and
nothing else — no Docker, no container, no execution service.
`git_commit`/`git_pull` run in-container instead, through the same
execution service `exec` uses (§20.1's "not a second execution path with
a second credential") — so `register()` needs `docker_client` and
friends too, unlike the read-only tools it sits alongside. `git_pull`
additionally needs `registry` and `github_config`: §20.2 remote-identity
validation compares the resolved remote against the repository's
configured GitHub identity and the `github.repositories` allowlist,
neither of which a workspace_id alone carries.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field

from mcp.server import MCPServer

import docker
from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    ExecutionConfig,
    GithubConfig,
    GitPolicyConfig,
)
from dev_workspace_mcp.errors import (
    CommandFailedError,
    ContainerRuntimeFailedError,
    GitConflictError,
    GitDirtyError,
    GitHeadChangedError,
    GitNonFastForwardError,
    InvalidArgumentError,
    PolicyDeniedError,
    WorkspaceNotRunningError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.commit import (
    build_add_argv,
    build_commit_argv,
    detect_default_branch,
    is_head_published,
)
from dev_workspace_mcp.git.diff import read_git_diff
from dev_workspace_mcp.git.log import DEFAULT_MAX_LOG_ENTRIES, read_git_log
from dev_workspace_mcp.git.pull import (
    build_fetch_argv,
    build_integrate_argv,
    is_non_fast_forward_failure,
)
from dev_workspace_mcp.git.remote import validate_remote_identity
from dev_workspace_mcp.git.service import detect_interrupted_operation, run_git_host_side
from dev_workspace_mcp.git.status import read_git_status
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    resolve_repository_root,
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


@dataclass
class GitLogEntryOutput:
    sha: str
    author: str
    date: str
    subject: str


@dataclass
class GitCommitOutput:
    commit_sha: str
    branch: str | None
    files_committed: list[str] = field(default_factory=list)


@dataclass
class GitPullOutput:
    remote: str
    branch: str
    remote_repository: str
    strategy: str
    previous_head_sha: str | None
    head_sha: str | None
    fast_forward: bool
    updated: bool


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    registry: RepositoryRegistry,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    git_policy_config: GitPolicyConfig,
    github_config: GithubConfig,
    audit_config: AuditConfig,
) -> None:
    manager = DockerWorkspaceManager(docker_client, db) if docker_client is not None else None
    execution_service = ExecutionService(docker_client) if docker_client is not None else None

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

    @server.tool(
        name="git_commit",
        description="Create a commit from explicit paths, with branch/HEAD guards "
        "and policy checks (§17.4).",
    )
    @translate_workspace_errors
    async def git_commit_tool(
        workspace_id: str,
        message: str,
        paths: list[str],
        expected_branch: str | None = None,
        expected_head_sha: str | None = None,
        strict: bool = False,
        amend: bool = False,
        no_verify: bool = False,
    ) -> GitCommitOutput:
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot commit"
            )
        if not paths:
            raise InvalidArgumentError(
                "git_commit requires at least one explicit path (§17.4) — there is "
                "no separate git_add; exec('git add ...') if you need a broader stage"
            )
        if not message.strip():
            raise InvalidArgumentError("git_commit requires a non-empty message")

        record = manager.get(workspace_id)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING
        container_id = record.container_id  # narrowed value, captured for the closure below

        # §20.6: preconditions, evaluated before the container is asked to
        # do anything, and fail closed — the working tree is bind-mounted,
        # so a host-side read here sees exactly what the container would.
        repo_path = resolve_repository_root(db, workspace_id)

        interrupted = detect_interrupted_operation(repo_path)
        if interrupted is not None:
            raise GitConflictError(
                f"a {interrupted.value} is in progress; resolve or abort it before "
                "committing (§44.4)",
                details={"workspace_id": workspace_id, "interrupted_operation": interrupted.value},
            )

        status = read_git_status(repo_path)

        if expected_branch is not None and status.branch != expected_branch:
            raise GitHeadChangedError(
                f"expected branch {expected_branch!r}, found {status.branch!r} (§45)",
                details={"expected_branch": expected_branch, "actual_branch": status.branch},
            )
        if expected_head_sha is not None and status.head_sha != expected_head_sha:
            raise GitHeadChangedError(
                f"expected HEAD {expected_head_sha!r}, found {status.head_sha!r} (§45)",
                details={
                    "expected_head_sha": expected_head_sha,
                    "actual_head_sha": status.head_sha,
                },
            )

        if strict:
            extraneous = sorted(set(status.staged) - set(paths))
            if extraneous:
                raise GitDirtyError(
                    f"strict mode: paths staged outside the commit request (§20.6): {extraneous}",
                    details={"paths": paths, "extraneous_staged": extraneous},
                )

        default_branch = detect_default_branch(repo_path)
        if (
            default_branch is not None
            and status.branch == default_branch
            and git_policy_config.default_branch_commit == "deny"
        ):
            raise PolicyDeniedError(
                f"default_branch_commit policy denies committing directly on "
                f"{default_branch!r} (§18.9)",
                details={"branch": default_branch, "policy": "default_branch_commit"},
            )

        if amend and git_policy_config.amend_published != "allow" and is_head_published(repo_path):
            raise PolicyDeniedError(
                "amend_published policy denies amending a commit already present "
                "on its upstream branch (§18.9, §65)",
                details={"policy": "amend_published", "upstream": status.upstream},
            )

        user = resolve_exec_user(container_config)

        async def run_git(cmd: list[str], *, failure_prefix: str) -> None:
            exec_id = await asyncio.to_thread(
                execution_service.start,
                container_id,
                cmd,
                cwd=record.workspace_path,
                user=user,
                env=None,
            )
            result = await asyncio.to_thread(
                execution_service.consume,
                exec_id,
                max_output_bytes=execution_config.max_output_bytes,
                response_tail_lines=execution_config.response_tail_lines,
            )
            if result.exit_code != 0:
                raise CommandFailedError(
                    f"{failure_prefix}: {result.stderr_tail}",
                    details={"workspace_id": workspace_id, "paths": paths},
                )

        # Two commands, not one — see git/commit.py's module docstring
        # for why `--only` alone can't stage a genuinely new file.
        await run_git(build_add_argv(paths), failure_prefix="git add failed")
        await run_git(
            build_commit_argv(message=message, paths=paths, amend=amend, no_verify=no_verify),
            failure_prefix="git commit failed",
        )

        new_status = read_git_status(repo_path)
        assert new_status.head_sha is not None  # a commit just succeeded

        write_audit_event(
            audit_config,
            operation="git_commit",
            result="success",
            workspace_id=workspace_id,
            repository_id=record.repository_id,
            metadata={
                "paths": paths,
                "amend": amend,
                "no_verify": no_verify,
                "commit_sha": new_status.head_sha,
            },
        )

        return GitCommitOutput(
            commit_sha=new_status.head_sha, branch=new_status.branch, files_committed=paths
        )

    @server.tool(
        name="git_pull",
        description="Fetch then integrate from a validated remote, per "
        "git_policy.pull_strategy (default ff_only); fails with GIT_NON_FAST_FORWARD "
        "rather than escalating past a non-fast-forward (§20.5).",
    )
    @translate_workspace_errors
    async def git_pull_tool(
        workspace_id: str,
        remote: str = "origin",
        expected_branch: str | None = None,
        expected_head_sha: str | None = None,
        expected_remote_repository: str | None = None,
    ) -> GitPullOutput:
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot pull"
            )

        record = manager.get(workspace_id)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING
        container_id = record.container_id  # narrowed value, captured for the closure below

        # §20.6 preconditions, host-side, before anything runs in-container
        # or touches the network — same ordering `git_commit` already uses.
        repo_path = resolve_repository_root(db, workspace_id)

        interrupted = detect_interrupted_operation(repo_path)
        if interrupted is not None:
            raise GitConflictError(
                f"a {interrupted.value} is in progress; resolve or abort it before pulling (§44.4)",
                details={"workspace_id": workspace_id, "interrupted_operation": interrupted.value},
            )

        status = read_git_status(repo_path)

        if expected_branch is not None and status.branch != expected_branch:
            raise GitHeadChangedError(
                f"expected branch {expected_branch!r}, found {status.branch!r} (§45)",
                details={"expected_branch": expected_branch, "actual_branch": status.branch},
            )
        if expected_head_sha is not None and status.head_sha != expected_head_sha:
            raise GitHeadChangedError(
                f"expected HEAD {expected_head_sha!r}, found {status.head_sha!r} (§45)",
                details={
                    "expected_head_sha": expected_head_sha,
                    "actual_head_sha": status.head_sha,
                },
            )

        if status.branch is None:
            raise GitConflictError(
                "git_pull requires a checked-out branch to integrate into; HEAD "
                "is detached (§20.5)",
                details={"workspace_id": workspace_id},
            )
        branch = status.branch
        previous_head_sha = status.head_sha

        # §20.2: validate the remote before contacting it at all — before
        # the fetch below, not merely before the integration step.
        repository_record = registry.get(record.repository_id)
        remote_repository = validate_remote_identity(
            repo_path,
            remote=remote,
            allowed_url_schemes=git_policy_config.allowed_url_schemes,
            configured_github=repository_record.github or "",
            allowlisted_repositories=github_config.repositories,
            expected_remote_repository=expected_remote_repository,
        )

        user = resolve_exec_user(container_config)

        async def exec_git(cmd: list[str]) -> tuple[int, str]:
            exec_id = await asyncio.to_thread(
                execution_service.start,
                container_id,
                cmd,
                cwd=record.workspace_path,
                user=user,
                env=None,
            )
            result = await asyncio.to_thread(
                execution_service.consume,
                exec_id,
                max_output_bytes=execution_config.max_output_bytes,
                response_tail_lines=execution_config.response_tail_lines,
            )
            return result.exit_code, result.stderr_tail

        # §18.8: the fetch step never recurses into submodules — a
        # `.gitmodules` URL is tracked, container-writable content, and
        # this is what keeps a structured pull from ever contacting a
        # submodule's own remote at all (AT-030).
        fetch_exit, fetch_stderr = await exec_git(build_fetch_argv(remote))
        if fetch_exit != 0:
            raise CommandFailedError(
                f"git fetch failed: {fetch_stderr}",
                details={"workspace_id": workspace_id, "remote": remote},
            )

        strategy = git_policy_config.pull_strategy
        integrate_exit, integrate_stderr = await exec_git(
            build_integrate_argv(strategy, remote=remote, branch=branch)
        )
        if integrate_exit != 0:
            if strategy == "ff_only" and is_non_fast_forward_failure(integrate_stderr):
                raise GitNonFastForwardError(
                    f"cannot fast-forward {branch!r} onto {remote}/{branch}: "
                    f"{integrate_stderr} (§20.5)",
                    details={"workspace_id": workspace_id, "remote": remote, "branch": branch},
                )
            raise CommandFailedError(
                f"git {strategy} integration failed: {integrate_stderr}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "branch": branch,
                    "strategy": strategy,
                },
            )

        new_status = read_git_status(repo_path)
        assert new_status.head_sha is not None  # the integration step just succeeded

        # A real fast-forward, verified rather than inferred from
        # `strategy` alone — plain `merge`/`rebase` can *also* resolve to
        # a fast-forward when there was nothing to actually integrate.
        fast_forward = False
        if previous_head_sha is not None:
            ancestor_check = run_git_host_side(
                repo_path, ["merge-base", "--is-ancestor", previous_head_sha, new_status.head_sha]
            )
            fast_forward = ancestor_check.ok

        write_audit_event(
            audit_config,
            operation="git_pull",
            result="success",
            workspace_id=workspace_id,
            repository_id=record.repository_id,
            metadata={
                "remote": remote,
                "remote_repository": remote_repository,
                "branch": branch,
                "strategy": strategy,
                "previous_head_sha": previous_head_sha,
                "head_sha": new_status.head_sha,
            },
        )

        return GitPullOutput(
            remote=remote,
            branch=branch,
            remote_repository=remote_repository,
            strategy=strategy,
            previous_head_sha=previous_head_sha,
            head_sha=new_status.head_sha,
            fast_forward=fast_forward,
            updated=new_status.head_sha != previous_head_sha,
        )
