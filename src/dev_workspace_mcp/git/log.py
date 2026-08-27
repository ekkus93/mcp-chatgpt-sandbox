"""`git_log` — bounded commit history (§17.3's "bounded output", §46) — S21.

Uses `git log --pretty=format:...` with ASCII field/record separators
(`\\x1f`/`\\x1e`) rather than a human-oriented format: commit subjects can
legitimately contain almost anything (including characters that would
collide with a naive delimiter like `|` or `,`), and the two ASCII
control characters git's own `%x1f`/`%x1e` placeholders emit are not
valid in a commit subject line at all.
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

#: §17.3's "bounded log" — no explicit number given, so this picks a
#: value in the same spirit as `filesystem.max_listing_entries` (§12.2):
#: enough for a normal review, never "the whole history" by accident.
DEFAULT_MAX_LOG_ENTRIES = 50

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = _FIELD_SEP.join(["%H", "%an <%ae>", "%aI", "%s"]) + _RECORD_SEP

#: `git log` on a branch with zero commits yet exits nonzero with this
#: message — an empty log, not a real failure.
_UNBORN_BRANCH_MARKER = "does not have any commits yet"


@dataclass(frozen=True, slots=True)
class GitLogEntry:
    sha: str
    author: str
    date: str
    subject: str


def read_git_log(
    repo_path: Path,
    *,
    max_entries: int = DEFAULT_MAX_LOG_ENTRIES,
    paths: list[str] | None = None,
    ref: str | None = None,
) -> list[GitLogEntry]:
    if max_entries <= 0:
        raise InvalidArgumentError(
            "max_entries must be positive", details={"max_entries": max_entries}
        )

    cmd = ["log", f"--max-count={max_entries}", f"--pretty=format:{_LOG_FORMAT}"]
    if ref is not None:
        # HR-001: `ref` is a client-supplied string that reaches a
        # host-side `git` invocation — reject anything option-shaped,
        # and place the real end-of-options marker immediately before it
        # as a second, load-bearing layer (rejection alone is a
        # denylist).
        validate_revision_argument(ref, param_name="ref")
        cmd.append(END_OF_OPTIONS)
        cmd.append(ref)
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    result = run_git_host_side(repo_path, cmd)
    if not result.ok:
        if _UNBORN_BRANCH_MARKER in result.stderr:
            return []
        raise CommandFailedError(
            f"git log failed: {result.stderr.strip()}",
            details={"path": str(repo_path), "stderr": result.stderr},
        )

    entries: list[GitLogEntry] = []
    for record in result.stdout.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, date, subject = record.split(_FIELD_SEP, 3)
        entries.append(GitLogEntry(sha=sha, author=author, date=date, subject=subject))
    return entries
