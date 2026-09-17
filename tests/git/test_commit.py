"""S22: `git_commit`'s host-side-checkable pieces (§17.4, §18.9).

H36 (`docs/HARDENING_TODO.md`, 6/8): `is_head_published`/
`read_committed_files` themselves now need a running workspace's
container context (§18.9's amend-published check and the post-commit
file list both run in-container). `_is_head_published_of`/
`_committed_files_of` below exercise the real, unchanged
`IS_HEAD_PUBLISHED_ARGV`/`build_committed_files_argv`/
`parse_committed_files_output` against a real host-side `git`, the same
split production's own functions use, keeping this whole suite fast and
Docker-free. `detect_default_branch` needs no such helper — see
`git/service.py`'s `run_git_metadata_read` docstring for why it stays
host-side (and its own tests unchanged) rather than migrating.
"""

import subprocess
from pathlib import Path

from mcp_chatgpt_sandbox.git.commit import (
    IS_HEAD_PUBLISHED_ARGV,
    build_add_argv,
    build_capture_index_argv,
    build_commit_argv,
    build_committed_files_argv,
    build_restore_index_argv,
    build_unstage_new_argv,
    detect_default_branch,
    is_nothing_to_commit_failure,
    parse_committed_files_output,
    parse_index_entries,
)


def _is_head_published_of(repo: Path) -> bool:
    result = subprocess.run(["git", *IS_HEAD_PUBLISHED_ARGV], cwd=repo, capture_output=True)
    return result.returncode == 0


