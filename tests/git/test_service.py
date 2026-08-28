"""S19: Git execution plumbing (§20.1, §18.7, §44.4).

H36 (`docs/HARDENING_TODO.md`): `run_git_host_side` and its HR-002
neutralization machinery (`_discover_code_executing_overrides`,
`_host_side_git_env`, `_CODE_EXECUTING_CONFIG_KEY_RE`, the env-scrub
tuples) are gone — every structured Git invocation runs in-container now
(`run_git_in_container`, tested against a real Docker daemon in
`tests/workspace/test_git_service_container.py`, including the specific
proof that a code-executing driver this module used to neutralize now
genuinely runs). What remains here: `run_git_metadata_read` (the one
narrow, permanent host-side exception — see its own docstring),
`GitCommandResult`/`sanitize_for_client` (shared by both execution
paths), and `detect_interrupted_operation` (never ran Git at all, so H36
never touched it).
"""

import os
import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.git.service import (
    GitCommandResult,
    InterruptedOperation,
    detect_interrupted_operation,
    run_git_metadata_read,
    sanitize_for_client,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    return path


def test_git_command_result_ok_property() -> None:
    assert GitCommandResult(returncode=0, stdout="", stderr="").ok is True
    assert GitCommandResult(returncode=1, stdout="", stderr="").ok is False


# --- run_git_metadata_read (H36, `docs/HARDENING_TODO.md`) ------------------------


def test_run_git_metadata_read_returns_stdout_and_exit_code(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = run_git_metadata_read(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() in ("main", "master")


def test_run_git_metadata_read_reports_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = run_git_metadata_read(repo, ["rev-parse", "no-such-ref"])
    assert not result.ok
    assert result.returncode != 0
    assert result.stderr != ""


def test_run_git_metadata_read_finds_git_via_path(tmp_path: Path) -> None:
    """Regression guard: an earlier version of this pattern
    (`git_state.py`, before S19) passed a *replacement* `env={...}` to
    `subprocess.run`, dropping `PATH` — it happened to still work only
    because `git` lives at a location POSIX's default fallback search
    path covers on this host. A real failure here would mean `git`
    couldn't be found at all. `run_git_metadata_read` inherits the real
    environment unmodified (unlike the retired `run_git_host_side`, it
    has no scrubbing of its own to apply), so this is a simpler guard
    than its predecessor's version, not a weaker one."""
    repo = _init_repo(tmp_path / "repo")
    result = run_git_metadata_read(repo, ["--version"])
    assert result.ok
    assert "git version" in result.stdout


def test_at_hat019_a_non_utf8_filename_does_not_crash_run_git_metadata_read(
    tmp_path: Path,
) -> None:
    """`errors="surrogateescape"` keeps a genuinely invalid UTF-8 byte —
    in an untracked filename, here, appearing raw in `git status -z`'s
    own stdout — from raising `UnicodeDecodeError` — confirmed
    empirically that the default `text=True` (`errors="strict"`) does,
    the same class of regression `run_git_host_side` (retired, H36) was
    originally hardened against. Not `detect_default_branch`'s own
    current usage (a fixed `symbolic-ref` command with no client input),
    but `run_git_metadata_read` is a general-purpose primitive and this
    property is worth verifying at its own level, independent of who
    calls it today."""
    hostile_name = os.fsdecode(b"bad-\xff\xfe-name.txt")
    repo = _init_repo(tmp_path / "repo")
    (repo / hostile_name).write_bytes(b"hi")

    result = run_git_metadata_read(
        repo, ["status", "--porcelain=v2", "--untracked-files=all", "-z"]
    )  # SHALL NOT raise UnicodeDecodeError

    assert result.ok
    assert "bad-" in result.stdout


def test_sanitize_for_client_turns_surrogate_escapes_into_the_replacement_character() -> None:
    """A `errors="surrogateescape"`-decoded string (`run_git_metadata_
    read`'s own decode) is lossless but produces a string the MCP SDK's
    own response serialization cannot handle — confirmed empirically
    that `pydantic_core.to_json` raises `UnicodeEncodeError` on a lone
    surrogate with the exact same message shape `surrogateescape` exists
    to avoid at the subprocess boundary, just moved downstream.
    `sanitize_for_client` SHALL produce a string safe to serialize."""
    raw = b"bad-\xff\xfe-name.txt"
    surrogate_escaped = raw.decode("utf-8", errors="surrogateescape")
    assert "\udcff" in surrogate_escaped  # sanity: this really is surrogate-escaped

    sanitized = sanitize_for_client(surrogate_escaped)

    assert sanitized.encode("utf-8")  # SHALL NOT raise UnicodeEncodeError
    assert "�" in sanitized  # the invalid bytes became U+FFFD
    assert "bad-" in sanitized and "name.txt" in sanitized  # the valid parts survive


def test_sanitize_for_client_is_a_noop_for_ordinary_text() -> None:
    assert sanitize_for_client("ordinary/path.txt") == "ordinary/path.txt"


def test_sanitize_for_client_is_a_noop_for_an_already_replace_decoded_string() -> None:
    """H36: `run_git_in_container`'s capture path decodes with
    `errors="replace"` (already lossy) rather than `run_git_metadata_
    read`'s `errors="surrogateescape"` (lossless) — `sanitize_for_client`
    is called on either's output, and a string that already went through
    `errors="replace"` (U+FFFD already substituted) SHALL pass through
    unchanged, not be corrupted further by a second lossy pass."""
    already_replaced = b"bad-\xff\xfe-name.txt".decode("utf-8", errors="replace")
    assert sanitize_for_client(already_replaced) == already_replaced


# --- detect_interrupted_operation (§44.4) --------------------------------------------


def test_detect_interrupted_operation_none_for_a_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert detect_interrupted_operation(repo) is None


def test_detect_interrupted_operation_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / "MERGE_HEAD").write_text("deadbeef\n")
    assert detect_interrupted_operation(repo) == InterruptedOperation.MERGE


@pytest.mark.parametrize("marker_dir", ["rebase-merge", "rebase-apply"])
def test_detect_interrupted_operation_rebase(tmp_path: Path, marker_dir: str) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / marker_dir).mkdir()
    assert detect_interrupted_operation(repo) == InterruptedOperation.REBASE


def test_detect_interrupted_operation_cherry_pick(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / "CHERRY_PICK_HEAD").write_text("deadbeef\n")
    assert detect_interrupted_operation(repo) == InterruptedOperation.CHERRY_PICK


def test_detect_interrupted_operation_bisect(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / "BISECT_LOG").write_text("git bisect start\n")
    assert detect_interrupted_operation(repo) == InterruptedOperation.BISECT


def test_detect_interrupted_operation_follows_a_real_linked_worktree(tmp_path: Path) -> None:
    """§18.2's gitfile case: `.git` in the worktree is a *file* pointing
    at the real git dir elsewhere; detection must follow it, not just
    look for markers directly under the working tree's own `.git`."""
    repo = _init_repo(tmp_path / "repo")
    worktree_path = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree_path), "-b", "wt-branch"],
        cwd=repo,
        check=True,
    )
    assert (worktree_path / ".git").is_file()

    assert detect_interrupted_operation(worktree_path) is None

    # The marker lives under the *linked* git dir, not repo/.git.
    content = (worktree_path / ".git").read_text().strip()
    linked_git_dir = Path(content.removeprefix("gitdir:").strip())
    (linked_git_dir / "MERGE_HEAD").write_text("deadbeef\n")
    assert detect_interrupted_operation(worktree_path) == InterruptedOperation.MERGE


def test_detect_interrupted_operation_on_non_repo_path_reports_none(tmp_path: Path) -> None:
    """Not a Git repository at all — no `.git` to find markers under.
    Diagnostic, so this reports `None` rather than raising."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    assert detect_interrupted_operation(not_a_repo) is None
