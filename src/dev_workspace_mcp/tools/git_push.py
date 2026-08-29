"""`git_push` (§20.3, §20.5, §18.9, §20.6, S27) — the structured tool's
full body.

Extracted from `tools/git.py` — see `git_commit.py`'s module docstring
for the full rationale behind the split; the same applies here.
`load_stored_remote_protection` moved here too, alongside its only
in-module caller — `tools/workspace.py` has its own separate copy for
`workspace_info`'s diagnostic block, pre-existing duplication this split
does not touch.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Literal

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
    GitAuthFailedError,
    GitConflictError,
    GitHeadChangedError,
    GitNonFastForwardError,
    GitRemoteNotAllowedError,
    InvalidArgumentError,
    NetworkFailedError,
    PolicyDeniedError,
    WorkspaceNotRunningError,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.commit import detect_default_branch
from dev_workspace_mcp.git.push import (
    build_force_with_lease_arg,
    build_push_argv,
    build_refspec,
    is_push_non_fast_forward_failure,
    validate_branch_name,
)
from dev_workspace_mcp.git.remote import (
    build_get_push_url_argv,
    normalize_to_owner_repo,
    validate_remote_identity_for_push,
)
from dev_workspace_mcp.git.service import detect_interrupted_operation, run_git_in_container
from dev_workspace_mcp.git.status import read_git_status
from dev_workspace_mcp.git.transport import is_auth_failure, is_network_failure
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
    get_authorized,
    resolve_repository_root,
)


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


def load_stored_remote_protection(
    db: sqlite3.Connection, repository_id: str
) -> RemoteProtectionResult | None:
    row = db.execute(
        "SELECT remote_protection FROM repository WHERE id = ?", (repository_id,)
    ).fetchone()
    return deserialize_remote_protection(row["remote_protection"]) if row is not None else None


async def run_git_push(
    workspace_id: str,
    remote: str,
    branch: str | None,
    expected_remote_repository: str | None,
    expected_local_head_sha: str | None,
    force: bool,
    force_with_lease: bool,
    delete: bool,
    ref_type: Literal["branch", "tag"],
    no_verify: bool,
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
) -> GitPushOutput:
    """§20.3: the `git_push` tool's full body — module-level for the same
    reason `run_git_commit` (`tools/git_commit.py`) is; see its docstring."""
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

    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    container_id = record.container_id  # narrowed value, captured for the closure below

    repo_path = resolve_repository_root(db, workspace_id, registry)

    interrupted = detect_interrupted_operation(repo_path)
    if interrupted is not None:
        raise GitConflictError(
            f"a {interrupted.value} is in progress; resolve or abort it before pushing (§44.4)",
            details={"workspace_id": workspace_id, "interrupted_operation": interrupted.value},
        )

    status = await read_git_status(
        repo_path,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
    )
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
        rev = await run_git_in_container(
            ["rev-parse", "--verify", "-q", f"refs/heads/{target_branch}"],
            container_id=container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=container_config,
            execution_config=execution_config,
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
            details={
                "policy": "push",
                "value": git_policy_config.push,
                "branch": target_branch,
            },
        )

    default_branch = detect_default_branch(repo_path)
    is_default_branch = default_branch is not None and target_branch == default_branch
    if is_default_branch:
        default_branch_push = git_policy_config.default_branch_push
        # HR-022: fail-closed (only "allow"/"allow_fast_forward" are
        # non-denying), not an enumeration of the known deny values
        # — the same shape `amend_published` (above) already uses.
        # Enumerating `"deny" or "require_approval"` explicitly would
        # silently *permit* the push if `default_branch_push` were
        # ever set to some other, unrecognized value instead of
        # correctly refusing it.
        if default_branch_push not in ("allow", "allow_fast_forward"):
            raise PolicyDeniedError(
                f"default_branch_push policy denies pushing to {default_branch!r} (§18.9)",
                details={
                    "branch": default_branch,
                    "policy": "default_branch_push",
                    "value": default_branch_push,
                },
            )
        if default_branch_push == "allow_fast_forward" and (force or force_with_lease):
            raise PolicyDeniedError(
                f"default_branch_push: allow_fast_forward permits only a "
                f"non-force push to {default_branch!r} (§18.9)",
                details={
                    "branch": default_branch,
                    "policy": "default_branch_push",
                    "value": default_branch_push,
                },
            )

    if force and _denied(git_policy_config.force_push):
        raise PolicyDeniedError(
            "force_push policy denies force: true (§18.9)",
            details={
                "policy": "force_push",
                "value": git_policy_config.force_push,
                "branch": target_branch,
            },
        )
    if force_with_lease and _denied(git_policy_config.force_with_lease):
        raise PolicyDeniedError(
            "force_with_lease policy denies force_with_lease: true (§18.9)",
            details={
                "policy": "force_with_lease",
                "value": git_policy_config.force_with_lease,
                "branch": target_branch,
            },
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
                details={
                    "policy": policy_name,
                    "value": delete_policy,
                    "branch": target_branch,
                },
            )

    # §20.3: a force push still carries a state guard — required once
    # policy has actually permitted it, not merely requested.
    if (force or force_with_lease) and expected_local_head_sha is None:
        raise InvalidArgumentError(
            "expected_local_head_sha is required when force or force_with_lease is used (§20.3)",
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

    # §20.2/HR-014: validate the remote before contacting it at all —
    # the *push* URL specifically (`validate_remote_identity_for_push`),
    # since `git push` can resolve to a different destination than
    # the plain fetch URL `validate_remote_identity` alone checks
    # (`remote.<name>.pushurl`/`url.<base>.pushInsteadOf`).
    repository_record = registry.get(record.repository_id)
    validated_remote = await validate_remote_identity_for_push(
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
    remote_repository = validated_remote.repository

    refspec = build_refspec(
        target_branch,
        ref_type=ref_type,
        force=force,
        delete=delete,
    )
    force_with_lease_arg = (
        await build_force_with_lease_arg(
            remote=remote,
            branch=target_branch,
            container_id=container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=container_config,
            execution_config=execution_config,
        )
        if force_with_lease
        else None
    )
    user = resolve_exec_user(container_config)

    # RR-002 (`docs/REMEDIATION_SPEC.md` §5): `validate_remote_identity_for_push`
    # above only establishes what the *host*, reading `.git/config`
    # with a clean environment, resolves the push destination to —
    # it says nothing about what the *container's own* `git` would
    # actually use, and `url.<base>.pushInsteadOf` rewrites a literal
    # URL argument exactly as readily as a named remote (verified
    # empirically), so simply pushing to the validated URL instead of
    # the remote name does not by itself close the gap. This
    # re-resolves the push URL *in-container*, as the container user
    # — pure local configuration resolution, no network contact
    # (verified empirically: instant, even against an unresolvable
    # hostname) — and refuses before ever invoking `git push` if the
    # container's own view disagrees with what was already approved.
    #
    # Compared by *normalized identity*, not raw string: S24's own
    # `entrypoint.sh` provisions a trusted, root-owned, system-scope
    # `url."git@github.com:".insteadOf "https://github.com/"` so an
    # HTTPS-authored origin pushes over the container's actual SSH
    # key — a real, benign difference between the host's clean-env
    # resolution (which deliberately blanks `GIT_CONFIG_SYSTEM` too,
    # since the *host's own* `/etc/gitconfig` has nothing to do with
    # this container) and the container's. Raw string equality
    # treated that as a mismatch; comparing `owner/repo` identity
    # accepts the legitimate rewrite while still catching a redirect
    # to an unauthorized host or repository, since only a recognized
    # GitHub URL form normalizes to a non-`None` identity at all.
    check_exec_id = await asyncio.to_thread(
        execution_service.start,
        container_id,
        build_get_push_url_argv(remote),
        cwd=record.workspace_path,
        user=user,
        env=None,
    )
    check_result = await asyncio.to_thread(
        execution_service.consume,
        check_exec_id,
        max_output_bytes=execution_config.max_output_bytes,
        response_tail_lines=execution_config.response_tail_lines,
    )
    in_container_push_url = check_result.stdout_tail.strip()
    in_container_repository = (
        normalize_to_owner_repo(in_container_push_url) if check_result.exit_code == 0 else None
    )
    if in_container_repository != validated_remote.repository:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} resolves to a different push destination "
            "inside the container than the daemon validated host-side "
            "(§20.2/HR-014) — refusing rather than pushing to an "
            "unconfirmed destination",
            details={
                "workspace_id": workspace_id,
                "remote": remote,
                "validated_repository": validated_remote.repository,
                "in_container_push_url": in_container_push_url,
            },
        )

    # Pushes to the URL the container's *own* git just resolved,
    # not the host-validated string — freshest possible value,
    # computed immediately before this call, and already confirmed
    # above to resolve to the authorized identity.
    argv = build_push_argv(
        in_container_push_url, refspec, force_with_lease_arg=force_with_lease_arg
    )
    if no_verify:
        argv.insert(argv.index("push") + 1, "--no-verify")

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
                    "remote_repository": remote_repository,
                },
            )
        # H32: same transport-failure classification as the fetch
        # step in `git_pull_tool` above — a rejected credential or
        # an unreachable remote is `GIT_AUTH_FAILED`/
        # `NETWORK_FAILED`, not a generic `COMMAND_FAILED`.
        if is_auth_failure(result.stderr_tail):
            raise GitAuthFailedError(
                f"git push failed authentication: {result.stderr_tail}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "branch": target_branch,
                    "remote_repository": remote_repository,
                },
            )
        if is_network_failure(result.stderr_tail):
            raise NetworkFailedError(
                f"git push failed to reach the remote: {result.stderr_tail}",
                details={
                    "workspace_id": workspace_id,
                    "remote": remote,
                    "branch": target_branch,
                    "remote_repository": remote_repository,
                },
            )
        raise CommandFailedError(
            f"git push failed: {result.stderr_tail}",
            details={
                "workspace_id": workspace_id,
                "remote": remote,
                "branch": target_branch,
                "delete": delete,
                "remote_repository": remote_repository,
            },
        )

    # §20.4: the *last* `workspace_open` verification, not a fresh
    # re-query — §20.4 only says a push MAY re-verify, and there is
    # no live credential to meaningfully re-verify against yet (D2);
    # reporting the cached value is honest and cheap. Computed once
    # and reused for both the audit record (HR-015: success records
    # SHALL carry `remote_protection_status`) and the response.
    remote_protection = to_report_output(load_stored_remote_protection(db, record.repository_id))

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
            "refspec": refspec,
            "pushed_sha": local_head_sha,
            "deleted": delete,
            "force": force,
            "force_with_lease": force_with_lease,
            "remote_protection_status": remote_protection.status,
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
        remote_protection=remote_protection,
    )
