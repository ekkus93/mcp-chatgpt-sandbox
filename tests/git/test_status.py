"""S20: `git_status`'s structured output (§17.2, §44.4, AT-020)."""

import subprocess
from pathlib import Path

from dev_workspace_mcp.git.service import InterruptedOperation
from dev_workspace_mcp.git.status import read_git_status


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


def test_status_on_a_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("hello\n")
    head_sha = _commit_all(repo, "initial")

    status = read_git_status(repo)

    assert status.branch == "main"
    assert status.head_sha == head_sha
    assert status.detached_head is False
    assert status.clean is True
    assert status.staged == []
    assert status.unstaged == []
    assert status.untracked == []
    assert status.conflicted == []
    assert status.interrupted_operation is None


def test_status_reports_staged_and_unstaged_and_untracked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    (repo / "b.txt").write_text("two\n")
    _commit_all(repo, "initial")

    (repo / "a.txt").write_text("one\nstaged-change\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    (repo / "b.txt").write_text("two\nunstaged-change\n")
    (repo / "c.txt").write_text("new\n")

    status = read_git_status(repo)

    assert status.clean is False
    assert status.staged == ["a.txt"]
    assert status.unstaged == ["b.txt"]
    assert status.untracked == ["c.txt"]


def test_status_reports_a_path_both_staged_and_unstaged(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    _commit_all(repo, "initial")

    (repo / "a.txt").write_text("one\nstaged\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    (repo / "a.txt").write_text("one\nstaged\nunstaged\n")

    status = read_git_status(repo)
    assert status.staged == ["a.txt"]
    assert status.unstaged == ["a.txt"]


def test_status_on_a_renamed_staged_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("line one\nline two\nline three\n")
    _commit_all(repo, "initial")
    subprocess.run(["git", "mv", "a.txt", "a-renamed.txt"], cwd=repo, check=True)

    status = read_git_status(repo)
    assert status.staged == ["a-renamed.txt"]
    assert status.unstaged == []
    # The parser must have consumed the origPath token correctly — a bug
    # there would leave "a.txt" misparsed as a bogus record afterward.
    assert status.clean is False


def test_status_reports_ahead_and_behind(tmp_path: Path) -> None:
    remote = _init_repo(tmp_path / "remote")
    (remote / "a.txt").write_text("one\n")
    _commit_all(remote, "initial")
    subprocess.run(["git", "config", "receive.denyCurrentBranch", "ignore"], cwd=remote, check=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=local, check=True)

    # Local gets one commit ahead.
    (local / "b.txt").write_text("two\n")
    _commit_all(local, "local commit")

    # Remote gets one commit local doesn't have, then local fetches it.
    (remote / "c.txt").write_text("three\n")
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "add", "-A"],
        cwd=remote,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            "remote commit",
        ],
        cwd=remote,
        check=True,
    )
    subprocess.run(["git", "fetch", "-q"], cwd=local, check=True)

    status = read_git_status(local)
    assert status.upstream is not None
    assert status.ahead == 1
    assert status.behind == 1


def test_status_on_detached_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    head_sha = _commit_all(repo, "initial")
    subprocess.run(["git", "checkout", "-q", head_sha], cwd=repo, check=True)

    status = read_git_status(repo)
    assert status.detached_head is True
    assert status.branch is None
    assert status.head_sha == head_sha


def test_status_on_an_unborn_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    status = read_git_status(repo)
    assert status.head_sha is None
    assert status.branch == "main"


def test_status_reports_merge_conflict_rather_than_repairing_it(tmp_path: Path) -> None:
    """AT-020."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("base\n")
    _commit_all(repo, "base")

    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    (repo / "a.txt").write_text("feature change\n")
    _commit_all(repo, "feature change")

    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("main change\n")
    _commit_all(repo, "main change")

    subprocess.run(["git", "merge", "-q", "feature"], cwd=repo, check=False)  # expected to conflict

    status = read_git_status(repo)
    assert status.conflicted == ["a.txt"]
    assert status.interrupted_operation == InterruptedOperation.MERGE
    assert status.clean is False


def test_at_hat019_a_non_utf8_filename_is_listed_untracked_and_json_safe(
    tmp_path: Path,
) -> None:
    """HAT-019: a repository containing a non-UTF-8 filename SHALL be
    statusable — and the reported path SHALL be safe to hand back to an
    MCP client (JSON serialization on a lone surrogate escape crashes,
    confirmed empirically), not merely not crash `read_git_status`
    itself."""
    import os

    repo = _init_repo(tmp_path / "repo")
    hostile_name = os.fsdecode(b"bad-\xff\xfe-name.txt")
    (repo / hostile_name).write_bytes(b"hi")

    status = read_git_status(repo)

    assert len(status.untracked) == 1
    assert "bad-" in status.untracked[0]
    assert status.untracked[0].encode("utf-8")  # SHALL NOT raise UnicodeEncodeError
    assert status.clean is False
