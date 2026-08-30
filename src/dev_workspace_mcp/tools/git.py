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

This module is now the thin hub, not where every tool body lives: each
mutating tool's full body (and its own module docstring explaining the
split) is in its own file — `git_commit.py`, `git_pull.py`, `git_push.py`
— and the three read tools share `git_read.py`. What stays here is
`audit_denials`, `register()` itself, and a re-export of every name the
four submodules define, so `dev_workspace_mcp.tools.git.run_git_commit`
(etc.) resolves exactly as it did before the split — a pure move, no
behavior change. Split because this file alone was 1662 lines, well past
every other `tools/*.py` module's size.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Literal

import docker
from mcp.server import MCPServer

from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    ExecutionConfig,
    GithubConfig,
    GitPolicyConfig,
)
from dev_workspace_mcp.errors import (
    ContainerRuntimeFailedError,
    ErrorCode,
    WorkspaceError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.log import DEFAULT_MAX_LOG_ENTRIES
from dev_workspace_mcp.logging_config import redacted_extra
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.tools.git_commit import GitCommitOutput, run_git_commit
from dev_workspace_mcp.tools.git_pull import GitPullOutput, run_git_pull
from dev_workspace_mcp.tools.git_push import (
    GitPushOutput,
    load_stored_remote_protection,
    run_git_push,
)
from dev_workspace_mcp.tools.git_read import (
    GitDiffOutput,
    GitLogEntryOutput,
    GitStatusOutput,
    run_git_diff,
    run_git_log,
    run_git_status,
)
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager

__all__ = [
    "GitCommitOutput",
    "GitDiffOutput",
    "GitLogEntryOutput",
    "GitPullOutput",
    "GitPushOutput",
    "GitStatusOutput",
    "audit_denials",
    "load_stored_remote_protection",
    "register",
    "run_git_commit",
    "run_git_diff",
    "run_git_log",
    "run_git_pull",
    "run_git_push",
    "run_git_status",
]

#: HR-015 (`docs/HARDENING_SPEC.md` §8): a denial record SHALL carry the
#: same operation-identifying fields a success record does, "where
#: applicable" — these are exactly the ones raise sites populate
#: opportunistically in their own `details` (only when actually known at
#: that point; e.g. `remote_repository` is absent from every denial raised
#: before remote validation has run, which is the correct, honest gap).
_DENIAL_DETAIL_FIELDS = ("policy", "remote", "branch", "remote_repository")

_logger = logging.getLogger(__name__)


def audit_denials(
    operation: str, *, audit_config: AuditConfig, manager: DockerWorkspaceManager | None
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
    signature here) rather than threaded through separately.

    HR-015: a denial record SHALL additionally carry `repository_id` and,
    where applicable, `remote`/`branch`/`remote_repository` — matching a
    success record's fidelity (§20.7). `repository_id` is resolved via
    `manager` (best-effort: a workspace lookup can itself fail, e.g. for
    an already-nonexistent `workspace_id`, in which case the *original*
    denial is still reported, just without it) rather than threaded
    through every raise site. The other three come from whichever of
    `exc.details` (populated at the raise site when already known there)
    or the tool's own kwargs (the caller-supplied value, for the common
    case where the raise happens before that value was ever resolved
    further) has it.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except WorkspaceError as exc:
                workspace_id = kwargs.get("workspace_id")

                repository_id = exc.details.get("repository_id")
                if repository_id is None and manager is not None and workspace_id is not None:
                    # best-effort only — the original denial still gets
                    # audited even if this secondary lookup itself fails
                    # (e.g. workspace_id doesn't resolve to anything)
                    with contextlib.suppress(WorkspaceError):
                        repository_id = manager.get(workspace_id).repository_id

                metadata: dict[str, Any] = {
                    "policy_decision": ("deny" if exc.code == ErrorCode.POLICY_DENIED else "n/a"),
                    "error_code": exc.code.value,
                }
                for key in _DENIAL_DETAIL_FIELDS:
                    if key in exc.details:
                        metadata[key] = exc.details[key]
                    elif key in kwargs and kwargs[key] is not None:
                        metadata[key] = kwargs[key]

                write_audit_event(
                    audit_config,
                    operation=operation,
                    result="denied" if exc.code == ErrorCode.POLICY_DENIED else "failure",
                    workspace_id=workspace_id,
                    repository_id=repository_id,
                    metadata=metadata,
                )
                # H31 (`docs/HARDENING_TODO.md`, HR-031): the audit event
                # above is the client-operation-facing record (§24); this
                # is the same denial's operational-visibility half —
                # `redacted_extra` routes it through the identical
                # `redact_secrets` HR-013 already applies to a
                # client-facing error and an audit record, since
                # `metadata` can carry a client-supplied `remote`/`branch`.
                _logger.warning(
                    "workspace %s: %s %s (%s)",
                    workspace_id,
                    operation,
                    "denied" if exc.code == ErrorCode.POLICY_DENIED else "failed",
                    redacted_extra(metadata),
                )
                raise

        return wrapper

    return decorator


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
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot report status"
            )
        return await run_git_status(
            workspace_id,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
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
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot diff"
            )
        return await run_git_diff(
            workspace_id,
            staged,
            base,
            head,
            paths,
            stat_only,
            max_bytes,
            offset,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
        )

    @server.tool(name="git_log", description="Bounded commit history (§17.3).")
    @translate_workspace_errors
    async def git_log_tool(
        workspace_id: str,
        max_entries: int = DEFAULT_MAX_LOG_ENTRIES,
        paths: list[str] | None = None,
        ref: str | None = None,
    ) -> list[GitLogEntryOutput]:
        if manager is None or execution_service is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot log"
            )
        return await run_git_log(
            workspace_id,
            max_entries,
            paths,
            ref,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
        )

    @server.tool(
        name="git_commit",
        description="Create a commit from explicit paths, with branch/HEAD guards "
        "and policy checks (§17.4).",
    )
    @translate_workspace_errors
    @audit_denials("git_commit", audit_config=audit_config, manager=manager)
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
        return await run_git_commit(
            workspace_id,
            message,
            paths,
            expected_branch,
            expected_head_sha,
            strict,
            amend,
            no_verify,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
            git_policy_config=git_policy_config,
            audit_config=audit_config,
        )

    @server.tool(
        name="git_pull",
        description="Fetch then integrate from a validated remote, per "
        "git_policy.pull_strategy (default ff_only); fails with GIT_NON_FAST_FORWARD "
        "rather than escalating past a non-fast-forward (§20.5).",
    )
    @translate_workspace_errors
    @audit_denials("git_pull", audit_config=audit_config, manager=manager)
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
        return await run_git_pull(
            workspace_id,
            remote,
            expected_branch,
            expected_head_sha,
            expected_remote_repository,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
            git_policy_config=git_policy_config,
            github_config=github_config,
            audit_config=audit_config,
        )

    @server.tool(
        name="git_push",
        description="Push to a validated remote with a daemon-constructed refspec — "
        "never a client-supplied one (§20.3). force/force_with_lease/delete are each "
        "governed by their own git_policy key, refused before Git is invoked.",
    )
    @translate_workspace_errors
    @audit_denials("git_push", audit_config=audit_config, manager=manager)
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
        return await run_git_push(
            workspace_id,
            remote,
            branch,
            expected_remote_repository,
            expected_local_head_sha,
            force,
            force_with_lease,
            delete,
            ref_type,
            no_verify,
            manager=manager,
            execution_service=execution_service,
            registry=registry,
            db=db,
            container_config=container_config,
            execution_config=execution_config,
            git_policy_config=git_policy_config,
            github_config=github_config,
            audit_config=audit_config,
        )
