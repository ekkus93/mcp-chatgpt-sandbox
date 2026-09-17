"""Refspec construction for structured `git_push` (§20.3, §20.5, §18.9) —
S27.

The daemon constructs its own refspec from validated parameters — never a
client-supplied string (FR-034) — exactly per §20.3's table. Mostly pure
functions here, mirroring `git/commit.py`/`git/pull.py`'s own split;
`tools/git.py`'s `git_push_tool` is what actually evaluates policy and
runs the constructed command, in-container, through the execution
service (§20.1), after `git/remote.py`'s `validate_remote_identity`
(S25) has already cleared the remote.

`build_force_with_lease_arg` is the one exception to "pure": it reads
the local remote-tracking ref to construct its return value, and (H36,
`docs/HARDENING_TODO.md`, 7/8) that read now runs in-container, through
`run_git_in_container` — `async`. `validate_branch_name`'s own `git
check-ref-format --branch` call stays a bare `subprocess.run`, untouched
by H36 either way: it takes no repository path at all (no `-C`, no
`cwd`) and reads no repository configuration — a stateless syntax
checker, not a read this migration's "repository-configured code
execution" concern ever applied to.
"""

from __future__ import annotations

import subprocess
from typing import Literal

from mcp_chatgpt_sandbox.config.models import ContainerConfig, ExecutionConfig
from mcp_chatgpt_sandbox.errors import GitRefspecNotAllowedError
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.git.remote import PROTOCOL_EXT_DISABLED_ARGS
from mcp_chatgpt_sandbox.git.service import run_git_in_container

RefType = Literal["branch", "tag"]

#: Real Git's own wording for a plain (non-force) push rejected because
#: the remote has commits the local branch doesn't — the push equivalent
#: of `git/pull.py`'s `NON_FAST_FORWARD_MARKER`, verified empirically
#: against a real diverged push before trusting it.
PUSH_NON_FAST_FORWARD_MARKERS = ("(fetch first)", "(non-fast-forward)", "(stale info)")


def validate_branch_name(branch: str) -> None:
    """§20.3: reject a `branch` that is not a valid ref name, or that
    would place the destination outside `refs/heads/`/`refs/tags/`.

    Raises `GitRefspecNotAllowedError` (`GIT_REFSPEC_NOT_ALLOWED`, §31)
    — this *is* §20.3's own "reject a `branch` value that is not a
    valid ref name, or that would place the destination outside
    `refs/heads/`/`refs/tags/`" bullet, not a generic argument-shape
    problem; `default_recoverable = True` on that error class ("correct
    the refspec parameters and retry") matches exactly what a caller
    should do with a rejection here. H32 (`docs/HARDENING_TODO.md`,
    `HARDENING_SPEC.md` §9/§26 row): previously `InvalidArgumentError`,
    which left the more specific code unreachable.

    Delegates the actual syntax check to Git itself
    (`check-ref-format --branch`) rather than a hand-rolled regex —
    verified empirically to reject `..`, a leading `-` (which `git push`
    would otherwise read as a flag), embedded spaces, and an empty
    string. Additionally rejects a value that is already `refs/`-
    qualified: the daemon always supplies the `refs/heads/`/`refs/tags/`
    prefix itself, so an input that supplies its own would silently
    double up rather than cleanly "place the destination outside" the
    namespace — confusing enough on its own to refuse outright."""
    if branch.startswith("refs/"):
        raise GitRefspecNotAllowedError(
            f"branch must be a short name, not a fully-qualified ref: {branch!r}",
            details={"branch": branch},
        )
    result = subprocess.run(  # noqa: S603
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitRefspecNotAllowedError(
            f"not a valid branch name: {branch!r} ({result.stderr.strip()})",
            details={"branch": branch},
        )


def build_refspec(branch: str, *, ref_type: RefType, force: bool, delete: bool) -> str:
    """§20.3's table, literally — one refspec string per row.
    `force_with_lease` uses this same (unforced) refspec shape; its extra
    CLI flag is `build_force_with_lease_arg`'s job, not this function's."""
    if delete:
        namespace = "heads" if ref_type == "branch" else "tags"
        return f":refs/{namespace}/{branch}"
    prefix = "+" if force else ""
    return f"{prefix}refs/heads/{branch}:refs/heads/{branch}"


async def build_force_with_lease_arg(
    *,
    remote: str,
    branch: str,
    container_id: str,
    workspace_path: str,
    execution_service: ExecutionService,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> str:
    """`--force-with-lease=<ref>:<sha>` when the local remote-tracking
    ref for `<remote>/<branch>` is known (the common case, after any
    fetch) — protecting against clobbering a push that happened since;
    bare `--force-with-lease` otherwise, letting Git fall back to its own
    reflog-based safety check rather than this daemon inventing an
    expected value it does not actually have.

    H36 (`docs/HARDENING_TODO.md`, 7/8): runs in-container now, through
    `run_git_in_container` — always called from within `run_git_push`,
    which already requires a running workspace before reaching here."""
    ref = f"refs/heads/{branch}"
    tracking = await run_git_in_container(
        ["rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"],
        container_id=container_id,
        workspace_path=workspace_path,
        execution_service=execution_service,
        container_config=container_config,
        execution_config=execution_config,
        strict_utf8=False,  # GR-006: a rev-parse'd SHA, never a repository path
    )
    if tracking.ok:
        return f"--force-with-lease={ref}:{tracking.stdout.strip()}"
    return "--force-with-lease"


def build_push_argv(
    remote: str, refspec: str, *, force_with_lease_arg: str | None = None
) -> list[str]:
    """§20.3's exact resulting invocation shape: `--atomic
    --recurse-submodules=no`, `-c protocol.ext.allow=never` (defense in
    depth, matching `git/remote.py`'s own use of the same flag on
    `remote get-url`), never `--mirror`, `--all`, or `--tags`."""
    argv = ["git", *PROTOCOL_EXT_DISABLED_ARGS, "push", "--atomic", "--recurse-submodules=no"]
    if force_with_lease_arg is not None:
        argv.append(force_with_lease_arg)
    argv.extend([remote, refspec])
    return argv


def is_push_non_fast_forward_failure(stderr: str) -> bool:
    """True for a plain push rejected because the remote has commits the
    local branch lacks — the push analogue of
    `git/pull.py`'s `is_non_fast_forward_failure`. Distinct wording from
    `merge --ff-only`'s, since this is a different Git command failing
    for a related reason."""
    return any(marker in stderr for marker in PUSH_NON_FAST_FORWARD_MARKERS)
