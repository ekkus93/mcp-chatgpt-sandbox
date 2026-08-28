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
    below, applied to one more failure mode."""

    async def _run(args: list[str]) -> str | None:
        try:
            result = await run_git_in_container(
                args,
                container_id=container_id,
                workspace_path=workspace_path,
                execution_service=execution_service,
                container_config=container_config,
                execution_config=execution_config,
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