def _committed_files_of(repo: Path, commit_sha: str) -> list[str]:
    result = subprocess.run(
        ["git", *build_committed_files_argv(commit_sha)],
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        return []
    return parse_committed_files_output(result.stdout)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    return path


# --- argv builders: must include "git" — a raw in-container exec, unlike ----------
# --- run_git_in_container, does not prepend it. A real regression: an earlier -----
# --- version omitted it and failed with "exec: add: executable file not found". ---


def test_build_add_argv_includes_git() -> None:
    argv = build_add_argv(["a.txt", "b.txt"])
    assert argv[0] == "git"
    assert argv == ["git", "add", "--", ":(literal)a.txt", ":(literal)b.txt"]


def test_build_commit_argv_includes_git() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt"], amend=False, no_verify=False)
    assert argv[0] == "git"
    assert argv == ["git", "commit", "--only", "-m", "msg", "--", ":(literal)a.txt"]


def test_build_commit_argv_amend_and_no_verify() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt"], amend=True, no_verify=True)
    assert "--amend" in argv
    assert "--no-verify" in argv
    # paths stay last, after "--", regardless of flag order.
    assert argv[-2:] == ["--", ":(literal)a.txt"]


def test_build_commit_argv_multiple_paths() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt", "b.txt"], amend=False, no_verify=False)
    assert argv[-2:] == [":(literal)a.txt", ":(literal)b.txt"]


# --- HR-024: paths are literal pathspecs, not pathspec magic ----------------------


def test_build_add_argv_prefixes_every_path_with_literal_magic() -> None:
    """A real regression: `paths=[":/"]` (or any string Git's pathspec
    syntax gives special meaning to) used to reach `git add` unprefixed,
    letting a single client-supplied "path" match — and stage — the
    entire repository instead of the one file a caller believed they
    were naming."""
    argv = build_add_argv([":/"])
    assert argv == ["git", "add", "--", ":(literal):/"]


def test_build_commit_argv_prefixes_every_path_with_literal_magic() -> None:
    argv = build_commit_argv(message="msg", paths=[":(glob)**"], amend=False, no_verify=False)
    assert argv[-1] == ":(literal):(glob)**"


# --- detect_default_branch ----------------------------------------------------------


def test_detect_default_branch_reads_origin_head_after_a_real_clone(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)

    assert detect_default_branch(clone) == "main"


def test_detect_default_branch_is_none_without_origin_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert detect_default_branch(repo) is None


def test_at_h024_default_branch_containing_its_own_slash_is_not_mangled(
    tmp_path: Path,
) -> None:
    """A real regression: `ref.rsplit("/", 1)[-1]` on
    `"refs/remotes/origin/release/v1"` returned `"v1"`, not
    `"release/v1"` — any default branch name containing its own `/`
    (a common convention: `release/v1`, `team/feature`) was mangled."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "release/v1", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "checkout", "-q", "-b", "release/v1"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", "release/v1"], cwd=seed, check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)

    assert detect_default_branch(clone) == "release/v1"


# --- is_head_published ---------------------------------------------------------------


def test_is_head_published_true_for_a_pushed_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)

    assert _is_head_published_of(seed) is True


def test_is_head_published_false_for_an_unpushed_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)

    (seed / "a.txt").write_text("a\nmore\n")
    subprocess.run(["git", "commit", "-q", "-am", "unpushed"], cwd=seed, check=True)

    assert _is_head_published_of(seed) is False


def test_is_head_published_false_without_any_upstream(tmp_path: Path) -> None:
    """No upstream at all means categorically nothing to have been
    published to — not "unknown, assume published"."""
    repo = _init_repo(tmp_path / "repo")
    assert _is_head_published_of(repo) is False


# --- read_committed_files -----------------------------------------------------------


def test_read_committed_files_reports_the_root_commit(tmp_path: Path) -> None:
    """`git diff-tree` needs `--root` for a commit with no parent —
    verified empirically that omitting it reports zero files for a
    repository's very first commit, since there is nothing to diff
    against without it."""
    repo = _init_repo(tmp_path / "repo")
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert _committed_files_of(repo, head_sha) == ["a.txt"]


def test_read_committed_files_reports_a_normal_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "b.txt").write_text("b\n")
    (repo / "c.txt").write_text("c\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert _committed_files_of(repo, head_sha) == ["b.txt", "c.txt"]


def test_read_committed_files_returns_empty_for_an_unknown_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert _committed_files_of(repo, "0" * 40) == []


# --- HR-025: rollback on a failed commit; a specific code for "nothing to commit" -


def test_build_capture_index_argv_uses_literal_pathspecs() -> None:
    argv = build_capture_index_argv(["a.txt", ":/"])
    assert argv == ["git", "ls-files", "-z", "--stage", "--", ":(literal)a.txt", ":(literal):/"]


def test_build_unstage_new_argv_uses_literal_pathspecs() -> None:
    argv = build_unstage_new_argv(["a.txt", ":/"])
    assert argv == [
        "git",
        "rm",
        "--cached",
        "--ignore-unmatch",
        "--",
        ":(literal)a.txt",
        ":(literal):/",
    ]


def test_build_restore_index_argv_one_cacheinfo_flag_per_entry() -> None:
    argv = build_restore_index_argv(
        [("100644", "f531e07edd848253b8d61160ac743ae4e3d09888", "a.txt")]
    )
    assert argv == [
        "git",
        "update-index",
        "--add",
        "--cacheinfo",
        "100644,f531e07edd848253b8d61160ac743ae4e3d09888,a.txt",
    ]


def test_build_restore_index_argv_path_is_not_pathspec_escaped() -> None:
    """Unlike every other argv this module builds, `--cacheinfo`'s path is
    a literal index path, not a pathspec — `:(literal)` would corrupt it,
    not protect it, and it is never client-supplied here (only ever a
    path `git ls-files` already resolved)."""
    argv = build_restore_index_argv([("100644", "f" * 40, ":/")])
    assert argv == ["git", "update-index", "--add", "--cacheinfo", f"100644,{'f' * 40},:/"]


def test_parse_index_entries_parses_z_delimited_ls_files_stage_output() -> None:
    raw = (
        "100644 ef99f41d819e681155d1a58eca788e6999e82397 0\tf.txt\0"
        "100644 3050cb23f67f67fb30a04419f3581db3241128a0 0\tg.txt\0"
    )
    assert parse_index_entries(raw) == [
        ("100644", "ef99f41d819e681155d1a58eca788e6999e82397", "f.txt"),
        ("100644", "3050cb23f67f67fb30a04419f3581db3241128a0", "g.txt"),
    ]


def test_parse_index_entries_empty_output_is_empty_list() -> None:
    assert parse_index_entries("") == []


def test_is_nothing_to_commit_failure_detects_the_stdout_message() -> None:
    """Confirmed empirically that Git prints this on *stdout*, not
    stderr — checked here explicitly since a caller passing only the
    wrong stream would never see a match."""
    assert is_nothing_to_commit_failure("On branch main\nnothing to commit, working tree clean", "")
    assert not is_nothing_to_commit_failure("", "some other failure")
    assert not is_nothing_to_commit_failure("", "")


def test_is_nothing_to_commit_failure_also_checks_stderr() -> None:
    """Defensive: checks both streams rather than assuming which one a
    given Git version or configuration uses."""
    assert is_nothing_to_commit_failure("", "nothing to commit, working tree clean")
