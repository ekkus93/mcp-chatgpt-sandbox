"""`git_log` — bounded commit history (§17.3's "bounded output", §46) — S21.

Uses `git log --pretty=format:...` with a NUL record separator and an
ASCII (`\\x1f`) field separator rather than a human-oriented format:
commit subjects can legitimately contain almost anything (including
characters that would collide with a naive delimiter like `|` or `,`).

HR-022 (`docs/HARDENING_SPEC.md` §10): a commit subject is *not*
guaranteed to avoid `\\x1e` — confirmed empirically that Git itself
places no such restriction on a commit message's bytes, and a subject
containing `\\x1e` will appear, unescaped, right in the middle of this
format's own record. An earlier version of this module used `\\x1e` as
the record separator on the mistaken assumption that it "is not valid in
a commit subject line at all"; NUL (`\\x00`) is used instead, since Git
*does* enforce that constraint (verified empirically: `git commit` itself
refuses "a NUL byte in commit log message not allowed"), making a false
split here actually impossible rather than merely unlikely. `\\x1f` as
the field separator needs no equivalent guarantee — `read_git_log`'s own
parse only ever splits on it with a bounded `maxsplit`, so an embedded
`\\x1f` inside the subject (the last, unbounded field) is captured as
part of the subject rather than mis-splitting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.errors import CommandFailedError, InvalidArgumentError
from dev_workspace_mcp.git.service import (
    END_OF_OPTIONS,
    run_git_host_side,
    sanitize_for_client,
    validate_revision_argument,
)

#: §17.3's "bounded log" — no explicit number given, so this picks a
#: value in the same spirit as `filesystem.max_listing_entries` (§12.2):
#: enough for a normal review, never "the whole history" by accident.
DEFAULT_MAX_LOG_ENTRIES = 50

_FIELD_SEP = "\x1f"
#: Two distinct constants for one separator, not an oversight: `%x00` is
#: Git's own *placeholder syntax* (four literal ASCII characters —
#: `%`, `x`, `0`, `0`) — safe to embed directly in the `argv` string
#: `subprocess.run` execs, since it contains no actual NUL byte at that
#: point. Confirmed empirically that the alternative (embedding a real
#: `"\x00"` character in the Python string built for the format
#: argument, the way `_FIELD_SEP` above is embedded raw) makes
#: `subprocess.run` itself raise `ValueError: embedded null byte` —
#: `execve`'s argv strings are NUL-terminated C strings, so a literal
#: NUL *inside* one is inexpressible at the OS level, not merely a
#: Python quirk. Git only produces the real `\x00` byte *after* it runs,
#: in its own stdout — `_RECORD_SEP` (the actual byte) is for parsing
#: that output, never for building the command line.
_RECORD_SEP_PLACEHOLDER = "%x00"
#: NUL, not `\x1e` — see the module docstring for why a record separator
#: has to be a byte Git itself guarantees can never appear inside a
#: commit subject, not merely one that's unlikely to.
_RECORD_SEP = "\x00"
_LOG_FORMAT = _FIELD_SEP.join(["%H", "%an <%ae>", "%aI", "%s"]) + _RECORD_SEP_PLACEHOLDER

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
        entries.append(
            GitLogEntry(
                sha=sha,
                author=sanitize_for_client(author),
                date=date,
                subject=sanitize_for_client(subject),
            )
        )
    return entries
