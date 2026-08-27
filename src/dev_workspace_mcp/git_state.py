"""A minimal, host-side Git state reader for `workspace_open`/`workspace_info`
(§62's `git` block: branch, head_sha, clean, upstream).

This is *not* the structured `git_status` tool (§17.2, Phase 3) — it has
none of that tool's conflict detection, ahead/behind tracking, or
interrupted-operation handling. It exists because §62's response shape
needs basic Git state now, in Phase 1, before Phase 3 exists.

Built on `git/service.py`'s `run_git_host_side` (S19) rather than its own
`subprocess` call, so both this and the real `git_status` (S20) share one
hook/fsmonitor-neutralized, lock-free invocation path (§20.1, §18.7)
instead of two independently-maintained ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.git.service import run_git_host_side


@dataclass(frozen=True, slots=True)
class BasicGitState:
    branch: str | None
    head_sha: str | None
    clean: bool | None
    upstream: str | None


def read_basic_git_state(repo_path: Path) -> BasicGitState:
    """Best-effort: an unborn/empty repository, or one that is not a Git
    repository at all, yields `None` fields rather than raising — this
    feeds a diagnostic response, not a Git operation with preconditions."""
    branch_result = run_git_host_side(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_result.stdout.strip() if branch_result.ok else None

    head_result = run_git_host_side(repo_path, ["rev-parse", "HEAD"])
    head_sha = head_result.stdout.strip() if head_result.ok else None

    status_result = run_git_host_side(repo_path, ["status", "--porcelain"])
    clean = status_result.stdout.strip() == "" if status_result.ok else None

    upstream_result = run_git_host_side(
        repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
    )
    upstream = upstream_result.stdout.strip() if upstream_result.ok else None

    return BasicGitState(branch=branch, head_sha=head_sha, clean=clean, upstream=upstream)
