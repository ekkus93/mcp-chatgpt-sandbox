"""S21: `git_diff` (§17.3, §46)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.git.diff import read_git_diff


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


def _commit_all(path: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_working_tree_diff_shows_unstaged_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("line one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("line one\nline two\n")

    result = read_git_diff(repo)
    assert "line two" in result.content
    assert "a.txt" in result.content
    assert result.truncated is False


def test_working_tree_diff_excludes_staged_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("line one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("line one\nstaged\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)

    result = read_git_diff(repo)
    assert result.content == ""


def test_staged_diff_shows_only_staged_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("line one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("line one\nstaged\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    (repo / "a.txt").write_text("line one\nstaged\nunstaged\n")

    result = read_git_diff(repo, staged=True)
    assert "staged" in result.content
    assert "unstaged" not in result.content


def test_commit_to_commit_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    first_sha = _commit_all(repo, "v1")
    (repo / "a.txt").write_text("v2\n")
    second_sha = _commit_all(repo, "v2")

    result = read_git_diff(repo, base=first_sha, head=second_sha)
    assert "-v1" in result.content
    assert "+v2" in result.content


def test_commit_to_commit_diff_head_defaults_to_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    first_sha = _commit_all(repo, "v1")
    (repo / "a.txt").write_text("v2\n")
    _commit_all(repo, "v2")

    result = read_git_diff(repo, base=first_sha)
    assert "+v2" in result.content


def test_base_and_staged_together_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    sha = _commit_all(repo, "v1")
    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, staged=True, base=sha)


def test_head_without_base_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    sha = _commit_all(repo, "v1")
    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, head=sha)


def test_path_filter_restricts_the_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    (repo / "b.txt").write_text("one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("one\nchanged-a\n")
    (repo / "b.txt").write_text("one\nchanged-b\n")

    result = read_git_diff(repo, paths=["a.txt"])
    assert "changed-a" in result.content
    assert "changed-b" not in result.content


def test_stat_only_mode(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("one\ntwo\nthree\n")

    result = read_git_diff(repo, stat_only=True)
    assert "a.txt" in result.content
    assert "+++" not in result.content  # not a full patch
    assert "insertion" in result.content or "changed" in result.content


def test_output_is_bounded_by_max_bytes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n" * 1000)
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("y\n" * 1000)

    result = read_git_diff(repo, max_bytes=50)
    assert len(result.content.encode("utf-8")) <= 50
    assert result.truncated is True
    assert result.total_bytes > 50


def test_offset_retrieves_the_remainder(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n" * 1000)
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("y\n" * 1000)

    full = read_git_diff(repo, max_bytes=10**9)
    first_chunk = read_git_diff(repo, max_bytes=100)
    second_chunk = read_git_diff(repo, max_bytes=10**9, offset=100)
    assert first_chunk.content + second_chunk.content == full.content
    assert second_chunk.truncated is False
