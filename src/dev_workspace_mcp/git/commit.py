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

HR-024 (`docs/HARDENING_SPEC.md` §11): every client-supplied path is
prefixed with the `:(literal)` pathspec magic before it ever reaches
`git`. Without it, a "path" is not necessarily a path at all — Git's own
pathspec syntax gives `:` special meaning (`:/` anchors to, and by itself
matches, the entire repository root; `:(glob)**` is a wildcard), so a
string that merely *looks* like a filename can stage the whole working
tree instead of the one file a caller believed they were naming.
Reproduced empirically: `git add -- ':/'` staged every modified file in
the repository; `git add -- ':(literal):/'` correctly fails with
"pathspec ':(literal):/' did not match any files" instead, exactly the
"there is no such file" response §17.4 promises for a nonexistent path.
"""

from __future__ import annotations

from pathlib import Path

from dev_workspace_mcp.git.service import run_git_host_side, sanitize_for_client


def _literal_pathspecs(paths: list[str]) -> list[str]:
    """`:(literal)` disables *all* pathspec magic for this one entry —
    no glob, no `:/`-anchoring, no icase — so every client-supplied
    string is matched only as the exact literal path it names."""
    return [f":(literal){path}" for path in paths]


def build_add_argv(paths: list[str]) -> list[str]:
    """A full argv for the execution service (§14) — unlike
    `run_git_host_side`, which prepends `git` itself, a raw in-container
    exec needs it spelled out (verified the hard way: omitting it exec's
    the literal argument `"add"` as the program, which is not on `$PATH`)."""
    return ["git", "add", "--", *_literal_pathspecs(paths)]


def build_commit_argv(*, message: str, paths: list[str], amend: bool, no_verify: bool) -> list[str]:
    cmd = ["git", "commit", "--only", "-m", message]
    if amend:
        cmd.append("--amend")
    if no_verify:
        cmd.append("--no-verify")
    cmd.append("--")
    cmd.extend(_literal_pathspecs(paths))
    return cmd


def build_capture_index_argv(paths: list[str]) -> list[str]:
    """RR-005 (`docs/REMEDIATION_SPEC.md` §5): the pre-`add` snapshot a
    failed commit needs in order to restore what `build_add_argv` is
    about to stage, rather than resetting to HEAD (the old
    `build_reset_argv`'s defect — HEAD and "what was staged before this
    call ran" are different states whenever a caller's paths already
    held prior staged content distinct from HEAD; reproduced empirically
    that a plain `git reset -- <paths>` silently destroys it). `-z`
    matches the NUL-delimited convention `read_committed_files` already
    uses — unlike a newline, a NUL cannot appear in a real path, and
    `tail_text`'s line-based truncation only ever sees this whole
    listing as a single "line" as a result, so a large listing is
    truncated only by the (10 MiB default) byte cap, never silently by
    line count."""
    return ["git", "ls-files", "-z", "--stage", "--", *_literal_pathspecs(paths)]


def parse_index_entries(raw: str) -> list[tuple[str, str, str]]:
    """Parses `-z --stage` output (`<mode> <sha> <stage>\\t<path>`,
    NUL-terminated entries) into `(mode, sha, path)` triples. The stage
    number itself is discarded, not asserted to be `0`: a real
    merge-conflict entry (stage 1/2/3) never actually reaches this
    parser in practice, because `git_commit_tool`'s own
    `status.conflicted` check (HR-023) already fails the commit closed
    before any of this runs — discarding the field is honest about what
    this parser does and doesn't guarantee, rather than silently
    assuming a conflict can't happen."""
    entries = []
    for chunk in raw.split("\0"):
        if not chunk:
            continue
        meta, _, path = chunk.partition("\t")
        mode, sha, _stage = meta.split(" ")
        entries.append((mode, sha, path))
    return entries


def build_restore_index_argv(entries: list[tuple[str, str, str]]) -> list[str]:
    """Restores exactly the `(mode, sha, path)` triples `parse_index_entries`
    captured — the faithful counterpart to the old destructive
    `build_reset_argv`. `--cacheinfo`'s `path` is a literal index path,
    not a pathspec (unlike every other argv this module builds): it is
    never client-supplied here, only ever an entry `git ls-files` itself
    already resolved, so wrapping it in `:(literal)` would corrupt it
    rather than protect it. `--add` makes one call correct whether the
    path was merely modified (an existing entry gets overwritten) or
    fully unstaged in between (a staged deletion; verified empirically
    that `--cacheinfo --add` re-creates the entry in that case too, with
    the working tree left untouched either way). Multiple `--cacheinfo`
    flags in one invocation each apply independently — verified against
    real git 2.43 with two distinct paths before trusting it."""
    cmd = ["git", "update-index", "--add"]
    for mode, sha, path in entries:
        cmd.extend(["--cacheinfo", f"{mode},{sha},{path}"])
    return cmd


def build_unstage_new_argv(paths: list[str]) -> list[str]:
    """The other half of the faithful restore: a path present in the
    post-`add` index but absent from the pre-`add` capture was not
    tracked or staged before this call, so restoring "prior state" for
    it means removing it from the index entirely, not re-adding
    anything. `--ignore-unmatch` keeps this a no-op rather than an error
    if a path is somehow already gone by the time this runs."""
    return ["git", "rm", "--cached", "--ignore-unmatch", "--", *_literal_pathspecs(paths)]


#: HR-025: the exact, stable substring Git's own `git commit` prints when
#: the given paths matched something real but there is genuinely nothing
#: to commit — confirmed empirically ("On branch main\nnothing to commit,
#: working tree clean") — distinct from every other commit failure mode,
#: which is why it gets its own error code rather than the generic
#: `COMMAND_FAILED`. Printed on *stdout*, not stderr — confirmed
#: empirically rather than assumed (an earlier draft checked only
#: `stderr_tail` and would never have matched a real failure).
NOTHING_TO_COMMIT_MARKER = "nothing to commit"


def is_nothing_to_commit_failure(stdout: str, stderr: str) -> bool:
    return NOTHING_TO_COMMIT_MARKER in stdout or NOTHING_TO_COMMIT_MARKER in stderr


def read_committed_files(repo_path: Path, commit_sha: str) -> list[str]:
    """HR-024: `files_committed` SHALL report what Git actually
    committed, not the request echo — the two can differ (a path that
    matched nothing would have already failed `build_add_argv`'s `git
    add` before reaching here, but a directory path legitimately expands
    to many files, and a case-insensitive filesystem can resolve a path
    to different literal casing than what was requested). Read host-side,
    same as every other post-commit status check in this module's
    caller — a single commit's own file list needs no container.

    `--root` is required, not decorative: verified empirically that
    `git diff-tree` without it reports *no* files at all for a commit
    with no parent (nothing to diff against) — `--root` makes it diff
    against the empty tree instead, and has no effect on an ordinary,
    non-root commit's own result."""
    result = run_git_host_side(
        repo_path,
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", commit_sha],
    )
    if not result.ok:
        return []
    return [sanitize_for_client(name) for name in result.stdout.split("\0") if name]


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
    # "refs/remotes/origin/main" -> "main". `removeprefix`, not
    # `rsplit("/", 1)[-1]` (HR-022) — a branch name containing its own
    # slash (e.g. "release/v1") would otherwise be mangled down to just
    # "v1".
    ref = result.stdout.strip()
    if not ref:
        return None
    return ref.removeprefix("refs/remotes/origin/")


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
