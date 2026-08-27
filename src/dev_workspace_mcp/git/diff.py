"""`git_diff` (§17.3, §46) — S21.

Working-tree, staged, and commit-to-commit diffs, all through one
function and one underlying `git diff` invocation — which mode is
selected is a pure function of which parameters are given, not three
separate code paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.errors import CommandFailedError, InvalidArgumentError
from dev_workspace_mcp.git.service import (
    END_OF_OPTIONS,
    run_git_host_side,
    validate_revision_argument,
)

#: A diff can legitimately run to megabytes (a large refactor, a
#: generated file) — §46 requires the *response* stay bounded regardless
#: of how big the underlying diff is. Same order of magnitude as
#: `filesystem.max_patch_bytes` (§12.2), a diff-shaped payload just read
#: instead of written. No config block exists whose sole purpose would
#: be this one field, so it stays a constant here, overridable per call
#: via `max_bytes` — the same "config default, client can ask for less"
#: shape `read_file`/`exec_output` already use.
DEFAULT_MAX_DIFF_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GitDiffResult:
    content: str
    truncated: bool
    total_bytes: int


def read_git_diff(
    repo_path: Path,
    *,
    staged: bool = False,
    base: str | None = None,
    head: str | None = None,
    paths: list[str] | None = None,
    stat_only: bool = False,
    max_bytes: int | None = None,
    offset: int = 0,
) -> GitDiffResult:
    """Three modes, matching §17.3:

    - working-tree diff (default): `staged=False`, no `base`/`head`;
    - staged diff: `staged=True`, no `base`/`head`;
    - commit-to-commit diff: `base` given (`head` defaults to `HEAD`).

    `staged` and `base`/`head` are mutually exclusive — combining them
    is ambiguous, not a request for anything git diff itself would do.
    `paths` filters to those paths; `stat_only` requests `--stat` instead
    of the full patch text. Bounded by `max_bytes` (§46), with `offset`
    for follow-up retrieval of what a first, truncated call did not
    include — the same shape `exec_output`'s byte range uses.
    """
    if head is not None and base is None:
        raise InvalidArgumentError(
            "head requires base for a commit-to-commit diff (§17.3)",
            details={"head": head},
        )
    if staged and base is not None:
        raise InvalidArgumentError(
            "staged and base/head are mutually exclusive (§17.3)",
            details={"staged": staged, "base": base},
        )

    cmd = ["diff"]
    if stat_only:
        cmd.append("--stat")
    if base is not None:
        head_value = head or "HEAD"
        # HR-001: `base`/`head` are client-supplied strings that reach a
        # host-side `git` invocation — reject anything option-shaped, and
        # place the real end-of-options marker immediately before them as
        # a second, load-bearing layer (rejection alone is a denylist).
        validate_revision_argument(base, param_name="base")
        validate_revision_argument(head_value, param_name="head")
        cmd.append(END_OF_OPTIONS)
        cmd.extend([base, head_value])
    elif staged:
        cmd.append("--cached")
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    result = run_git_host_side(repo_path, cmd)
    if not result.ok:
        raise CommandFailedError(
            f"git diff failed: {result.stderr.strip()}",
            details={"path": str(repo_path), "stderr": result.stderr},
        )

    encoded = result.stdout.encode("utf-8")
    total_bytes = len(encoded)
    limit = max_bytes if max_bytes is not None else DEFAULT_MAX_DIFF_BYTES
    window = encoded[offset : offset + limit]
    truncated = offset + len(window) < total_bytes

    return GitDiffResult(
        content=window.decode("utf-8", errors="replace"),
        truncated=truncated,
        total_bytes=total_bytes,
    )
