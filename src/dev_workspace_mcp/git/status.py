"""`git_status`'s structured output (§17.2, §22.3) — S20.

Parsed from a single `git status --porcelain=v2 --branch
--untracked-files=all -z` call rather than several separate `git`
invocations: branch, head, upstream, ahead/behind, and every path's
staged/unstaged/untracked/conflicted state all come from one consistent
snapshot instead of risking a change between sequential reads. `-z`
(NUL-separated, no C-style path quoting) is what makes the output
reliably machine-parseable — porcelain v1's human-oriented quoting would
need its own escaping logic for exotic filenames.

Reports conflicts and interrupted operations rather than repairing them
(§44.4, AT-020): a merge conflict shows up in `conflicted` and
`interrupted_operation` untouched, never auto-resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dev_workspace_mcp.errors import CommandFailedError
from dev_workspace_mcp.git.service import (
    InterruptedOperation,
    detect_interrupted_operation,
    run_git_host_side,
)

_AB_RE = re.compile(r"\+(\d+) -(\d+)")


@dataclass(frozen=True, slots=True)
class GitStatus:
    branch: str | None
    """`None` when `detached_head` is true."""
    head_sha: str | None
    """`None` for an unborn branch (no commits yet)."""
    upstream: str | None
    ahead: int
    behind: int
    detached_head: bool
    clean: bool
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicted: list[str] = field(default_factory=list)
    interrupted_operation: InterruptedOperation | None = None


def read_git_status(repo_path: Path) -> GitStatus:
    result = run_git_host_side(
        repo_path, ["status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z"]
    )
    if not result.ok:
        raise CommandFailedError(
            f"git status failed: {result.stderr.strip()}",
            details={"path": str(repo_path), "stderr": result.stderr},
        )

    branch: str | None = None
    head_sha: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    detached_head = False
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    tokens = result.stdout.split("\0")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "":
            continue

        if token.startswith("# branch.oid "):
            value = token.removeprefix("# branch.oid ")
            head_sha = None if value == "(initial)" else value
        elif token.startswith("# branch.head "):
            value = token.removeprefix("# branch.head ")
            if value == "(detached)":
                detached_head = True
            else:
                branch = value
        elif token.startswith("# branch.upstream "):
            upstream = token.removeprefix("# branch.upstream ")
        elif token.startswith("# branch.ab "):
            match = _AB_RE.search(token)
            if match is not None:
                ahead, behind = int(match.group(1)), int(match.group(2))
        elif token.startswith("1 "):
            # Ordinary changed entry: `1 XY sub mH mI mW hH hI path` —
            # 8 fields (XY, sub, mH, mI, mW, hH, hI, plus the leading
            # "1") then path.
            parts = token.split(" ", 8)
            xy, path = parts[1], parts[8]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)
        elif token.startswith("2 "):
            # Rename/copy: `2 XY sub mH mI mW hH hI score path` — one
            # extra field (the score, e.g. "R100") versus a `1` record,
            # so path is one token later. Followed by one more
            # NUL-delimited token (origPath), which this loop skips by
            # advancing `index` past it.
            parts = token.split(" ", 9)
            xy, path = parts[1], parts[9]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)
            index += 1  # consume the trailing origPath token
        elif token.startswith("u "):
            # Unmerged/conflict entry — 9 fields then path.
            parts = token.split(" ", 10)
            conflicted.append(parts[10])
        elif token.startswith("? "):
            untracked.append(token[2:])
        # "!" (ignored) is never requested (`--ignored` not passed).

    clean = not (staged or unstaged or untracked or conflicted)

    return GitStatus(
        branch=branch,
        head_sha=head_sha,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        detached_head=detached_head,
        clean=clean,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
        interrupted_operation=detect_interrupted_operation(repo_path),
    )
