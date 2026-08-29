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
import contextlib
import logging
import posixpath
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
    GitAuthFailedError,
    GitConflictError,
    GitDirtyError,
    GitHeadChangedError,
    GitNonFastForwardError,
    GitNothingToCommitError,
    GitRemoteNotAllowedError,
    InvalidArgumentError,
    NetworkFailedError,
    PolicyDeniedError,
    WorkspaceError,
    WorkspaceNotRunningError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.service import CapturedOutput, ExecResult, ExecutionService
from dev_workspace_mcp.git.commit import (
    build_add_argv,
    build_capture_index_argv,
    build_commit_argv,
    build_restore_index_argv,
    build_unstage_new_argv,
    detect_default_branch,
    is_head_published,
    is_nothing_to_commit_failure,
    parse_index_entries,
    read_committed_files,
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
from dev_workspace_mcp.git.remote import (
    build_get_push_url_argv,
    normalize_to_owner_repo,
    validate_remote_identity,
    validate_remote_identity_for_push,
)
from dev_workspace_mcp.git.service import detect_interrupted_operation, run_git_in_container
from dev_workspace_mcp.git.status import head_sha_matches, read_git_status
from dev_workspace_mcp.git.transport import is_auth_failure, is_network_failure
from dev_workspace_mcp.github_api import (
    RemoteProtectionReportOutput,
    RemoteProtectionResult,
    deserialize_remote_protection,
    to_report_output,
)
from dev_workspace_mcp.logging_config import redacted_extra
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.workspace.container_config import resolve_exec_user
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    get_authorized,
    resolve_repository_root,
    touch_last_used,
)

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


def load_stored_remote_protection(
    db: sqlite3.Connection, repository_id: str
) -> RemoteProtectionResult | None:
    row = db.execute(
        "SELECT remote_protection FROM repository WHERE id = ?", (repository_id,)
    ).fetchone()
    return deserialize_remote_protection(row["remote_protection"]) if row is not None else None


