"""S26: `git_pull`'s host-side-checkable fetch/integrate mechanics (§20.5,
§18.8). Pure argv construction and outcome classification, exercised
against real local Git repositories — no Docker, no remote-identity
validation (that's S25's `git/remote.py`, tested separately; `git_pull`
itself never runs with an unvalidated remote — see
`tests/workspace/test_git_pull_tool.py` for that end-to-end path)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.git.pull import (
    NON_FAST_FORWARD_MARKER,
    build_fetch_argv,
    build_integrate_argv,
    is_non_fast_forward_failure,
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", message], repo)


def _clone_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A bare `remote.git` and a `work` clone of it, both with one shared
    commit — the common setup every test below builds on."""
    remote = tmp_path / "remote.git"
    _run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], tmp_path)

    seed = tmp_path / "seed"
    _run(["git", "clone", "-q", str(remote), str(seed)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], seed)
    _run(["git", "config", "user.name", "Test"], seed)
    _commit(seed, "README.md", "hello\n", "initial")
    _run(["git", "push", "-q", "origin", "main"], seed)

    work = tmp_path / "work"
    _run(["git", "clone", "-q", str(remote), str(work)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], work)
    _run(["git", "config", "user.name", "Test"], work)
    return remote, work


# --- build_fetch_argv ---------------------------------------------------------------


def test_build_fetch_argv_always_disables_submodule_recursion() -> None:
    """§18.8, AT-030's actual mechanism: never recurse into submodules on
    a structured pull's fetch step."""
    assert build_fetch_argv("origin") == ["git", "fetch", "--no-recurse-submodules", "origin"]


def test_build_fetch_argv_uses_the_given_remote_name() -> None:
    assert "upstream" in build_fetch_argv("upstream")


# --- build_integrate_argv ------------------------------------------------------------


def test_build_integrate_argv_ff_only() -> None:
    assert build_integrate_argv("ff_only", remote="origin", branch="main") == [
        "git",
        "merge",
        "--ff-only",
        "origin/main",
    ]


def test_build_integrate_argv_merge() -> None:
    assert build_integrate_argv("merge", remote="origin", branch="main") == [
        "git",
        "merge",
        "origin/main",
    ]


def test_build_integrate_argv_rebase() -> None:
    assert build_integrate_argv("rebase", remote="origin", branch="main") == [
        "git",
        "rebase",
        "origin/main",
    ]


def test_build_integrate_argv_uses_the_remote_tracking_branch_of_the_same_name() -> None:
    assert build_integrate_argv("ff_only", remote="upstream", branch="feature") == [
        "git",
        "merge",
        "--ff-only",
        "upstream/feature",
    ]


# --- is_non_fast_forward_failure ------------------------------------------------------


def test_is_non_fast_forward_failure_true_for_gits_own_wording() -> None:
    assert is_non_fast_forward_failure(f"fatal: {NON_FAST_FORWARD_MARKER}, aborting.\n") is True


def test_is_non_fast_forward_failure_false_for_an_unrelated_failure() -> None:
    assert is_non_fast_forward_failure("fatal: not a git repository\n") is False


# --- real end-to-end fetch/integrate, against real local repositories --------------


def test_fast_forward_pull_succeeds_and_updates_head(tmp_path: Path) -> None:
    remote, work = _clone_pair(tmp_path)

    # advance the remote by one commit the local clone doesn't have yet
    seed = tmp_path / "seed"
    _commit(seed, "b.txt", "b\n", "second")
    _run(["git", "push", "-q", "origin", "main"], seed)

    subprocess.run(build_fetch_argv("origin"), cwd=work, check=True, capture_output=True)
    result = subprocess.run(
        build_integrate_argv("ff_only", remote="origin", branch="main"),
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    head = _run(["git", "rev-parse", "HEAD"], work).stdout.strip()
    remote_head = _run(["git", "rev-parse", "main"], remote).stdout.strip()
    assert head == remote_head


def test_diverged_history_fails_ff_only_with_the_non_fast_forward_marker(tmp_path: Path) -> None:
    """The real, empirically-verified failure mode `git_pull` (S26)
    relies on to raise `GIT_NON_FAST_FORWARD` rather than escalate."""
    remote, work = _clone_pair(tmp_path)

    # diverge: a local-only commit in `work` *and* a remote-only commit
    _commit(work, "local.txt", "local\n", "local change")
    seed = tmp_path / "seed"
    _commit(seed, "remote.txt", "remote\n", "remote change")
    _run(["git", "push", "-q", "origin", "main"], seed)

    subprocess.run(build_fetch_argv("origin"), cwd=work, check=True, capture_output=True)
    result = subprocess.run(
        build_integrate_argv("ff_only", remote="origin", branch="main"),
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert is_non_fast_forward_failure(result.stderr)


def test_already_up_to_date_ff_only_succeeds_as_a_no_op(tmp_path: Path) -> None:
    _remote, work = _clone_pair(tmp_path)
    before = _run(["git", "rev-parse", "HEAD"], work).stdout.strip()

    subprocess.run(build_fetch_argv("origin"), cwd=work, check=True, capture_output=True)
    result = subprocess.run(
        build_integrate_argv("ff_only", remote="origin", branch="main"),
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    after = _run(["git", "rev-parse", "HEAD"], work).stdout.strip()
    assert after == before


def test_merge_strategy_creates_a_merge_commit_on_true_divergence(tmp_path: Path) -> None:
    remote, work = _clone_pair(tmp_path)
    _commit(work, "local.txt", "local\n", "local change")
    seed = tmp_path / "seed"
    _commit(seed, "remote.txt", "remote\n", "remote change")
    _run(["git", "push", "-q", "origin", "main"], seed)

    subprocess.run(build_fetch_argv("origin"), cwd=work, check=True, capture_output=True)
    result = subprocess.run(
        build_integrate_argv("merge", remote="origin", branch="main"),
        cwd=work,
        capture_output=True,
        text=True,
        input="",
    )
    assert result.returncode == 0, result.stderr
    parents = _run(["git", "log", "-1", "--pretty=%P"], work).stdout.split()
    assert len(parents) == 2  # a real merge commit, not a fast-forward


def test_rebase_strategy_replays_local_commits_onto_the_remote(tmp_path: Path) -> None:
    remote, work = _clone_pair(tmp_path)
    _commit(work, "local.txt", "local\n", "local change")
    seed = tmp_path / "seed"
    _commit(seed, "remote.txt", "remote\n", "remote change")
    _run(["git", "push", "-q", "origin", "main"], seed)

    subprocess.run(build_fetch_argv("origin"), cwd=work, check=True, capture_output=True)
    result = subprocess.run(
        build_integrate_argv("rebase", remote="origin", branch="main"),
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    log = _run(["git", "log", "--oneline"], work).stdout
    assert "local change" in log
    assert "remote change" in log
    parents = _run(["git", "log", "-1", "--pretty=%P"], work).stdout.split()
    assert len(parents) == 1  # rebased onto remote, not merged


@pytest.mark.parametrize("strategy", ["ff_only", "merge", "rebase"])
def test_integrate_argv_never_recurses_submodules_implicitly(strategy: str) -> None:
    """Only the fetch step needs an explicit flag (§18.8) — none of the
    integration commands touch the network or a submodule's remote at
    all, so none of them should carry a submodule flag."""
    argv = build_integrate_argv(strategy, remote="origin", branch="main")  # type: ignore[arg-type]
    assert "--recurse-submodules" not in " ".join(argv)
