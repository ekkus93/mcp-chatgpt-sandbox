"""A minimal Git state reader for `workspace_open`/`workspace_info`
(§62's `git` block: branch, head_sha, clean, upstream).

This is *not* the structured `git_status` tool (§17.2, Phase 3) — it has
none of that tool's conflict detection, ahead/behind tracking, or
interrupted-operation handling. It exists because §62's response shape
needs basic Git state now, in Phase 1, before Phase 3 exists.

H36 (`docs/HARDENING_TODO.md`, 5/8): `read_basic_git_state` runs
in-container now, through `run_git_in_container` — `async`, and needs a
running workspace's container context. Unlike the three structured read
tools, this one has no non-`None` fallback for a workspace that is not
currently running: `workspace_info`/`workspace_open`'s own callers
(`tools/workspace.py`'s `build_workspace_info`) pass `BasicGitState`'s
all-`None` form directly for that case, rather than calling this
function at all — `workspace_info` on a stopped or absent workspace
still needs to succeed (reporting `container_state` and everything else
§62 promises), just without the git block populated, so this can't raise
`WorkspaceNotRunningError` the way the three read tools now do."""

from __future__ import annotations

from dataclasses import dataclass

from dev_workspace_mcp.config.models import ContainerConfig, ExecutionConfig
from dev_workspace_mcp.errors import CommandFailedError
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.service import run_git_in_container


@dataclass(frozen=True, slots=True)
class BasicGitState:
    branch: str | None
    head_sha: str | None
    clean: bool | None
    upstream: str | None


#: `workspace_info`/`workspace_open`'s own value for a workspace that is
#: not currently running — every field `None`, exactly what
#: `read_basic_git_state` itself already returned for an unborn/invalid
#: repository, now also used for "no running container to read from."
NO_GIT_STATE = BasicGitState(branch=None, head_sha=None, clean=None, upstream=None)


async def read_basic_git_state(
    *,
    container_id: str,
    workspace_path: str,
    execution_service: ExecutionService,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> BasicGitState:
    """Best-effort: an unborn/empty repository, or one that is not a Git
    repository at all, yields `None` fields rather than raising — this
    feeds a diagnostic response, not a Git operation with preconditions.
    No host `repo_path` parameter, unlike this module's sibling
    functions: this never had a host-side fallback (`detect_interrupted_
    operation`-shaped or otherwise), so once the read moved in-container
    there was nothing left needing the host path at all.

    The one caller that deliberately absorbs `run_git_in_container`'s
    truncation failure rather than propagating it. That function fails
    closed for everything else — a parser handed a cut-off `git status`
    or `git log` would produce silently wrong results — but this block is
    `workspace_info`'s *diagnostic* `git` field, whose whole contract is
    already "`None` when it cannot be determined". A working tree
    pathological enough to overflow the capture cap must not make
    `workspace_info` itself unavailable: reporting `clean: null` beside
    an accurate `container_state` is strictly more useful than failing
    the call. This is the same reasoning as the `if result.ok else None`
    below, applied to one more failure mode.

    G2R-021 (`docs/GAP2_SPEC.md`): `_run` below is shared by every
    command this function issues (`rev-parse` three times, `status
    --porcelain` once) with no distinction, and used to leave
    `strict_utf8` at `run_git_in_container`'s own default (`True`) by
    omission, not by choice — a real behavior nobody had actually
    decided. None of the four callers below need it: `branch`/
    `head_sha`/`upstream` are ref names and a SHA, never a repository
    *content* path, and `status_output` is only ever compared for
    emptiness (`clean = status_output == ""`) — no individual path is
    extracted from it or reported anywhere.

    **Correction — verify the actual mechanism, not the plausible one
    (RR-033).** This task's own write-up originally claimed the
    consequence was a non-UTF-8 *file* path making `git.clean` go
    `null`. Checked empirically and that specific claim does not hold:
    `git status --porcelain` (this call — no `-z`, unlike `git/
    status.py`'s own `--porcelain=v2 ... -z`) C-quotes any non-ASCII
    byte in a path as a literal octal escape (`"bad-\377\376-name.txt"`)
    by git's own default `core.quotePath`, which this codebase never
    overrides — the quoted output is valid ASCII/UTF-8 regardless of
    the real filename's bytes, so `decode_utf8_lossy` never trips for
    an untracked/modified *file* here at all. The genuine, reproducible
    trigger is a non-UTF-8 **ref name** instead: `git rev-parse
    --abbrev-ref HEAD`/`@{upstream}` emit a branch/upstream name
    *unquoted*, raw — confirmed directly (`git checkout -b
    "$(printf 'bad-\xff\xfe-branch')"` then `git rev-parse --abbrev-ref
    HEAD` prints the two raw bytes verbatim). With the old implicit
    `strict_utf8=True`, checking out such a branch made `branch` (or,
    for the symmetric `@{upstream}` call, `upstream`) go `None` instead
    of reporting the name — the same "diagnostic field silently
    unavailable" shape the original claim described, just triggered by
    a different one of `_run`'s four callers than claimed. Set `False`
    here to fix it for all four, uniformly, since none of them parse a
    path either way."""

    async def _run(args: list[str]) -> str | None:
        try:
            result = await run_git_in_container(
                args,
                container_id=container_id,
                workspace_path=workspace_path,
                execution_service=execution_service,
                container_config=container_config,
                execution_config=execution_config,
                strict_utf8=False,  # G2R-021: see this function's own docstring
            )
        except CommandFailedError:
            return None
        return result.stdout.strip() if result.ok else None

    branch = await _run(["rev-parse", "--abbrev-ref", "HEAD"])
    head_sha = await _run(["rev-parse", "HEAD"])
    status_output = await _run(["status", "--porcelain"])
    clean = status_output == "" if status_output is not None else None
    upstream = await _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])

    return BasicGitState(branch=branch, head_sha=head_sha, clean=clean, upstream=upstream)