async def run_git_commit(
    workspace_id: str,
    message: str,
    paths: list[str],
    expected_branch: str | None,
    expected_head_sha: str | None,
    strict: bool,
    amend: bool,
    no_verify: bool,
    *,
    manager: DockerWorkspaceManager,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    git_policy_config: GitPolicyConfig,
    audit_config: AuditConfig,
) -> GitCommitOutput:
    """§17.4: the `git_commit` tool's full body — module-level, not a
    `register()` closure (R27, `docs/REMEDIATION_TODO.md`), so it can be
    unit-tested by constructing its dependencies directly rather than a
    whole running server, the same reason `tools/workspace.py`'s
    `open_workspace` was extracted the same way. `register()`'s
    `git_commit_tool` is a thin wrapper: check Docker reachability, then
    call this."""
    if not paths:
        raise InvalidArgumentError(
            "git_commit requires at least one explicit path (§17.4) — there is "
            "no separate git_add; exec('git add ...') if you need a broader stage"
        )
    # RR-004: a non-empty *list* containing an empty-string (or otherwise
    # root-normalizing) entry passed the check above. `:(literal)` disables
    # glob/anchor magic for every *other* character, but an empty literal
    # is still not "no pathspec at all" to git — reproduced empirically,
    # `git add -- ':(literal)'` stages the entire working tree, exactly
    # the "no separate git_add" boundary this tool exists to enforce.
    root_normalizing = sorted({path for path in paths if posixpath.normpath(path) == "."})
    if root_normalizing:
        raise InvalidArgumentError(
            "git_commit requires each path to name a real file, not the "
            f"repository root (§17.4): {root_normalizing}",
            details={"paths": paths, "root_normalizing": root_normalizing},
        )
    if not message.strip():
        raise InvalidArgumentError("git_commit requires a non-empty message")

    record = get_authorized(manager, workspace_id, registry)
    if record.state != WorkspaceState.RUNNING:
        raise WorkspaceNotRunningError(
            f"workspace {workspace_id!r} is not running (state: {record.state.value})",
            details={"workspace_id": workspace_id, "state": record.state.value},
        )
    assert record.container_id is not None  # implied by state == RUNNING
    container_id = record.container_id  # narrowed value, captured for the closure below
    touch_last_used(db, workspace_id)  # §8.1: this is real usage, not idle

    # §20.6: preconditions, evaluated before the container is asked to
    # do anything, and fail closed — the working tree is bind-mounted,
    # so a host-side read here sees exactly what the container would.
    repo_path = resolve_repository_root(db, workspace_id, registry)

    interrupted = detect_interrupted_operation(repo_path)
    if interrupted is not None:
        raise GitConflictError(
            f"a {interrupted.value} is in progress; resolve or abort it before committing (§44.4)",
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

    # HR-023 (`docs/HARDENING_SPEC.md` §11): `detect_interrupted_operation`
    # alone is not enough — it only reads marker files (`MERGE_HEAD`,
    # `rebase-merge/`, etc.) an ordinary `git merge`/`rebase` leaves,
    # and `git stash pop` does not leave one of those, even though it
    # can leave exactly the same unresolved-conflict working tree
    # (`git status` reporting `UU`). Reproduced: without this check,
    # a commit after a conflicting `stash pop` succeeded, silently
    # recording whatever half-resolved content happened to be staged.
    if status.conflicted:
        raise GitConflictError(
            f"unresolved conflicts present: {status.conflicted} — resolve them "
            "before committing (§44.4)",
            details={"workspace_id": workspace_id, "conflicted": status.conflicted},
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

    if strict:
        # HR-024: normalized before comparing — a caller-supplied
        # `./foo.txt` or `a/../foo.txt` is the same path as `git
        # status`'s own `foo.txt`, but a literal-string set
        # difference would treat them as different, flagging
        # `foo.txt` as extraneous even though it was, in substance,
        # requested. `status.staged` (git/status.py) now also
        # includes a staged rename's old path alongside its new
        # one — a caller who names only the new path is correctly
        # flagged here, rather than going on to silently produce a
        # broken partial-rename commit.
        normalized_paths = {posixpath.normpath(path) for path in paths}
        extraneous = sorted(set(status.staged) - normalized_paths)
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

    # HR-022: `!= "allow"` — the same fail-closed shape
    # `default_branch_push` (below) now also uses — denies both
    # "deny" and "require_approval" alike; §42: `require_approval`
    # is a hard denial in this release, no interactive-approval
    # mechanism exists yet.
    if (
        amend
        and git_policy_config.amend_published != "allow"
        and await is_head_published(
            container_id=container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=container_config,
            execution_config=execution_config,
        )
    ):
        raise PolicyDeniedError(
            "amend_published policy denies amending a commit already present "
            "on its upstream branch (§18.9, §65)",
            details={
                "policy": "amend_published",
                "value": git_policy_config.amend_published,
                "upstream": status.upstream,
                "branch": status.branch,
            },
        )

    user = resolve_exec_user(container_config)

    async def run_git(cmd: list[str]) -> ExecResult:
        exec_id = await asyncio.to_thread(
            execution_service.start,
            container_id,
            cmd,
            cwd=record.workspace_path,
            user=user,
            env=None,
        )
        return await asyncio.to_thread(
            execution_service.consume,
            exec_id,
            max_output_bytes=execution_config.max_output_bytes,
            response_tail_lines=execution_config.response_tail_lines,
        )

    async def capture_index(cmd: list[str]) -> CapturedOutput:
        """VR-001 (`docs/REVIEW_SPEC.md` §4): the index snapshot
        `parse_index_entries` parses is read through `capture`, not
        `run_git`/`consume` — `consume`'s `response_tail_lines` line-crop
        can cut a NUL-delimited `-z --stage` entry in half whenever a
        staged path contains an embedded newline (git does not escape
        newlines in `-z` output, unlike the NUL delimiter itself; a real
        path can contain one), and `ExecResult.stdout_truncated` reflects
        only the *byte* cap, so it stays `False` while the data is
        already cropped. `capture`'s `stdout_truncated` is the right
        signal here: this is a whole listing being handed to a strict
        parser, not a human-readable tail."""
        exec_id = await asyncio.to_thread(
            execution_service.start,
            container_id,
            cmd,
            cwd=record.workspace_path,
            user=user,
            env=None,
        )
        return await asyncio.to_thread(
            execution_service.capture,
            exec_id,
            max_bytes=execution_config.max_output_bytes,
        )

    # RR-005 (`docs/REMEDIATION_SPEC.md` §5): the pre-`add` snapshot a
    # failed commit needs in order to restore what's about to be
    # staged, rather than resetting to HEAD — those are different
    # states whenever these paths already held prior staged content
    # distinct from HEAD (reproduced empirically: the old
    # `build_reset_argv` silently destroyed it). `stdout_truncated` is
    # checked, not assumed false — a truncated capture is not a
    # faithful snapshot and must not be trusted for a
    # destructive-adjacent restore.
    #
    # AR-001 (`docs/AUDIT_SPEC.md` §4): `stdout_lossy` is checked
    # alongside it, for the same reason — a non-UTF-8 staged path
    # decodes to U+FFFD, and `build_restore_index_argv` would then
    # write this call's prior content to that *corrupted* path
    # instead of the real one, leaving a phantom index entry while
    # `index_left_dirty` reports `false`. There is no faithful decode
    # to fall back to here (the Docker exec boundary is JSON and
    # cannot carry a non-UTF-8 byte — verified against a real
    # container, `docs/AUDIT_SPEC.md` §4), so a lossy snapshot is
    # treated exactly like a truncated one: not a faithful snapshot,
    # `prior_entries` stays `None`, no restore is attempted.
    capture_result = await capture_index(build_capture_index_argv(paths))
    prior_entries: list[tuple[str, str, str]] | None = (
        parse_index_entries(capture_result.stdout)
        if capture_result.exit_code == 0
        and not capture_result.stdout_truncated
        and not capture_result.stdout_lossy
        else None
    )

    # Two commands, not one — see git/commit.py's module docstring
    # for why `--only` alone can't stage a genuinely new file.
    add_result = await run_git(build_add_argv(paths))
    if add_result.exit_code != 0:
        raise CommandFailedError(
            f"git add failed: {add_result.stderr_tail}",
            details={"workspace_id": workspace_id, "paths": paths},
        )

    commit_result = await run_git(
        build_commit_argv(message=message, paths=paths, amend=amend, no_verify=no_verify)
    )
    if commit_result.exit_code != 0:
        # HR-025 (`docs/HARDENING_SPEC.md` §12) / RR-005: a failed
        # commit SHALL leave the index restored to what it held
        # *before* this call's own `git add`, not reset to HEAD.
        # Best-effort, and only ever additive-then-corrective, never a
        # blind reset: if the pre-add snapshot is unavailable, or the
        # post-add snapshot needed to know what's newly staged fails,
        # this does *not* fall back to a destructive reset — it
        # leaves the index as `git add`/`git commit` left it and
        # reports that honestly via `index_left_dirty` instead. A
        # leaked stage is recoverable; guessing wrong about what to
        # destroy is not.
        index_left_dirty = True
        if prior_entries is not None:
            with contextlib.suppress(Exception):
                # AR-001 (`docs/AUDIT_SPEC.md` §4): this re-capture has its
                # own, independent failure mode when lossy — distinct from
                # site 1's above. Its `current_entries` feeds `extra_paths`
                # (paths newly staged by this call's own `git add`, to be
                # unstaged) via set difference against `prior_paths`. A
                # non-UTF-8 path that decodes to the *same* U+FFFD on both
                # captures cancels out of that difference and is silently
                # never unstaged; one that decodes lossily only here
                # produces a corrupted path `build_unstage_new_argv` passes
                # to `git rm --cached --ignore-unmatch`, which matches
                # nothing and exits 0 — reporting `unstage_ok` (and so
                # `index_left_dirty`) as if the real file had been
                # unstaged, when it was not. Checked the same way site 1
                # is: not a faithful snapshot, skip using it.
                current_result = await capture_index(build_capture_index_argv(paths))
                if (
                    current_result.exit_code == 0
                    and not current_result.stdout_truncated
                    and not current_result.stdout_lossy
                ):
                    current_entries = parse_index_entries(current_result.stdout)
                    prior_paths = {path for _, _, path in prior_entries}
                    extra_paths = sorted({path for _, _, path in current_entries} - prior_paths)
                    unstage_ok = True
                    if extra_paths:
                        unstage_result = await run_git(build_unstage_new_argv(extra_paths))
                        unstage_ok = unstage_result.exit_code == 0
                    restore_ok = True
                    if prior_entries:
                        restore_result = await run_git(build_restore_index_argv(prior_entries))
                        restore_ok = restore_result.exit_code == 0
                    index_left_dirty = not (unstage_ok and restore_ok)

        if is_nothing_to_commit_failure(commit_result.stdout_tail, commit_result.stderr_tail):
            raise GitNothingToCommitError(
                f"nothing to commit: {commit_result.stdout_tail or commit_result.stderr_tail}",
                details={
                    "workspace_id": workspace_id,
                    "paths": paths,
                    "index_left_dirty": index_left_dirty,
                },
            )
        raise CommandFailedError(
            f"git commit failed: {commit_result.stderr_tail}",
            details={
                "workspace_id": workspace_id,
                "paths": paths,
                "index_left_dirty": index_left_dirty,
            },
        )

    new_status = await read_git_status(
        repo_path,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
    )
    assert new_status.head_sha is not None  # a commit just succeeded
    # HR-024: `files_committed` SHALL report what Git actually
    # committed, not the request echo — a directory path legitimately
    # expands to many files, and `paths` itself is never a reliable
    # stand-in for "what actually changed" in general.
    files_committed = await read_committed_files(
        new_status.head_sha,
        container_id=container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
    )

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
            "files_committed": files_committed,
        },
    )

    return GitCommitOutput(
        commit_sha=new_status.head_sha,
        branch=new_status.branch,
        files_committed=files_committed,
    )


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
    reason `run_git_commit` above is; see its docstring."""
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
    reason `run_git_commit` above is; see its docstring."""
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
    `run_git_pull`/`run_git_push` above already use. Pre-H36 this was a
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
