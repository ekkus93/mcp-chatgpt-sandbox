"""Git execution plumbing (§20.1, §18.7, §44.4) — S19.

**H36 (`docs/HARDENING_TODO.md`): every structured Git invocation runs
in-container**, through `run_git_in_container` — the *same* execution
service `exec` already uses (S12/S15), not a second execution path with
a second credential (§20.1's rationale). This applies uniformly to the
read tools (`git_status`/`git_diff`/`git_log`, S20/S21) and the mutating
ones (`git_commit`/`git_pull`/`git_push`, S22/S26/S27) alike.
`run_git_host_side`, this module's pre-H36 host-side path (hook-/
fsmonitor-neutralized so a read could not execute repository-supplied
code under the *daemon's* identity, §18.7/HR-002 — see this file's own
git history for the retired implementation), is gone: in-container
execution accepts repository-code execution as an already-scoped risk
(`exec` already gives the client arbitrary shell in the container by
design, §19.7/§20.1), so there was nothing that four-part neutralization
protected against that `exec` doesn't already expose. See H36's own
write-up (`docs/HARDENING_TODO.md`) for the full before/after, including
the two exceptions below.

Two functions stay host-side regardless, for two different reasons:

- `detect_interrupted_operation` (§44.4) — it only reads marker files
  Git itself already maintains, the same ones `git status` reports on,
  directly off the bind-mounted working tree; it never runs a Git
  process at all, in or out of a container.
- `run_git_metadata_read` — a narrow, **permanent** exception, found
  while migrating `detect_default_branch` (`git/commit.py`):
  `open_workspace`'s §18.5 remote-protection check needs it *before* any
  container exists, and is restricted to Git plumbing commands that
  provably invoke no repository-configured code, so HR-002's
  neutralization was never actually protecting anything for this one
  case. See its own docstring for the full reasoning — it is not a
  general host-side escape hatch for anything else.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dev_workspace_mcp.config.models import ContainerConfig, ExecutionConfig
from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.workspace.container_config import resolve_exec_user

#: Placed immediately before a client-supplied revision argument in every
#: read that accepts one (`git_diff`'s `base`/`head`, `git_log`'s
#: `ref`) — HR-001, `docs/HARDENING_SPEC.md` §4. Verified against git 2.43.0:
#: everything after this marker is parsed strictly as a positional argument,
#: never as an option, even one shaped exactly like `--output=<path>`. Still
#: enforced post-H36 even though every read now runs in-container: this
#: protects against option injection, a concern independent of *where* Git
#: runs — `exec` already gives the client arbitrary shell in the container,
#: but a structured tool's own client-supplied parameter still SHALL NOT be
#: interpretable as an option of its own accord.
END_OF_OPTIONS = "--end-of-options"


def validate_revision_argument(value: str, *, param_name: str) -> None:
    """HR-001: a client-supplied revision/ref parameter SHALL NOT be
    interpretable by Git as an option. Rejecting a leading `-` is the
    first, human-readable layer — `--end-of-options` (placed by each
    caller immediately before the argument in the actual argv) is the
    second, load-bearing one; this check alone is a denylist over a
    syntax Git may extend, so it is deliberately not the only control."""
    if value.startswith("-"):
        raise InvalidArgumentError(
            f"{param_name} may not begin with '-' (§20.1/HR-001): {value!r}",
            details={param_name: value},
        )


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """`stdout`/`stderr` for one Git invocation, whichever of
    `run_git_in_container`/`run_git_metadata_read` produced it.
    `run_git_in_container`'s (`execution/service.py`'s `capture`) are
    decoded with `errors="replace"`; `run_git_metadata_read`'s with
    `errors="surrogateescape"` — see each function's own docstring.
    `sanitize_for_client` handles either safely (see its own
    docstring)."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def sanitize_for_client(value: str) -> str:
    """A string decoded from a Git invocation's stdout — a
    repository-tracked path, or a commit subject/author, neither of
    which Git requires to be valid UTF-8 — is safe to hold and reason
    about *within* this process, but a lone surrogate escape is not
    valid JSON output. Confirmed empirically that the MCP SDK's own
    response serialization (`pydantic_core.to_json`) crashes on one with
    the exact `UnicodeEncodeError` `surrogateescape` exists to avoid at
    the subprocess boundary in the first place — moving the crash
    downstream, not preventing it. Re-encoding with `surrogateescape`
    and decoding back with `errors="replace"` turns any such byte into
    U+FFFD — the same lossy-but-safe tradeoff `git/diff.py` already
    makes for diff *content*; harmless as a no-op when the input was
    already `errors="replace"`-decoded (U+FFFD round-trips losslessly
    through UTF-8). Call this at the point a git-derived string is about
    to become a client-facing field (a path, a commit subject/author, a
    branch/upstream name), not any earlier."""
    return value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


