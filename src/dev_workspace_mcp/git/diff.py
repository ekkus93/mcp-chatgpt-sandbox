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
    next_offset: int


def _utf8_lead_sequence_length(lead_byte: int) -> int | None:
    """How many bytes the UTF-8 sequence starting with `lead_byte` needs
    in total, or `None` if `lead_byte` isn't a valid lead byte at all (a
    continuation byte, or one of the values UTF-8 never uses as a lead:
    0xC0, 0xC1, 0xF5-0xFF)."""
    if lead_byte & 0x80 == 0x00:
        return 1
    if lead_byte & 0xE0 == 0xC0:
        return 2
    if lead_byte & 0xF0 == 0xE0:
        return 3
    if lead_byte & 0xF8 == 0xF0:
        return 4
    return None


def _trim_incomplete_trailing_char(window: bytes) -> bytes:
    """`window` is an arbitrary byte slice of a UTF-8 diff — its end can
    land in the middle of a multi-byte character. Trim any incomplete
    trailing sequence off so decoding `window` never corrupts that
    character (an offset/`max_bytes` byte window historically fed
    straight to `bytes.decode(..., errors="replace")`, which turns the
    *whole* split character into U+FFFD instead of reassembling it
    correctly once the next window supplies the missing bytes).

    Only a *truncated* sequence — one that would be valid if more bytes
    followed — is trimmed. A genuinely invalid byte is left in place, to
    be handled by the caller's own lossy `errors="replace"` decode, same
    as always: this function's job is pagination correctness, not
    encoding validation.

    RR-016 (`docs/REMEDIATION_SPEC.md` §12): scans backward from the end
    of `window` for the trailing character's own lead byte, rather than
    inspecting only the *first* `UnicodeDecodeError` `window.decode`
    raises — reproduced empirically that an earlier invalid byte (e.g. a
    lone `0xff`, which `errors="surrogateescape"` deliberately lets
    survive into a diff, HR-022/H19) makes `decode` fail *there* first,
    masking a genuinely truncated multi-byte character later in the same
    window: `b'\\xff\\xe6\\x97'` (an invalid byte, then 2 of 3 bytes of
    `日`) used to come back unchanged, splitting the character instead of
    deferring it to the next window. Scanning back (at most 4 bytes,
    UTF-8's own maximum sequence length) finds the trailing character's
    lead byte directly, independent of whatever precedes it."""
    limit = min(len(window), 4)
    for back in range(1, limit + 1):
        byte = window[-back]
        if byte & 0xC0 == 0x80:
            continue  # a continuation byte — keep scanning backward
        seq_len = _utf8_lead_sequence_length(byte)
        if seq_len is not None and back < seq_len:
            return window[: len(window) - back]
        break  # a lead byte (valid or not) — nothing earlier to trim
    return window


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
    include — the same shape `exec_output`'s byte range uses. The
    returned `next_offset` is the byte position to pass as `offset` on
    the follow-up call; it can differ from `offset + max_bytes` by a few
    bytes whenever the naive window would have split a multi-byte
    character (`_trim_incomplete_trailing_char`).
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
    if offset < 0:
        raise InvalidArgumentError("offset must be >= 0 (§17.3)", details={"offset": offset})
    # RR-016 (`docs/REMEDIATION_SPEC.md` §12): `< 1`, not `< 0` — `0` used
    # to be accepted and produce a window of zero bytes every call,
    # `next_offset` never advancing past the caller's own `offset` —
    # reproduced empirically as permanent zero progress across repeated
    # calls, exactly the case H24's write-up claims was eliminated.
    if max_bytes is not None and max_bytes < 1:
        raise InvalidArgumentError(
            "max_bytes must be >= 1 (§17.3)", details={"max_bytes": max_bytes}
        )

    # HR-002(1): a diff-producing read must not invoke a repository's own
    # `diff.external`/`diff.<name>.textconv` driver. `--no-ext-diff` alone
    # is not enough — verified empirically that it does not suppress
    # `textconv` — and `--no-textconv` cannot substitute for
    # `--no-ext-diff` either, so both are required. Neither errors in any
    # mode this function has (`--stat`, `--cached`, commit-to-commit).
    cmd = ["diff", "--no-ext-diff", "--no-textconv"]
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

    # HR-022: `result.stdout` was decoded with `errors="surrogateescape"`
    # (`run_git_host_side`) precisely so a non-UTF-8 byte in the diff
    # can't raise here either — re-encode the same way rather than
    # `.encode("utf-8")`, which would raise `UnicodeEncodeError` on a
    # lone surrogate the moment the diff touched a non-UTF-8 file.
    encoded = result.stdout.encode("utf-8", errors="surrogateescape")
    total_bytes = len(encoded)
    limit = max_bytes if max_bytes is not None else DEFAULT_MAX_DIFF_BYTES
    window = encoded[offset : offset + limit]
    reaches_true_end = offset + len(window) >= total_bytes
    # Only snap the window shorter when there IS a next call that will
    # supply the trimmed bytes — snapping at the genuine end of the diff
    # (the trailing bytes are truncated because there's nothing more,
    # not because of our own windowing) would trim forever and never
    # make progress.
    if not reaches_true_end:
        trimmed = _trim_incomplete_trailing_char(window)
        # A `max_bytes` smaller than the byte width of the character
        # sitting at `offset` (the degenerate case — a real character is
        # at most 4 bytes) would otherwise trim the window all the way
        # to empty and make zero progress every call, forever. Forward
        # progress matters more than avoiding this one window's lossy
        # `errors="replace"` corruption below — keep the untrimmed bytes
        # rather than stall.
        if trimmed or not window:
            window = trimmed
    # RR-016: clamped to `total_bytes`, not `offset + len(window)`
    # unconditionally — an `offset` already past `total_bytes` (a client
    # re-requesting past the true end, or paginating off a diff that
    # shrank between calls) makes `window` empty, and `offset + 0` would
    # otherwise echo the same out-of-range value back as `next_offset`,
    # forever, rather than a value that actually means "the end."
    next_offset = min(offset + len(window), total_bytes)
    truncated = next_offset < total_bytes

    return GitDiffResult(
        content=window.decode("utf-8", errors="replace"),
        truncated=truncated,
        total_bytes=total_bytes,
        next_offset=next_offset,
    )
