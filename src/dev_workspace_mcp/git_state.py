"""A minimal, host-side Git state reader for `workspace_open`/`workspace_info`
(§62's `git` block: branch, head_sha, clean, upstream).

This is *not* the structured `git_status` tool (§17.2, Phase 3) — it has
none of that tool's conflict detection, ahead/behind tracking, or
interrupted-operation handling. It exists because §62's response shape
needs basic Git state now, in Phase 1, before Phase 3 exists. `git_status`
SHOULD be able to reuse the plumbing here rather than duplicate it.

Read-only `git` invocations only. `core.fsmonitor` disabled per §20.1's
host-side-read pattern; hook neutralization is not needed for the plumbing
commands used here (`rev-parse`, `status --porcelain`) — none of them
trigger `.git/hooks/*`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BasicGitState:
    branch: str | None
    head_sha: str | None
    clean: bool | None
    upstream: str | None


def _run(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_path), "-c", "core.fsmonitor=", *args],
        capture_output=True,
        text=True,
        env={"GIT_OPTIONAL_LOCKS": "0"},
    )


def read_basic_git_state(repo_path: Path) -> BasicGitState:
    """Best-effort: an unborn/empty repository, or one that is not a Git
    repository at all, yields `None` fields rather than raising — this
    feeds a diagnostic response, not a Git operation with preconditions."""
    branch_result = _run(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

    head_result = _run(repo_path, "rev-parse", "HEAD")
    head_sha = head_result.stdout.strip() if head_result.returncode == 0 else None

    status_result = _run(repo_path, "status", "--porcelain")
    clean = status_result.stdout.strip() == "" if status_result.returncode == 0 else None

    upstream_result = _run(
        repo_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None

    return BasicGitState(branch=branch, head_sha=head_sha, clean=clean, upstream=upstream)
