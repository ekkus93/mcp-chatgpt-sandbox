"""S20: `git_status`'s structured output (§17.2, §44.4, AT-020).

H36 (`docs/HARDENING_TODO.md`, 2/8): `read_git_status` itself now needs a
running workspace's container context (it runs `git status` in-container)
— these tests exercise `parse_git_status_output`, the pure parser it
calls, directly instead, via `_status_of` below: a real host-side
`git status --porcelain=v2 ...` (never itself the point of H36 — no
concern this test suite tests for depends on *where* Git ran) feeds the
same parser `read_git_status` does, keeping every one of these fast,
Docker-free, and exercising the exact parsing logic production code
runs. `tests/workspace/test_git_status_tool.py` covers `read_git_status`
end-to-end, in-container."""

import subprocess
from pathlib import Path

from mcp_chatgpt_sandbox.git.service import InterruptedOperation, detect_interrupted_operation
from mcp_chatgpt_sandbox.git.status import GitStatus, head_sha_matches, parse_git_status_output


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


def _status_of(repo: Path) -> GitStatus:
    # `errors="replace"`, not the default strict UTF-8 decoding — a
    # repository-tracked filename is not guaranteed to be valid UTF-8
    # (HR-022, `docs/HARDENING_SPEC.md` §10), and this needs to match
    # `execution/service.py`'s `capture`, the decoding `parse_git_status_
    # output`'s real (post-H36, in-container) caller actually hands it,
    # so this test exercises the parser against the same shape of input
    # production does.
    result = subprocess.run(
        ["git", "status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    )
    return parse_git_status_output(
        result.stdout, interrupted_operation=detect_interrupted_operation(repo)
    )


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

    status = _status_of(repo)

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

    status = _status_of(repo)

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

    status = _status_of(repo)
    assert status.staged == ["a.txt"]
    assert status.unstaged == ["a.txt"]


def test_status_on_a_renamed_staged_file(tmp_path: Path) -> None:
    """HR-024 (`docs/HARDENING_SPEC.md` §11): `staged` includes *both*
    the new and old path for a staged rename, not only the new one — a
    real regression: `git commit --only -- <newpath>` alone, without the
    old path also named, was reproduced to silently commit only the new
    file's creation, leaving the old path staged-for-deletion and
    outside the commit entirely. A caller relying on this list to know
    what to pass to `git_commit` needs to see both names."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("line one\nline two\nline three\n")
    _commit_all(repo, "initial")
    subprocess.run(["git", "mv", "a.txt", "a-renamed.txt"], cwd=repo, check=True)

    status = _status_of(repo)
    assert status.staged == ["a-renamed.txt", "a.txt"]
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

    status = _status_of(local)
    assert status.upstream is not None
    assert status.ahead == 1
    assert status.behind == 1


def test_status_on_detached_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\n")
    head_sha = _commit_all(repo, "initial")
    subprocess.run(["git", "checkout", "-q", head_sha], cwd=repo, check=True)

    status = _status_of(repo)
    assert status.detached_head is True
    assert status.branch is None
    assert status.head_sha == head_sha


def test_status_on_an_unborn_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    status = _status_of(repo)
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

    status = _status_of(repo)
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

    status = _status_of(repo)

    assert len(status.untracked) == 1
    assert "bad-" in status.untracked[0]
    assert status.untracked[0].encode("utf-8")  # SHALL NOT raise UnicodeEncodeError
    assert status.clean is False


# --- H24 (`docs/HARDENING_TODO.md`, HR-022): head_sha_matches accepts an
# abbreviated expected_head_sha ------------------------------------------------


def test_head_sha_matches_a_full_sha() -> None:
    sha = "a" * 40
    assert head_sha_matches(sha, sha) is True


def test_head_sha_matches_an_abbreviated_prefix() -> None:
    """A real regression: `git_commit`/`git_pull`'s `expected_head_sha`
    was compared with `!=` against the always-full `head_sha` — a
    caller-supplied abbreviated SHA (the ordinary way one is copied from
    `git log --oneline`, a PR description, etc.) could never match,
    even when it correctly identified the current HEAD."""
    full = "abc1234567890abc1234567890abc1234567890"
    assert head_sha_matches(full, full[:7]) is True


def test_head_sha_matches_rejects_a_mismatched_prefix() -> None:
    full = "abc1234567890abc1234567890abc1234567890"
    assert head_sha_matches(full, "def4567") is False


def test_head_sha_matches_rejects_a_too_short_abbreviation() -> None:
    """A 1-3 char value trivially prefix-matches almost any SHA and isn't
    how anyone actually copies one — `MIN_ABBREVIATED_SHA_LENGTH` refuses
    it rather than treat it as a legitimate abbreviation."""
    full = "abc1234567890abc1234567890abc1234567890"
    assert head_sha_matches(full, "abc") is False
    assert head_sha_matches(full, "") is False


def test_head_sha_matches_is_false_for_an_unborn_branch() -> None:
    assert head_sha_matches(None, "abc1234") is False
