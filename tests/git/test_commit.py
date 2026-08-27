"""S22: `git_commit`'s host-side-checkable pieces (§17.4, §18.9)."""

import subprocess
from pathlib import Path

from dev_workspace_mcp.git.commit import (
    build_add_argv,
    build_commit_argv,
    build_reset_argv,
    detect_default_branch,
    is_head_published,
    is_nothing_to_commit_failure,
    read_committed_files,
)


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
# --- run_git_host_side, does not prepend it. A real regression: an earlier --------
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


# --- is_head_published ---------------------------------------------------------------


def test_is_head_published_true_for_a_pushed_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)

    assert is_head_published(seed) is True


def test_is_head_published_false_for_an_unpushed_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)

    (seed / "a.txt").write_text("a\nmore\n")
    subprocess.run(["git", "commit", "-q", "-am", "unpushed"], cwd=seed, check=True)

    assert is_head_published(seed) is False


def test_is_head_published_false_without_any_upstream(tmp_path: Path) -> None:
    """No upstream at all means categorically nothing to have been
    published to — not "unknown, assume published"."""
    repo = _init_repo(tmp_path / "repo")
    assert is_head_published(repo) is False


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

    assert read_committed_files(repo, head_sha) == ["a.txt"]


def test_read_committed_files_reports_a_normal_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "b.txt").write_text("b\n")
    (repo / "c.txt").write_text("c\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert read_committed_files(repo, head_sha) == ["b.txt", "c.txt"]


def test_read_committed_files_returns_empty_for_an_unknown_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert read_committed_files(repo, "0" * 40) == []


# --- HR-025: rollback on a failed commit; a specific code for "nothing to commit" -


def test_build_reset_argv_uses_literal_pathspecs() -> None:
    argv = build_reset_argv(["a.txt", ":/"])
    assert argv == ["git", "reset", "--", ":(literal)a.txt", ":(literal):/"]


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
