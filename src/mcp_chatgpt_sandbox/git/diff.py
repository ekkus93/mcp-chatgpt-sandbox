"""`git_diff` (§17.3, §46) — S21.

Working-tree, staged, and commit-to-commit diffs, all through one
function and one underlying `git diff` invocation — which mode is
selected is a pure function of which parameters are given, not three
separate code paths.

RR-018 (`docs/REMEDIATION_SPEC.md` §13): the character-boundary trim
this module's own `next_offset` pagination needs (RR-016) moved to
`utf8_boundary.py`, imported here under its original private name —
`filesystem/service.py`'s `read_file` needs the identical fix for the
identical reason (a byte window blind to character boundaries), and
duplicating it risked the two copies silently diverging.

H36 (`docs/HARDENING_TODO.md`, 3/8): `read_git_diff` runs in-container
now, through `run_git_in_container` — `async`, and needs a running
workspace's container context. Split into three pieces so the parts with
no Docker dependency of their own stay directly, cheaply testable —
matching `git/commit.py`/`git/pull.py`/`git/push.py`'s existing
pure-argv-builder convention, which this module predates:

- `build_diff_argv`: mode selection + HR-001 validation, pure;
- `paginate_diff_output`: §46's byte-windowing/truncation math over an
  already-fetched diff, pure — this never depended on where the diff
  came from, and moving the invocation in-container changed none of it;
- `read_git_diff`: the async glue between the two, plus the actual
  in-container invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcp_chatgpt_sandbox.config.models import ContainerConfig, ExecutionConfig
from mcp_chatgpt_sandbox.errors import CommandFailedError, InvalidArgumentError
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.git.service import (
    END_OF_OPTIONS,
    run_git_in_container,
    validate_revision_argument,
)
from mcp_chatgpt_sandbox.utf8_boundary import (
    trim_incomplete_trailing_char as _trim_incomplete_trailing_char,
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


def build_diff_argv(
    *,
    staged: bool = False,
    base: str | None = None,
    head: str | None = None,
    paths: list[str] | None = None,
    stat_only: bool = False,
) -> list[str]:
    """Three modes, matching §17.3:

    - working-tree diff (default): `staged=False`, no `base`/`head`;
    - staged diff: `staged=True`, no `base`/`head`;
    - commit-to-commit diff: `base` given (`head` defaults to `HEAD`).

    `staged` and `base`/`head` are mutually exclusive — combining them
    is ambiguous, not a request for anything git diff itself would do.
    `paths` filters to those paths; `stat_only` requests `--stat` instead
    of the full patch text.
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

    # HR-002(1): a diff-producing read must not invoke a repository's own
    # `diff.external`/`diff.<name>.textconv` driver. `--no-ext-diff`
    # alone is not enough — verified empirically that it does not
    # suppress `textconv` — and `--no-textconv` cannot substitute for
    # `--no-ext-diff` either, so both are required. Neither errors in
    # any mode this function has (`--stat`, `--cached`, commit-to-commit).
    # H36: kept even though in-container execution no longer needs this
    # for the *code-execution* concern HR-002 named it for (§4's control
    # surface is retired) — an external diff/textconv driver's *output*
    # replacing the real diff would still corrupt what the client sees,
    # a correctness reason independent of where Git runs.
    cmd = ["diff", "--no-ext-diff", "--no-textconv"]
    if stat_only:
        cmd.append("--stat")
    if base is not None:
        head_value = head or "HEAD"
        # HR-001: `base`/`head` are client-supplied strings that reach a
        # Git invocation — reject anything option-shaped, and place the
        # real end-of-options marker immediately before them as a
        # second, load-bearing layer (rejection alone is a denylist).
        validate_revision_argument(base, param_name="base")
        validate_revision_argument(head_value, param_name="head")
        cmd.append(END_OF_OPTIONS)
        cmd.extend([base, head_value])
    elif staged:
        cmd.append("--cached")
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    return cmd


def paginate_diff_output(
    stdout: str, *, max_bytes: int | None = None, offset: int = 0
) -> GitDiffResult:
    """§46's bounded-output pagination over an already-fetched `git diff`
    stdout. `offset` for follow-up retrieval of what a first, truncated
    call did not include — the same shape `exec_output`'s byte range
    uses. The returned `next_offset` is the byte position to pass as
    `offset` on the follow-up call; it can differ from `offset +
    max_bytes` by a few bytes whenever the naive window would have split
    a multi-byte character (`_trim_incomplete_trailing_char`)."""
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

    # HR-022: `stdout` is decoded with `errors="replace"`
    # (`execution/service.py`'s `capture`) — re-encode the same lossy
    # way, not `.encode("utf-8")`, which would raise `UnicodeEncodeError`
    # on... nothing, actually, since `errors="replace"` already replaced
    # every undecodable byte with U+FFFD before this function ever saw
    # the string. Kept as `surrogateescape` regardless: harmless on an
    # already-`replace`d string (U+FFFD round-trips losslessly through
    # UTF-8), and this function has no way to know which decoding
    # produced its input if a future caller's capture path changes.
    encoded = stdout.encode("utf-8", errors="surrogateescape")
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


async def read_git_diff(
    repo_path: Path,
    *,
    container_id: str,
    workspace_path: str,
    execution_service: ExecutionService,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    staged: bool = False,
    base: str | None = None,
    head: str | None = None,
    paths: list[str] | None = None,
    stat_only: bool = False,
    max_bytes: int | None = None,
    offset: int = 0,
) -> GitDiffResult:
    """`build_diff_argv` (mode + HR-001) + an in-container invocation +
    `paginate_diff_output` (§46). `repo_path` is used only for error
    reporting — the actual read happens in-container, against
    `workspace_path`."""
    cmd = build_diff_argv(staged=staged, base=base, head=head, paths=paths, stat_only=stat_only)

    result = await run_git_in_container(
        cmd,
        container_id=container_id,
        workspace_path=workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        # G2R-021 (`docs/GAP2_SPEC.md`): explicit, matching the existing
        # default — diff headers (`diff --git a/... b/...`, `+++`/`---`)
        # embed the changed files' own paths, and `paginate_diff_output`
        # reports the diff text to the client verbatim, the "report
        # faithfully" case for keeping this `True`.
        strict_utf8=True,
    )
    if not result.ok:
        raise CommandFailedError(
            f"git diff failed: {result.stderr.strip()}",
            details={"path": str(repo_path), "stderr": result.stderr},
        )

    return paginate_diff_output(result.stdout, max_bytes=max_bytes, offset=offset)
