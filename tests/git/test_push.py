"""S27: `git_push`'s host-side-checkable refspec-construction and
outcome-classification pieces (§20.3, §20.5, §18.9)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.git.push import (
    PUSH_NON_FAST_FORWARD_MARKERS,
    build_force_with_lease_arg,
    build_push_argv,
    build_refspec,
    is_push_non_fast_forward_failure,
    validate_branch_name,
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", message], repo)


def _clone_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    _run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], tmp_path)
    work = tmp_path / "work"
    _run(["git", "clone", "-q", str(remote), str(work)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], work)
    _run(["git", "config", "user.name", "Test"], work)
    _commit(work, "README.md", "hello\n", "initial")
    _run(["git", "push", "-q", "origin", "main"], work)
    return remote, work


# --- validate_branch_name -----------------------------------------------------------


@pytest.mark.parametrize("branch", ["main", "feature/x", "release-1.0"])
def test_validate_branch_name_accepts_ordinary_names(branch: str) -> None:
    validate_branch_name(branch)  # does not raise


@pytest.mark.parametrize(
    "branch", ["../etc/passwd", "-oops", "a..b", "", "a b", "HEAD", "refs/heads/main"]
)
def test_validate_branch_name_rejects_dangerous_or_invalid_names(branch: str) -> None:
    with pytest.raises(InvalidArgumentError):
        validate_branch_name(branch)


# --- build_refspec (§20.3's table, literally) ---------------------------------------


def test_default_refspec() -> None:
    assert (
        build_refspec("main", ref_type="branch", force=False, delete=False)
        == "refs/heads/main:refs/heads/main"
    )


def test_force_refspec_is_prefixed_with_plus() -> None:
    assert (
        build_refspec("main", ref_type="branch", force=True, delete=False)
        == "+refs/heads/main:refs/heads/main"
    )


def test_force_with_lease_refspec_is_the_same_as_default() -> None:
    """§20.3's table: `force_with_lease` uses the default refspec shape —
    its extra safety comes from the CLI flag, not a `+` prefix."""
    assert (
        build_refspec("main", ref_type="branch", force=False, delete=False)
        == "refs/heads/main:refs/heads/main"
    )


def test_delete_branch_refspec() -> None:
    assert (
        build_refspec("stale", ref_type="branch", force=False, delete=True) == ":refs/heads/stale"
    )


def test_delete_tag_refspec() -> None:
    assert build_refspec("v1.0", ref_type="tag", force=False, delete=True) == ":refs/tags/v1.0"


# --- build_force_with_lease_arg -------------------------------------------------------


def test_force_with_lease_arg_includes_the_known_remote_tracking_sha(tmp_path: Path) -> None:
    _remote, work = _clone_pair(tmp_path)
    expected_sha = _run(["git", "rev-parse", "origin/main"], work).stdout.strip()

    arg = build_force_with_lease_arg(work, remote="origin", branch="main")

    assert arg == f"--force-with-lease=refs/heads/main:{expected_sha}"


def test_force_with_lease_arg_is_bare_without_a_known_tracking_ref(tmp_path: Path) -> None:
    repo = tmp_path / "solo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    _commit(repo, "a.txt", "a\n", "initial")

    arg = build_force_with_lease_arg(repo, remote="origin", branch="main")

    assert arg == "--force-with-lease"


# --- build_push_argv -----------------------------------------------------------------


def test_build_push_argv_shape_without_force_with_lease() -> None:
    argv = build_push_argv("origin", "refs/heads/main:refs/heads/main")
    assert argv == [
        "git",
        "-c",
        "protocol.ext.allow=never",
        "push",
        "--atomic",
        "--recurse-submodules=no",
        "origin",
        "refs/heads/main:refs/heads/main",
    ]


def test_build_push_argv_includes_force_with_lease_arg_before_remote() -> None:
    argv = build_push_argv(
        "origin",
        "refs/heads/main:refs/heads/main",
        force_with_lease_arg="--force-with-lease=refs/heads/main:abc123",
    )
    assert argv[-3:] == [
        "--force-with-lease=refs/heads/main:abc123",
        "origin",
        "refs/heads/main:refs/heads/main",
    ]


def test_build_push_argv_never_passes_mirror_all_or_tags() -> None:
    argv = build_push_argv("origin", "refs/heads/main:refs/heads/main")
    joined = " ".join(argv)
    assert "--mirror" not in joined
    assert " --all" not in joined
    assert "--tags" not in joined  # --recurse-submodules=no contains no bare "--tags"


# --- is_push_non_fast_forward_failure (real, diverged push) ------------------------


def test_real_diverged_push_is_rejected_with_a_recognized_marker(tmp_path: Path) -> None:
    remote, work_a = _clone_pair(tmp_path)
    work_b = tmp_path / "work_b"
    _run(["git", "clone", "-q", str(remote), str(work_b)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], work_b)
    _run(["git", "config", "user.name", "Test"], work_b)

    _commit(work_a, "a.txt", "a\n", "from a")
    _run(["git", "push", "-q", "origin", "main"], work_a)

    _commit(work_b, "b.txt", "b\n", "from b")
    result = subprocess.run(
        ["git", "push", "origin", "main"], cwd=work_b, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert is_push_non_fast_forward_failure(result.stderr)


def test_is_push_non_fast_forward_failure_false_for_an_unrelated_failure() -> None:
    assert is_push_non_fast_forward_failure("fatal: repository not found\n") is False


@pytest.mark.parametrize("marker", PUSH_NON_FAST_FORWARD_MARKERS)
def test_is_push_non_fast_forward_failure_recognizes_every_documented_marker(marker: str) -> None:
    assert is_push_non_fast_forward_failure(f" ! [rejected]  main -> main {marker}\n") is True
