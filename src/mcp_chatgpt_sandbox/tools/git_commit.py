"""`git_commit` (§17.4, §20.6, §45, §18.9, S22) — the structured tool's
full body.

Extracted from `tools/git.py` (originally 1662 lines) into its own module
so no single file in `tools/` needs to hold every structured Git tool's
full body at once — `tools/git.py` itself is now the thin hub:
`register()`, `audit_denials`, and a re-export of every tool body/output
type the four `git_*` submodules (this one, `git_pull.py`, `git_push.py`,
`git_read.py`) define, so `mcp_chatgpt_sandbox.tools.git.run_git_commit`
still resolves exactly as it did before this split — a pure move, no
behavior change.
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import sqlite3
from dataclasses import dataclass, field

from mcp_chatgpt_sandbox.audit import write_audit_event
from mcp_chatgpt_sandbox.config.models import (
    AuditConfig,
    ContainerConfig,
    ExecutionConfig,
    GitPolicyConfig,
)
from mcp_chatgpt_sandbox.errors import (
    CommandFailedError,
    GitConflictError,
    GitDirtyError,
    GitHeadChangedError,
    GitNothingToCommitError,
    InvalidArgumentError,
    PolicyDeniedError,
    WorkspaceNotRunningError,
)
from mcp_chatgpt_sandbox.execution.service import CapturedOutput, ExecResult, ExecutionService
from mcp_chatgpt_sandbox.git.commit import (
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
from mcp_chatgpt_sandbox.git.service import detect_interrupted_operation
from mcp_chatgpt_sandbox.git.status import head_sha_matches, read_git_status
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.workspace.container_config import resolve_exec_user
from mcp_chatgpt_sandbox.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    get_authorized,
    resolve_repository_root,
    touch_last_used,
)


@dataclass
class GitCommitOutput:
    commit_sha: str
    branch: str | None
    files_committed: list[str] = field(default_factory=list)


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
        # GR-006 (`docs/GAP_SPEC.md`): only `conflicted`/`branch`/
        # `head_sha` are read below — an unrelated non-UTF-8 path
        # elsewhere in the repository has no bearing on this check and
        # SHALL NOT block an otherwise-unrelated commit.
        strict_utf8=False,
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
        strict_utf8=False,  # GR-006: only `head_sha` is used below
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
