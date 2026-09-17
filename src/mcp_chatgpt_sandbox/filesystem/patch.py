"""Unified-diff application for `patch_file` (§13.4).

A hand-rolled single-file applier rather than shelling out to `patch(1)`:
exact context matching only (no fuzz), atomic (every hunk applies or none
do), and the failure shape is exactly the structured fields §13.4 asks
for — not `patch`'s stderr text or `.rej` files to parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    """1-indexed, as unified diff hunk headers are."""
    old_lines: list[str]
    new_lines: list[str]


@dataclass(frozen=True, slots=True)
class RejectedHunk:
    old_start: int
    reason: str


@dataclass(frozen=True, slots=True)
class PatchResult:
    applied: bool
    new_content: str | None
    """`None` when `applied` is False."""
    rejected_hunks: list[RejectedHunk] = field(default_factory=list)


class PatchFormatError(ValueError):
    """The supplied text is not a parseable unified diff."""


def parse_unified_diff(diff_text: str) -> list[Hunk]:
    """Parses hunks from a single-file unified diff. `---`/`+++` file
    header lines are accepted and ignored — the target file is already
    known from the tool call's own `path` argument, not re-derived from
    the diff text.
    """
    lines = diff_text.splitlines()
    hunks: list[Hunk] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("--- ", "+++ ")):
            i += 1
            continue
        match = _HUNK_HEADER_RE.match(line)
        if not match:
            i += 1
            continue

        old_start = int(match.group(1))
        old_len = int(match.group(2) or 1)
        new_len = int(match.group(4) or 1)
        i += 1

        old_lines: list[str] = []
        new_lines: list[str] = []
        while len(old_lines) < old_len or len(new_lines) < new_len:
            if i >= len(lines):
                raise PatchFormatError(f"hunk at line {old_start} ends before its body completes")
            body_line = lines[i]
            i += 1
            if body_line.startswith(" "):
                old_lines.append(body_line[1:])
                new_lines.append(body_line[1:])
            elif body_line.startswith("-"):
                old_lines.append(body_line[1:])
            elif body_line.startswith("+"):
                new_lines.append(body_line[1:])
            elif body_line == "\\ No newline at end of file":
                continue
            else:
                raise PatchFormatError(f"unrecognized hunk body line: {body_line!r}")

        hunks.append(Hunk(old_start=old_start, old_lines=old_lines, new_lines=new_lines))

    if not hunks:
        raise PatchFormatError("no hunks found in diff text")
    return hunks


def apply_unified_diff(original: str, diff_text: str) -> PatchResult:
    """Applies every hunk in `diff_text` to `original`, atomically: if any
    hunk's context does not match the corresponding region of `original`
    exactly, none are applied and every mismatching hunk is reported.
    """
    hunks = parse_unified_diff(diff_text)
    original_lines = original.splitlines(keepends=True)

    rejected: list[RejectedHunk] = []
    for hunk in hunks:
        start = _splice_index(hunk)
        actual = [
            _strip_newline(line) for line in original_lines[start : start + len(hunk.old_lines)]
        ]
        if actual != hunk.old_lines:
            rejected.append(
                RejectedHunk(
                    old_start=hunk.old_start,
                    reason="context does not match the current file content",
                )
            )

    if rejected:
        return PatchResult(applied=False, new_content=None, rejected_hunks=rejected)

    # Known limitation: every replacement line is written back with a
    # trailing "\n", including a hunk's last line when it lands at the
    # very end of the file. A source file that itself had no trailing
    # newline gains one if the final hunk touches its last line. This
    # matches what most editors and `write_file` callers expect anyway,
    # and is far simpler than tracking "\ No newline at end of file"
    # through every hunk boundary.
    result_lines = list(original_lines)
    offset = 0
    for hunk in hunks:
        start = _splice_index(hunk) + offset
        end = start + len(hunk.old_lines)
        result_lines[start:end] = [line + "\n" for line in hunk.new_lines]
        offset += len(hunk.new_lines) - len(hunk.old_lines)

    return PatchResult(applied=True, new_content="".join(result_lines), rejected_hunks=[])


def _splice_index(hunk: Hunk) -> int:
    """0-indexed position of `hunk.old_start` in the file being patched.

    Unified diff's hunk-header convention shifts for a zero-length old
    range (a pure insertion): the header number then means "insert after
    this many existing lines" rather than "this is the 1-indexed line
    here", so the 0-indexed splice point is `old_start` unchanged, not
    `old_start - 1`. `@@ -0,0 +1,1 @@` inserts before the first line
    (index 0); `@@ -1,0 +2,1 @@` inserts after the first line (index 1).
    """
    if not hunk.old_lines:
        return hunk.old_start
    return hunk.old_start - 1


def _strip_newline(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line
