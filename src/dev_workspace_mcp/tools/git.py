"""`git_status` (§17.2, S20), `git_diff` / `git_log` (§17.3, §46, S21) —
§34.1's host-side structured Git reads — plus `git_commit` (§17.4, §20.6,
§45, §18.9, S22), `git_pull` (§20.5, §18.8, S26), and `git_push` (§20.3,
§20.5, §18.9, §20.6, S27), the structured *mutating* tools.

The reads are host-side (§20.1): each needs the repository root and
nothing else — no Docker, no container, no execution service.
`git_commit`/`git_pull`/`git_push` run in-container instead, through the
same execution service `exec` uses (§20.1's "not a second execution path
with a second credential") — so `register()` needs `docker_client` and
friends too, unlike the read-only tools it sits alongside. `git_pull`/
`git_push` additionally need `registry` and `github_config`: §20.2
remote-identity validation compares the resolved remote against the
repository's configured GitHub identity and the `github.repositories`
allowlist, neither of which a workspace_id alone carries.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Literal

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
    ErrorCode,
    GitConflictError,
    GitDirtyError,
    GitHeadChangedError,
    GitNonFastForwardError,
    InvalidArgumentError,
    PolicyDeniedError,
    WorkspaceError,
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
from dev_workspace_mcp.git.push import (
    build_force_with_lease_arg,
    build_push_argv,
    build_refspec,
    is_push_non_fast_forward_failure,
    validate_branch_name,
)
from dev_workspace_mcp.git.remote import validate_remote_identity
from dev_workspace_mcp.git.service import detect_interrupted_operation, run_git_host_side
from dev_workspace_mcp.git.status import read_git_status
from dev_workspace_mcp.github_api import (
    RemoteProtectionReportOutput,
    RemoteProtectionResult,
    deserialize_remote_protection,
    to_report_output,
)
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    resolve_repository_root,
)


def audit_denials(
    operation: str, *, audit_config: AuditConfig
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """§20.7 / §24: "denials SHALL be audited with the same fidelity as
    successes." Stacks directly *below* `@translate_workspace_errors` (so
    it sees the raw `WorkspaceError`, before that decorator converts it
    into the SDK's `ToolError`) on every structured Git tool — one audit
    record per failure, instead of one hand-written `write_audit_event`
    call at each of the many raise sites in `git_commit_tool`/
    `git_pull_tool`/`git_push_tool`. `workspace_id` is read from the
    tool's own call kwargs (the MCP framework invokes tool functions with
    validated arguments as keywords, matching every `git_*_tool`
    signature here) rather than threaded through separately."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except WorkspaceError as exc:
                write_audit_event(
                    audit_config,
                    operation=operation,
                    result="denied" if exc.code == ErrorCode.POLICY_DENIED else "failure",
                    workspace_id=kwargs.get("workspace_id"),
                    metadata={
                        "policy_decision": (
                            "deny" if exc.code == ErrorCode.POLICY_DENIED else "n/a"
                        ),
                        "error_code": exc.code.value,
                    },
                )
                raise

        return wrapper

    return decorator


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


