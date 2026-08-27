"""S21: `git_log` (§17.3's bounded output)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.git.log import read_git_log


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


def _commit(path: Path, filename: str, message: str) -> str:
    (path / filename).write_text(f"{message}\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_log_returns_commits_newest_first(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    first_sha = _commit(repo, "a.txt", "first")
    second_sha = _commit(repo, "a.txt", "second")

    entries = read_git_log(repo)
    assert [e.sha for e in entries] == [second_sha, first_sha]
    assert entries[0].subject == "second"
    assert entries[1].subject == "first"
    assert "test@example.com" in entries[0].author


def test_log_on_an_unborn_branch_is_empty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert read_git_log(repo) == []


def test_log_is_bounded_by_max_entries(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    for i in range(5):
        _commit(repo, "a.txt", f"commit {i}")

    entries = read_git_log(repo, max_entries=2)
    assert len(entries) == 2
    assert entries[0].subject == "commit 4"
    assert entries[1].subject == "commit 3"


def test_log_rejects_non_positive_max_entries(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "first")
    with pytest.raises(InvalidArgumentError):
        read_git_log(repo, max_entries=0)


def test_log_path_filter(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "touch a")
    _commit(repo, "b.txt", "touch b")
    _commit(repo, "a.txt", "touch a again")

    entries = read_git_log(repo, paths=["b.txt"])
    assert [e.subject for e in entries] == ["touch b"]


def test_log_ref_selects_a_specific_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "on main")
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    _commit(repo, "b.txt", "on feature")

    main_entries = read_git_log(repo, ref="main")
    assert [e.subject for e in main_entries] == ["on main"]

    feature_entries = read_git_log(repo, ref="feature")
    assert [e.subject for e in feature_entries] == ["on feature", "on main"]


def test_log_subject_with_special_characters_survives_intact(tmp_path: Path) -> None:
    """The chosen field separator (ASCII 0x1f/0x1e) must not collide with
    anything a real commit subject could contain."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subject = 'fix: handle | and , and " quotes correctly'
    subprocess.run(["git", "commit", "-q", "-m", subject], cwd=repo, check=True)

    entries = read_git_log(repo)
    assert entries[0].subject == subject
