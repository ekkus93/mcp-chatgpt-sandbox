"""S22: `git_commit`'s host-side-checkable pieces (§17.4, §18.9)."""

import subprocess
from pathlib import Path

from dev_workspace_mcp.git.commit import (
    build_add_argv,
    build_commit_argv,
    detect_default_branch,
    is_head_published,
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
    assert argv == ["git", "add", "--", "a.txt", "b.txt"]


def test_build_commit_argv_includes_git() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt"], amend=False, no_verify=False)
    assert argv[0] == "git"
    assert argv == ["git", "commit", "--only", "-m", "msg", "--", "a.txt"]


def test_build_commit_argv_amend_and_no_verify() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt"], amend=True, no_verify=True)
    assert "--amend" in argv
    assert "--no-verify" in argv
    # paths stay last, after "--", regardless of flag order.
    assert argv[-2:] == ["--", "a.txt"]


def test_build_commit_argv_multiple_paths() -> None:
    argv = build_commit_argv(message="msg", paths=["a.txt", "b.txt"], amend=False, no_verify=False)
    assert argv[-2:] == ["a.txt", "b.txt"]


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