async def run_git_in_container(
    args: list[str],
    *,
    container_id: str,
    workspace_path: str,
    execution_service: ExecutionService,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> GitCommandResult:
    """Runs `git <args>` in-container (§20.1, H36), as the container
    user, through the *same* execution service `exec`/`git_commit`/
    `git_pull`/`git_push` already use — not a second execution path with
    a second credential. Applies no HR-002-style neutralization:
    in-container repository-code execution is an already-accepted,
    already-scoped risk, so there is nothing left here that `exec`
    doesn't already expose.

    `GIT_OPTIONAL_LOCKS=0` is set regardless — not a security control, a
    read simply has no business taking the index lock, in a container
    any more than on the host.

    `max_output_bytes=execution_config.max_output_bytes` (not any of the
    smaller, read-specific defaults like `git/diff.py`'s
    `DEFAULT_MAX_DIFF_BYTES`) is deliberately generous: this is `capture`
    the caller does its *own* pagination/truncation on top of (§46), not
    the client-visible cap — a smaller value here would silently mean a
    caller's own `total_bytes`/pagination reasons about less than the
    command's true output. A single Git read exceeding
    `execution_config.max_output_bytes` (10 MiB by default — an order of
    magnitude past `DEFAULT_MAX_DIFF_BYTES`) is the one case this can
    still under-report the true total; accepted as a known, generous
    limit rather than an unbounded capture, consistent with every other
    container-output capture in this codebase.
    """
    user = resolve_exec_user(container_config)
    exec_id = await asyncio.to_thread(
        execution_service.start,
        container_id,
        ["git", *args],
        cwd=workspace_path,
        user=user,
        env={"GIT_OPTIONAL_LOCKS": "0"},
    )
    captured = await asyncio.to_thread(
        execution_service.capture,
        exec_id,
        max_bytes=execution_config.max_output_bytes,
    )
    return GitCommandResult(
        returncode=captured.exit_code, stdout=captured.stdout, stderr=captured.stderr
    )


def run_git_metadata_read(repo_path: Path, args: list[str]) -> GitCommandResult:
    """A narrow, **permanent** host-side exception to H36's "every
    structured Git invocation runs in-container" migration — needed for
    `detect_default_branch` (`git/commit.py`), which `open_workspace`'s
    §18.5 remote-protection check calls *before* any container is
    created or any key is provisioned into one: "SHALL NOT provision a
    write-capable key... SHALL NOT have provisioned a key into the
    container" is enforced at that point precisely so it runs before
    Docker is touched at all. There is no container yet for that call to
    read from — moving it in-container isn't a smaller version of the
    same migration, it's structurally impossible without either
    violating §18.5's own ordering or starting a container speculatively
    only to possibly tear it down immediately, which is exactly what the
    existing code comment there says this ordering avoids.

    Restricted to Git plumbing commands that provably never invoke
    repository-configured code — no hook, filter, textconv, or driver of
    any kind — so HR-002's four-part neutralization (retired along with
    `run_git_host_side`, since it protected against exactly that) was
    never actually needed here in the first place, rather than being
    skipped for convenience. `git symbolic-ref` (this function's only
    caller, `detect_default_branch`) resolves a single ref; it touches
    neither the working tree nor the index, and no hook exists for it
    (checked against Git's own hook list, `githooks(5)`). Do not add a
    new caller here without the same verification for whatever command
    it wants to run — this is not a general-purpose host-side escape
    hatch, and every new use has to independently justify its own
    safety, not inherit this one's."""
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    return GitCommandResult(
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
    )


class InterruptedOperation(StrEnum):
    """§44.4's states, detected from the marker files Git itself
    maintains for each."""

    MERGE = "merge"
    REBASE = "rebase"
    CHERRY_PICK = "cherry_pick"
    BISECT = "bisect"


def _git_dir(repo_path: Path) -> Path:
    """The actual `.git` directory for `repo_path` — a plain directory in
    the ordinary case, or resolved one level through a gitfile's
    `gitdir:` line for a linked worktree or submodule (§18.2). Diagnostic
    only: unlike `workspace/mounts.py`'s `resolve_git_dir`, this does not
    authorize the resolved path against anything — it only reads state,
    it never decides what may be mounted."""
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        content = git_path.read_text().strip()
        if content.startswith("gitdir:"):
            target = content.removeprefix("gitdir:").strip()
            return (repo_path / target).resolve()
    return git_path


def detect_interrupted_operation(repo_path: Path) -> InterruptedOperation | None:
    """§44.4: merge/rebase/cherry-pick/bisect in progress, detected from
    the marker files Git itself uses — the same ones `git status`
    reports on. Called before a structured operation proceeds, not to
    repair anything: the daemon reports these rather than guessing what
    the user wants done about them. `None` for a repository not
    currently in any of these states (the common case)."""
    git_dir = _git_dir(repo_path)
    if (git_dir / "MERGE_HEAD").exists():
        return InterruptedOperation.MERGE
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return InterruptedOperation.REBASE
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return InterruptedOperation.CHERRY_PICK
    if (git_dir / "BISECT_LOG").exists():
        return InterruptedOperation.BISECT
    return None
