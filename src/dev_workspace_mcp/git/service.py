"""Git execution plumbing (§20.1, §18.7, §44.4) — S19.

Two execution modes, matching §20.1 exactly:

- host-side reads (`git_status` / `git_diff` / `git_log`, S20/S21) run as
  the daemon uid, hook- and fsmonitor-neutralized so a read cannot
  execute repository-supplied code under the daemon's identity (§18.7);
- every mutating structured tool (`git_commit`, `git_pull`, `git_push`,
  S22/S26/S27) runs in-container, as the container user, through the
  *same* execution service `exec` already uses (S12/S15) — not a second
  execution path with a second credential (§20.1's rationale). There is
  therefore no "in-container git runner" here: a structured tool that
  mutates state calls `ExecutionService`/`AsyncJobRunner` directly with
  a `["git", ...]` argv, exactly as `exec("git ...")` would.

`detect_interrupted_operation` is host-side too (§44.4) — it only reads
marker files Git itself already maintains, the same ones `git status`
reports on; it never runs a Git process.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_git_host_side(repo_path: Path, args: list[str]) -> GitCommandResult:
    """Runs `git <args>` host-side, as the daemon uid (§20.1) — for
    `git_status` / `git_diff` / `git_log` only; every other structured
    operation runs in-container instead (see module docstring).

    Neutralizes hooks: `-c core.hooksPath=<a fresh, empty directory>`, so
    a repository-configured hook cannot run under the daemon's identity
    (§18.7), regardless of what the repository's own config says.
    Neutralizes `core.fsmonitor` the same way — the one hook-like
    mechanism a plain read (`status`, `diff`, `log`) can actually
    trigger; `core.hooksPath` alone would not stop it, since fsmonitor is
    a separate config key, not a file under the hooks directory.
    Disables the index lock (`GIT_OPTIONAL_LOCKS=0`) since a read has no
    business taking it. Uses the real environment plus that one
    override, not a replacement env — a bare `env={...}` drops `PATH`
    and everything else a subprocess needs to actually find `git`.
    """
    with tempfile.TemporaryDirectory(prefix="mcp-empty-hooks-") as empty_hooks_dir:
        result = subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                f"core.hooksPath={empty_hooks_dir}",
                "-c",
                "core.fsmonitor=",
                *args,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
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
