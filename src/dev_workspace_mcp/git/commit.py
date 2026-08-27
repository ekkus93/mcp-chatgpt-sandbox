"""`git_commit` (§17.4, §20.6, §45, §65, §18.9) — S22.

Commit creation itself runs in-container (§20.1), through the execution
service `tools/git.py` shares with `exec` — this module only owns the
host-side-checkable pieces: building the argv, and the two policy
detections (`default_branch_commit`, `amend_published`) that need to be
evaluated *before* the container is asked to do anything, per §20.6's
"evaluate preconditions immediately before acting, and fail closed."

Two commands, not one: `git add -- <paths>` then `git commit --only -m
<message> -- <paths>`. "No separate `git_add`" (§17.4) means the *client*
never makes a separate tool call to stage anything — `git_commit` does
staging and committing in one request — not that the implementation
avoids `git add` as a subprocess. Verified against real git behavior that
it can't: `git commit --only <path>` alone raises "pathspec ... did not
match any file(s) known to git" for a genuinely new, never-tracked file —
`--only` restricts which of git's already-known changes get committed, it
does not itself introduce an untracked file to git the way `git add`
does. `--only` is still what makes "stages exactly what it is told" true
in the presence of anything *else* already staged: without it, a plain
`git commit -m ...` would commit that too.
"""

from __future__ import annotations

from pathlib import Path

from dev_workspace_mcp.git.service import run_git_host_side


def build_add_argv(paths: list[str]) -> list[str]:
    """A full argv for the execution service (§14) — unlike
    `run_git_host_side`, which prepends `git` itself, a raw in-container
    exec needs it spelled out (verified the hard way: omitting it exec's
    the literal argument `"add"` as the program, which is not on `$PATH`)."""
    return ["git", "add", "--", *paths]


def build_commit_argv(*, message: str, paths: list[str], amend: bool, no_verify: bool) -> list[str]:
    cmd = ["git", "commit", "--only", "-m", message]
    if amend:
        cmd.append("--amend")
    if no_verify:
        cmd.append("--no-verify")
    cmd.append("--")
    cmd.extend(paths)
    return cmd


def detect_default_branch(repo_path: Path) -> str | None:
    """The repository's default branch, if determinable without any
    network access or credential — which the host-side daemon has
    neither of (§19's whole point). `origin/HEAD` is a *local* ref git
    itself sets up during a normal `git clone` (or `git remote set-head
    origin --auto`); reading it needs no network round trip. `None` when
    it was never set (e.g. a repository the daemon didn't clone itself),
    in which case `default_branch_commit` enforcement is skipped rather
    than guessed at (§18.9's policy governs a parameter a tool actually
    evaluates — guessing "main"/"master" would make it evaluate a guess,
    not the repository's real default branch)."""
    result = run_git_host_side(repo_path, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if not result.ok:
        return None
    # "refs/remotes/origin/main" -> "main"
    ref = result.stdout.strip()
    return ref.rsplit("/", 1)[-1] if ref else None


def is_head_published(repo_path: Path) -> bool:
    """Whether the *current* HEAD (before any amend) is already present
    on its upstream tracking branch — §18.9's `amend_published` policy
    exists to stop rewriting exactly this. `git merge-base --is-ancestor
    HEAD @{upstream}` exits 0 when HEAD is reachable from (or equal to)
    upstream, i.e. already pushed there.

    No upstream configured at all is treated as "not published", not
    "unknown, block to be safe": with no upstream branch, there is
    categorically no "upstream branch" this commit could already be
    present on (verified: this fails with a distinct "no upstream
    configured" error, not ambiguously with a not-an-ancestor result) —
    so amending is genuinely safe here, not just permitted by default.
    """
    result = run_git_host_side(repo_path, ["merge-base", "--is-ancestor", "HEAD", "@{upstream}"])
    return result.ok