@dataclass
class GitPushOutput:
    remote: str
    remote_repository: str
    branch: str
    ref_type: str
    pushed_sha: str | None
    deleted: bool
    fast_forward: bool
    remote_protection: RemoteProtectionReportOutput


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

    def _load_stored_remote_protection(repository_id: str) -> RemoteProtectionResult | None:
        row = db.execute(
            "SELECT remote_protection FROM repository WHERE id = ?", (repository_id,)
        ).fetchone()
        return deserialize_remote_protection(row["remote_protection"]) if row is not None else None

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
    @audit_denials("git_commit", audit_config=audit_config)
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
                "policy_decision": "allow",
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
    @audit_denials("git_pull", audit_config=audit_config)
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
                "policy_decision": "allow",
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

    @server.tool(
        name="git_push",
        description="Push to a validated remote with a daemon-constructed refspec — "
        "never a client-supplied one (§20.3). force/force_with_lease/delete are each "
        "governed by their own git_policy key, refused before Git is invoked.",
    )
    @translate_workspace_errors
    @audit_denials("git_push", audit_config=audit_config)
    async def git_push_tool(
        workspace_id: str,
        remote: str = "origin",
        branch: str | None = None,
        expected_remote_repository: str | None = None,
        expected_local_head_sha: str | None = None,
        force: bool = False,
        force_with_lease: bool = False,
        delete: bool = False,
        ref_type: Literal["branch", "tag"] = "branch",
        no_verify: bool = False,
    ) -> GitPushOutput:
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot push"
            )
        if force and force_with_lease:
            raise InvalidArgumentError(
                "force and force_with_lease are mutually exclusive (§20.3, AT-029)",
                details={"force": force, "force_with_lease": force_with_lease},
            )
        if delete and expected_local_head_sha is not None:
            raise InvalidArgumentError(
                "expected_local_head_sha guards the commit being pushed; it does "
                "not apply to delete: true, which pushes nothing",
                details={"delete": delete},
            )

        record = manager.get(workspace_id)
        if record.state != WorkspaceState.RUNNING:
            raise WorkspaceNotRunningError(
                f"workspace {workspace_id!r} is not running (state: {record.state.value})",
                details={"workspace_id": workspace_id, "state": record.state.value},
            )
        assert record.container_id is not None  # implied by state == RUNNING
        container_id = record.container_id  # narrowed value, captured for the closure below

        repo_path = resolve_repository_root(db, workspace_id)

        interrupted = detect_interrupted_operation(repo_path)
        if interrupted is not None:
            raise GitConflictError(
                f"a {interrupted.value} is in progress; resolve or abort it before pushing (§44.4)",
                details={"workspace_id": workspace_id, "interrupted_operation": interrupted.value},
            )

        status = read_git_status(repo_path)
        target_branch = branch if branch is not None else status.branch
        if target_branch is None:
            raise InvalidArgumentError(
                "git_push requires an explicit 'branch' when HEAD is detached",
                details={"workspace_id": workspace_id},
            )
        validate_branch_name(target_branch)

        if delete:
            local_head_sha = None
        elif target_branch == status.branch:
            local_head_sha = status.head_sha
        else:
            rev = run_git_host_side(
                repo_path, ["rev-parse", "--verify", "-q", f"refs/heads/{target_branch}"]
            )
            if not rev.ok:
                raise InvalidArgumentError(
                    f"local branch {target_branch!r} does not exist",
                    details={"branch": target_branch},
                )
            local_head_sha = rev.stdout.strip()

        # §18.9 policy, evaluated entirely before Git is invoked or the
        # remote is even validated (AT-011) — `require_approval` is a
        # hard denial in this release (§42: no interactive-approval
        # mechanism exists yet).
        def _denied(policy: str) -> bool:
            return policy != "allow"

        if _denied(git_policy_config.push):
            raise PolicyDeniedError(
                "git_policy.push denies git_push entirely (§18.9)",
                details={"policy": "push", "value": git_policy_config.push},
            )

        default_branch = detect_default_branch(repo_path)
        is_default_branch = default_branch is not None and target_branch == default_branch
        if is_default_branch:
            default_branch_push = git_policy_config.default_branch_push
            if default_branch_push == "deny" or default_branch_push == "require_approval":
                raise PolicyDeniedError(
                    f"default_branch_push policy denies pushing to {default_branch!r} (§18.9)",
                    details={"branch": default_branch, "policy": "default_branch_push"},
                )
            if default_branch_push == "allow_fast_forward" and (force or force_with_lease):
                raise PolicyDeniedError(
                    f"default_branch_push: allow_fast_forward permits only a "
                    f"non-force push to {default_branch!r} (§18.9)",
                    details={"branch": default_branch, "policy": "default_branch_push"},
                )

        if force and _denied(git_policy_config.force_push):
            raise PolicyDeniedError(
                "force_push policy denies force: true (§18.9)",
                details={"policy": "force_push", "value": git_policy_config.force_push},
            )
        if force_with_lease and _denied(git_policy_config.force_with_lease):
            raise PolicyDeniedError(
                "force_with_lease policy denies force_with_lease: true (§18.9)",
                details={"policy": "force_with_lease", "value": git_policy_config.force_with_lease},
            )
        if delete:
            delete_policy = (
                git_policy_config.delete_remote_branch
                if ref_type == "branch"
                else git_policy_config.delete_remote_tag
            )
            policy_name = "delete_remote_branch" if ref_type == "branch" else "delete_remote_tag"
            if _denied(delete_policy):
                raise PolicyDeniedError(
                    f"{policy_name} policy denies delete: true (§18.9)",
                    details={"policy": policy_name, "value": delete_policy},
                )

        # §20.3: a force push still carries a state guard — required once
        # policy has actually permitted it, not merely requested.
        if (force or force_with_lease) and expected_local_head_sha is None:
            raise InvalidArgumentError(
                "expected_local_head_sha is required when force or "
                "force_with_lease is used (§20.3)",
                details={"force": force, "force_with_lease": force_with_lease},
            )
        if (
            expected_local_head_sha is not None
            and not delete
            and expected_local_head_sha != local_head_sha
        ):
            raise GitHeadChangedError(
                f"expected local HEAD {expected_local_head_sha!r} for "
                f"{target_branch!r}, found {local_head_sha!r} (§45)",
                details={
                    "expected_local_head_sha": expected_local_head_sha,
                    "actual_local_head_sha": local_head_sha,
                },
            )

        # §20.2: validate the remote before contacting it at all.
        repository_record = registry.get(record.repository_id)
        remote_repository = validate_remote_identity(
            repo_path,
            remote=remote,
            allowed_url_schemes=git_policy_config.allowed_url_schemes,
            configured_github=repository_record.github or "",
            allowlisted_repositories=github_config.repositories,
            expected_remote_repository=expected_remote_repository,
        )

        refspec = build_refspec(
            target_branch,
            ref_type=ref_type,
            force=force,
            delete=delete,
        )
        force_with_lease_arg = (
            build_force_with_lease_arg(repo_path, remote=remote, branch=target_branch)
            if force_with_lease
            else None
        )
        argv = build_push_argv(remote, refspec, force_with_lease_arg=force_with_lease_arg)
        if no_verify:
            argv.insert(argv.index("push") + 1, "--no-verify")

        user = resolve_exec_user(container_config)
        exec_id = await asyncio.to_thread(
            execution_service.start,
            container_id,
            argv,
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
            if not delete and not force and is_push_non_fast_forward_failure(result.stderr_tail):
                raise GitNonFastForwardError(
                    f"push rejected: {target_branch!r} has diverged from "
                    f"{remote}/{target_branch}: {result.stderr_tail} (§20.5)",
                    details={
                        "workspace_id": workspace_id,
                        "remote": remote,
                        "branch": target_branch,
                    },
                )
            raise CommandFailedError(
                f"git push failed: {result.stderr_tail}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "branch": target_branch,
                    "delete": delete,
                },
            )

        write_audit_event(
            audit_config,
            operation="git_push",
            result="success",
            workspace_id=workspace_id,
            repository_id=record.repository_id,
            metadata={
                "policy_decision": "allow",
                "remote": remote,
                "remote_repository": remote_repository,
                "branch": target_branch,
                "ref_type": ref_type,
                "pushed_sha": local_head_sha,
                "deleted": delete,
                "force": force,
                "force_with_lease": force_with_lease,
            },
        )

        return GitPushOutput(
            remote=remote,
            remote_repository=remote_repository,
            branch=target_branch,
            ref_type=ref_type,
            pushed_sha=local_head_sha,
            deleted=delete,
            fast_forward=not force and not force_with_lease and not delete,
            # §20.4: the *last* `workspace_open` verification, not a fresh
            # re-query — §20.4 only says a push MAY re-verify, and there is
            # no live credential to meaningfully re-verify against yet
            # (D2); reporting the cached value is honest and cheap.
            remote_protection=to_report_output(
                _load_stored_remote_protection(record.repository_id)
            ),
        )
