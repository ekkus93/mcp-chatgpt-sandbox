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
    sanitize_for_client,
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
                branch = sanitize_for_client(value)
        elif token.startswith("# branch.upstream "):
            upstream = sanitize_for_client(token.removeprefix("# branch.upstream "))
        elif token.startswith("# branch.ab "):
            match = _AB_RE.search(token)
            if match is not None:
                ahead, behind = int(match.group(1)), int(match.group(2))
        elif token.startswith("1 "):
            # Ordinary changed entry: `1 XY sub mH mI mW hH hI path` —
            # 8 fields (XY, sub, mH, mI, mW, hH, hI, plus the leading
            # "1") then path.
            parts = token.split(" ", 8)
            xy, path = parts[1], sanitize_for_client(parts[8])
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)
        elif token.startswith("2 "):
            # Rename/copy: `2 XY sub mH mI mW hH hI score path` — one
            # extra field (the score, e.g. "R100") versus a `1` record,
            # so path is one token later. Followed by one more
            # NUL-delimited token (origPath), which this loop consumes
            # by advancing `index` past it.
            parts = token.split(" ", 9)
            xy, score, path = parts[1], parts[8], sanitize_for_client(parts[9])
            orig_path = sanitize_for_client(tokens[index]) if index < len(tokens) else None
            index += 1  # consume the trailing origPath token
            if xy[0] != ".":
                staged.append(path)
                # HR-024 (`docs/HARDENING_SPEC.md` §11): a staged rename
                # (not a copy — a copy's original is untouched, so it is
                # not itself a pending change) also removes the old path,
                # which `git_commit`'s own strict-mode check needs to see
                # too: `git commit --only -- <newpath>` alone, without
                # `<oldpath>` also named, was reproduced to silently
                # leave the old path staged-for-deletion and *outside*
                # the commit — a caller relying on this list to know what
                # to pass needs both names, not just the new one.
                if score.startswith("R") and orig_path is not None:
                    staged.append(orig_path)
            if xy[1] != ".":
                unstaged.append(path)
        elif token.startswith("u "):
            # Unmerged/conflict entry — 9 fields then path.
            parts = token.split(" ", 10)
            conflicted.append(sanitize_for_client(parts[10]))
        elif token.startswith("? "):
            untracked.append(sanitize_for_client(token[2:]))
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


#: git's own historical minimum for an unambiguous abbreviated SHA
#: (`GIT_MIN_HEXSZ`). Below this, `head_sha_matches` refuses to treat a
#: short value as a legitimate abbreviation — a 1-2 char value trivially
#: prefix-matches almost any SHA and isn't how anyone actually copies one.
MIN_ABBREVIATED_SHA_LENGTH = 4


def head_sha_matches(actual_head_sha: str | None, expected_head_sha: str) -> bool:
    """§45: `expected_head_sha` is routinely copied from a `git log`/`git
    show`/PR summary line, which by convention displays an *abbreviated*
    SHA (`git_log`'s own `%H` format is the exception, not the rule
    callers can assume) — comparing that directly against `head_sha`
    (always the full 40-hex-character OID; `--porcelain=v2` reports
    `branch.oid` in full) rejected every legitimate abbreviated caller.
    Accepted as a prefix match instead, the same relationship git itself
    uses to resolve an abbreviated revision — gated on
    `MIN_ABBREVIATED_SHA_LENGTH` so a degenerate short value can't
    trivially match. No separate hex-character check is needed: a
    non-hex character at any position can never be a prefix of the
    (always-hex) `actual_head_sha`, so `startswith` already rejects it."""
    if actual_head_sha is None:
        return False
    if len(expected_head_sha) < MIN_ABBREVIATED_SHA_LENGTH:
        return False
    return actual_head_sha.startswith(expected_head_sha)
