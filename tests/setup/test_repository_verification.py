"""Read-only setup repository verification tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dev_workspace_mcp.setup.model import SetupRepository
from dev_workspace_mcp.setup.repository_verification import verify_repository


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_repository_verification_accepts_matching_origin(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "remote", "add", "origin", "git@github.com:owner/demo.git")
    before = (repository / ".git" / "config").read_bytes()
    result = verify_repository(
        SetupRepository(id="demo", path=str(repository), github="owner/demo")
    )
    after = (repository / ".git" / "config").read_bytes()
    assert result.valid
    assert result.normalized_github == "owner/demo"
    assert before == after


def test_repository_verification_rejects_remote_mismatch(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "remote", "add", "origin", "https://github.com/owner/other.git")
    result = verify_repository(
        SetupRepository(id="demo", path=str(repository), github="owner/demo")
    )
    assert not result.valid
    assert "not configured" in result.message


def test_repository_verification_rejects_non_git_directory(tmp_path: Path) -> None:
    result = verify_repository(SetupRepository(id="demo", path=str(tmp_path), github="owner/demo"))
    assert not result.valid
    assert "not a Git working tree" in result.message
