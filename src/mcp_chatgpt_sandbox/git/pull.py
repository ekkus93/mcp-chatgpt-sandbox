"""Fetch + integrate mechanics for structured `git_pull` (§20.5, §18.8) — S26.

Two Git invocations per pull, mirroring `git/commit.py`'s own two-step
add-then-commit split (S22): a fetch step, always with
`--no-recurse-submodules` (§18.8), followed by an integration step chosen
by `git_policy.pull_strategy` (§18.9), defaulting to `ff_only`. Pure argv
builders and result classification live here, like `git/commit.py`'s
`build_add_argv`/`build_commit_argv` — `tools/git.py`'s `git_pull_tool`
is what actually runs them, in-container, through the execution service
(§20.1), after `git/remote.py`'s `validate_remote_identity` (S25) has
already cleared the remote.
"""

from __future__ import annotations

from typing import Literal

PullStrategy = Literal["ff_only", "merge", "rebase"]

#: Real Git's own wording for `git merge --ff-only`'s specific failure —
#: matched so a genuine non-fast-forward is distinguished from some other
#: `merge --ff-only` failure (e.g. uncommitted changes the merge would
#: overwrite), which SHALL NOT be misreported as `GIT_NON_FAST_FORWARD`.
NON_FAST_FORWARD_MARKER = "Not possible to fast-forward"


def build_fetch_argv(remote: str) -> list[str]:
    """§18.8: the fetch step of a structured pull SHALL pass
    `--no-recurse-submodules` — a `.gitmodules` URL is tracked,
    container-writable content, and this is what keeps a structured pull
    from ever contacting a submodule's own remote at all, regardless of
    what `.gitmodules` says (AT-030's actual mechanism; see S25/S26 notes
    in docs/TODO.md)."""
    return ["git", "fetch", "--no-recurse-submodules", remote]


def build_integrate_argv(strategy: PullStrategy, *, remote: str, branch: str) -> list[str]:
    """The integration step, run only after a successful fetch.

    Always integrates `<remote>/<branch>` — the same same-named-branch
    convention §20.3's refspec table already assumes for the opposite
    direction (`refs/heads/<b>:refs/heads/<b>`)."""
    ref = f"{remote}/{branch}"
    if strategy == "ff_only":
        return ["git", "merge", "--ff-only", ref]
    if strategy == "merge":
        return ["git", "merge", ref]
    return ["git", "rebase", ref]


def is_non_fast_forward_failure(stderr: str) -> bool:
    """True only for `git merge --ff-only`'s specific "can't fast-forward"
    failure, not for `merge --ff-only`'s other possible failure modes
    (e.g. local changes that would be overwritten) — those SHALL surface
    as an ordinary command failure instead, per §20.6, not be
    misclassified as the (recoverable, "pull again later") `§20.5`
    non-fast-forward case."""
    return NON_FAST_FORWARD_MARKER in stderr
