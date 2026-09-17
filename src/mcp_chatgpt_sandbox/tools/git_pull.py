"""`git_pull` (§20.5, §18.8, S26) — the structured tool's full body.

Extracted from `tools/git.py` — see `git_commit.py`'s module docstring
for the full rationale behind the split; the same applies here.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

from mcp_chatgpt_sandbox.audit import write_audit_event
from mcp_chatgpt_sandbox.config.models import (
    AuditConfig,
    ContainerConfig,
    ExecutionConfig,
    GithubConfig,
    GitPolicyConfig,
)
from mcp_chatgpt_sandbox.errors import (
    CommandFailedError,
    GitAuthFailedError,
    GitConflictError,
    GitDirtyError,
    GitHeadChangedError,
    GitNonFastForwardError,
    NetworkFailedError,
    WorkspaceNotRunningError,
)
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.git.pull import (
    build_fetch_argv,
    build_integrate_argv,
    is_non_fast_forward_failure,
)
from mcp_chatgpt_sandbox.git.remote import validate_remote_identity
from mcp_chatgpt_sandbox.git.service import detect_interrupted_operation, run_git_in_container
from mcp_chatgpt_sandbox.git.status import head_sha_matches, read_git_status
from mcp_chatgpt_sandbox.git.transport import is_auth_failure, is_network_failure
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.workspace.container_config import resolve_exec_user
from mcp_chatgpt_sandbox.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    get_authorized,
    resolve_repository_root,
)


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


async def run_git_pull(
    workspace_id: str,
    remote: str,
    expected_branch: str | None,
    expected_head_sha: str | None,
    expected_remote_repository: str | None,
    *,
    manager: DockerWorkspaceManager,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    git_policy_config: GitPolicyConfig,
    github_config: GithubConfig,
    audit_config: AuditConfig,
) -> GitPullOutput:
    """§20.5: the `git_pull` tool's full body — module-level for the same
    reason `run_git_commit` (`tools/git_commit.py`) is; see its docstring."""
    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    container_id = record.container_id  # narrowed value, captured for the closure below

    # §20.6 preconditions, host-side, before anything runs in-container
    # or touches the network — same ordering `git_commit` already uses.
    repo_path = resolve_repository_root(db, workspace_id, registry)

    interrupted = detect_interrupted_operation(repo_path)
    if interrupted is not None:
        raise GitConflictError(
            f"a {interrupted.value} is in progress; resolve or abort it before pulling (§44.4)",
            details={"workspace_id": workspace_id, "interrupted_operation": interrupted.value},
        )

    status = await read_git_status(
        repo_path,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        strict_utf8=False,  # G2R-006: only `branch`/`head_sha` are used below
    )

    if expected_branch is not None and status.branch != expected_branch:
        raise GitHeadChangedError(
            f"expected branch {expected_branch!r}, found {status.branch!r} (§45)",
            details={"expected_branch": expected_branch, "actual_branch": status.branch},
        )
    if expected_head_sha is not None and not head_sha_matches(status.head_sha, expected_head_sha):
        raise GitHeadChangedError(
            f"expected HEAD {expected_head_sha!r}, found {status.head_sha!r} (§45)",
            details={
                "expected_head_sha": expected_head_sha,
                "actual_head_sha": status.head_sha,
            },
        )

    if status.branch is None:
        raise GitConflictError(
            "git_pull requires a checked-out branch to integrate into; HEAD is detached (§20.5)",
            details={"workspace_id": workspace_id},
        )
    branch = status.branch
    previous_head_sha = status.head_sha
    strategy = git_policy_config.pull_strategy

    # §22.3/§20.6/AT-019: "potentially destructive operations SHOULD
    # fail when unrelated uncommitted changes exist unless explicitly
    # authorized." `ff_only` (the default) is deliberately exempt —
    # a real fast-forward only ever moves the branch tip forward and
    # preserves non-conflicting uncommitted changes, the same
    # guarantee `git merge --ff-only` itself already gives; `merge`/
    # `rebase` genuinely rewrite history the working tree sits on,
    # which is what makes them the "could overwrite changes"
    # operations this precondition exists for.
    if strategy != "ff_only" and not status.clean:
        raise GitDirtyError(
            f"pull_strategy: {strategy!r} requires a clean working tree; "
            "uncommitted changes are present (§22.3)",
            details={"workspace_id": workspace_id, "strategy": strategy},
        )

    # §20.2: validate the remote before contacting it at all — before
    # the fetch below, not merely before the integration step.
    repository_record = registry.get(record.repository_id)
    remote_repository = await validate_remote_identity(
        remote=remote,
        allowed_url_schemes=git_policy_config.allowed_url_schemes,
        configured_github=repository_record.github or "",
        allowlisted_repositories=github_config.repositories,
        expected_remote_repository=expected_remote_repository,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
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
        # H32: a transport failure underneath fetch gets its own
        # error code — `GIT_AUTH_FAILED`/`NETWORK_FAILED` — instead
        # of always falling through to `COMMAND_FAILED`, mirroring
        # how `is_non_fast_forward_failure` is already checked
        # before the generic case just below.
        if is_auth_failure(fetch_stderr):
            raise GitAuthFailedError(
                f"git fetch failed authentication: {fetch_stderr}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "remote_repository": remote_repository,
                },
            )
        if is_network_failure(fetch_stderr):
            raise NetworkFailedError(
                f"git fetch failed to reach the remote: {fetch_stderr}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "remote_repository": remote_repository,
                },
            )
        raise CommandFailedError(
            f"git fetch failed: {fetch_stderr}",
            details={
                "workspace_id": workspace_id,
                "remote": remote,
                "remote_repository": remote_repository,
            },
        )

    integrate_exit, integrate_stderr = await exec_git(
        build_integrate_argv(strategy, remote=remote, branch=branch)
    )
    if integrate_exit != 0:
        if strategy == "ff_only" and is_non_fast_forward_failure(integrate_stderr):
            raise GitNonFastForwardError(
                f"cannot fast-forward {branch!r} onto {remote}/{branch}: "
                f"{integrate_stderr} (§20.5)",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "branch": branch,
                    "remote_repository": remote_repository,
                },
            )
        raise CommandFailedError(
            f"git {strategy} integration failed: {integrate_stderr}",
            details={
                "workspace_id": workspace_id,
                "remote": remote,
                "branch": branch,
                "strategy": strategy,
                "remote_repository": remote_repository,
            },
        )

    new_status = await read_git_status(
        repo_path,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        strict_utf8=False,  # G2R-006: only `head_sha` is used below
    )
    assert new_status.head_sha is not None  # the integration step just succeeded

    # A real fast-forward, verified rather than inferred from
    # `strategy` alone — plain `merge`/`rebase` can *also* resolve to
    # a fast-forward when there was nothing to actually integrate.
    fast_forward = False
    if previous_head_sha is not None:
        ancestor_check = await run_git_in_container(
            ["merge-base", "--is-ancestor", previous_head_sha, new_status.head_sha],
            container_id=container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=container_config,
            execution_config=execution_config,
            strict_utf8=False,  # G2R-021: none — `--is-ancestor` stdout is always empty
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
