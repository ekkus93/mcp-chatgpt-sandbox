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
the field separator needs no equivalent guarantee — `parse_git_log_
output`'s own parse only ever splits on it with a bounded `maxsplit`, so
an embedded `\\x1f` inside the subject (the last, unbounded field) is
captured as part of the subject rather than mis-splitting anything.

H36 (`docs/HARDENING_TODO.md`, 4/8): `read_git_log` runs in-container
now, through `run_git_in_container` — `async`, and needs a running
workspace's container context. Split into three pieces, matching
`git/diff.py`'s own H36 split and `git/commit.py`/`git/pull.py`/
`git/push.py`'s pure-argv-builder convention:

- `build_log_argv`: `max_entries`/`ref`/`paths` -> argv, plus HR-001
  validation, pure;
- `parse_git_log_output`: the record/field parsing, pure — never
  depended on where the log came from;
- `read_git_log`: the async glue, plus the actual in-container
  invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.config.models import ContainerConfig, ExecutionConfig
from dev_workspace_mcp.errors import CommandFailedError, InvalidArgumentError
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.service import (
    END_OF_OPTIONS,
    run_git_in_container,
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
#: `%`, `x`, `0`, `0`) — safe to embed directly in the `argv` a caller
#: execs, since it contains no actual NUL byte at that point. Confirmed
#: empirically that the alternative (embedding a real `"\x00"` character
#: in the Python string built for the format argument, the way
#: `_FIELD_SEP` above is embedded raw) makes `subprocess.run` itself
#: raise `ValueError: embedded null byte` — `execve`'s argv strings are
#: NUL-terminated C strings, so a literal NUL *inside* one is
#: inexpressible at the OS level, not merely a Python quirk (the
#: in-container invocation, via Docker's exec API rather than a raw
#: `execve`, has never been tested against this specific constraint, but
#: there is no reason to expect it behaves any more permissively — the
#: format argument is still built the same way). Git only produces the
#: real `\x00` byte *after* it runs, in its own stdout — `_RECORD_SEP`
#: (the actual byte) is for parsing that output, never for building the
#: command line.
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


def build_log_argv(
    *,
    max_entries: int = DEFAULT_MAX_LOG_ENTRIES,
    paths: list[str] | None = None,
    ref: str | None = None,
) -> list[str]:
    if max_entries <= 0:
        raise InvalidArgumentError(
            "max_entries must be positive", details={"max_entries": max_entries}
        )

    cmd = ["log", f"--max-count={max_entries}", f"--pretty=format:{_LOG_FORMAT}"]
    if ref is not None:
        # HR-001: `ref` is a client-supplied string that reaches a Git
        # invocation — reject anything option-shaped, and place the real
        # end-of-options marker immediately before it as a second,
        # load-bearing layer (rejection alone is a denylist).
        validate_revision_argument(ref, param_name="ref")
        cmd.append(END_OF_OPTIONS)
        cmd.append(ref)
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    return cmd


def parse_git_log_output(stdout: str, stderr: str) -> list[GitLogEntry]:
    """`git log`'s stdout (in `_LOG_FORMAT`) -> `list[GitLogEntry]`.
    `stderr` is only consulted for the unborn-branch case, which `git
    log` reports as a nonzero exit — a caller that already checked
    `result.ok` before calling this passes an empty string."""
    if _UNBORN_BRANCH_MARKER in stderr:
        return []

    entries: list[GitLogEntry] = []
    for record in stdout.split(_RECORD_SEP):
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


async def read_git_log(
    repo_path: Path,
    *,
    container_id: str,
    workspace_path: str,
    execution_service: ExecutionService,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    max_entries: int = DEFAULT_MAX_LOG_ENTRIES,
    paths: list[str] | None = None,
    ref: str | None = None,
) -> list[GitLogEntry]:
    """`build_log_argv` + an in-container invocation + `parse_git_log_
    output`. `repo_path` is used only for error reporting — the actual
    read happens in-container, against `workspace_path`."""
    cmd = build_log_argv(max_entries=max_entries, paths=paths, ref=ref)

    result = await run_git_in_container(
        cmd,
        container_id=container_id,
        workspace_path=workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        # G2R-021 (`docs/GAP2_SPEC.md`): `_LOG_FORMAT` (`%H`, `%an <%ae>`,
        # `%aI`, `%s`) has no path field at all — `paths` above filters
        # which commits are considered, it is never echoed back in the
        # output `parse_git_log_output` reads. No path is ever parsed
        # out of `result.stdout`, so a non-UTF-8 path elsewhere in the
        # repository (§46's own "whole tree, not just what this caller
        # needs" concern) has no bearing on this call.
        strict_utf8=False,
    )
    if not result.ok and _UNBORN_BRANCH_MARKER not in result.stderr:
        raise CommandFailedError(
            f"git log failed: {result.stderr.strip()}",
            details={"path": str(repo_path), "stderr": result.stderr},
        )

    return parse_git_log_output(result.stdout, result.stderr)
