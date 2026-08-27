"""S19: Git execution plumbing (§20.1, §18.7, §44.4)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.git.service import (
    GitCommandResult,
    InterruptedOperation,
    detect_interrupted_operation,
    run_git_host_side,
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


# --- run_git_host_side ---------------------------------------------------------------


def test_run_git_host_side_returns_stdout_and_exit_code(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = run_git_host_side(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() in ("main", "master")


def test_run_git_host_side_reports_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = run_git_host_side(repo, ["rev-parse", "no-such-ref"])
    assert not result.ok
    assert result.returncode != 0
    assert result.stderr != ""


def test_git_command_result_ok_property() -> None:
    assert GitCommandResult(returncode=0, stdout="", stderr="").ok is True
    assert GitCommandResult(returncode=1, stdout="", stderr="").ok is False


def test_run_git_host_side_finds_git_via_path(tmp_path: Path) -> None:
    """Regression guard: an earlier version of this pattern
    (`git_state.py`, before S19) passed a *replacement* `env={...}` to
    `subprocess.run`, dropping `PATH` — it happened to still work only
    because `git` lives at a location POSIX's default fallback search
    path covers on this host. A real failure here would mean `git`
    couldn't be found at all."""
    repo = _init_repo(tmp_path / "repo")
    result = run_git_host_side(repo, ["--version"])
    assert result.ok
    assert "git version" in result.stdout


def test_run_git_host_side_disables_fsmonitor_hook(tmp_path: Path) -> None:
    """AT-025: the one hook-like mechanism a plain `status`/`diff`/`log`
    can actually trigger — no standard Git hook fires on those commands,
    which is exactly why §18.7 singles fsmonitor out alongside
    `core.hooksPath`. Proven both ways: our neutralized call does not
    invoke the script, and the same repo *without* our override does —
    so this demonstrates suppression, not just "the script never ran
    for some unrelated reason"."""
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "fsmonitor-ran.marker"
    hook_script = tmp_path / "fake-fsmonitor.sh"
    hook_script.write_text(f"#!/bin/sh\ntouch {marker}\necho 1\n")
    hook_script.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook_script)], cwd=repo, check=True)

    run_git_host_side(repo, ["status", "--porcelain"])
    assert not marker.exists(), "fsmonitor hook ran despite core.fsmonitor= override"

    # Sanity check: the same repo, without our override, does invoke it.
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True)
    assert marker.exists(), "fsmonitor hook mechanism itself is not actually being tested"


def test_run_git_host_side_hooks_path_override_takes_precedence(tmp_path: Path) -> None:
    """`core.hooksPath` itself: our override value wins over whatever the
    repository's own config says, regardless of what mechanism would
    have consulted it."""
    repo = _init_repo(tmp_path / "repo")
    malicious_hooks_dir = repo / "not-the-real-hooks-dir"
    malicious_hooks_dir.mkdir()
    subprocess.run(
        ["git", "config", "core.hooksPath", str(malicious_hooks_dir)], cwd=repo, check=True
    )

    # Without our override, git reports the repo-configured value.
    unmediated = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert unmediated.stdout.strip() == str(malicious_hooks_dir)

    # `run_git_host_side` always passes its own `-c core.hooksPath=...`
    # ahead of the repository's, so a `git config` call routed through
    # it reports our override, not the repo's.
    result = run_git_host_side(repo, ["config", "core.hooksPath"])
    assert result.ok
    assert result.stdout.strip() != str(malicious_hooks_dir)


def test_run_git_host_side_disables_optional_locks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = run_git_host_side(repo, ["status", "--porcelain"])
    assert result.ok
    assert not (repo / ".git" / "index.lock").exists()


# --- HR-002 (docs/HARDENING_SPEC.md §4): code-executing config neutralization --------


def _configure_filter_driver(repo: Path, marker: Path, *, driver_name: str) -> None:
    """Binds `README.md` to a `clean` filter that touches `marker` — the same
    driver git status itself was verified, empirically, to invoke."""
    script = marker.parent / f"{marker.name}.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    (repo / ".gitattributes").write_text(f"README.md filter={driver_name}\n")
    subprocess.run(
        ["git", "config", f"filter.{driver_name}.clean", str(script)], cwd=repo, check=True
    )


def test_run_git_host_side_neutralizes_a_locally_configured_filter_driver(
    tmp_path: Path,
) -> None:
    """`filter.<name>.clean` is not a hook and not `fsmonitor` — it is
    exactly the class HR-002's dynamic discovery exists for. Proven both
    ways, matching this file's own `fsmonitor` test above: our call does
    not invoke the script, and the same repo without mediation does.

    Forced via `diff`, not `status`: verified empirically that plain
    `git status` does not *reliably* invoke a content-level clean filter
    for a simple stat-detectable modification (git's own stat-based
    "racily clean" heuristic can skip it) — `git diff` genuinely needs
    the rendered content to build a patch, so it is the deterministic
    choice for a unit test that must reproduce the same way every run."""
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "filter-clean-ran.marker"
    _configure_filter_driver(repo, marker, driver_name="hardening")
    (repo / "README.md").write_text("changed\n")

    run_git_host_side(repo, ["diff"])
    assert not marker.exists(), "filter.clean ran despite dynamic neutralization"

    subprocess.run(["git", "-C", str(repo), "diff"], check=True)
    assert marker.exists(), "filter.clean mechanism itself is not actually being tested"


def test_run_git_host_side_neutralizes_an_arbitrary_unlisted_driver_name(
    tmp_path: Path,
) -> None:
    """The mechanism is discovery, not a static list: a driver name no
    one anticipated is neutralized identically, because it is read back
    out of the repository's own configuration rather than matched
    against `_CODE_EXECUTING_CONFIG_KEY_RE`'s literal examples."""
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "arbitrary-driver-ran.marker"
    _configure_filter_driver(repo, marker, driver_name="zzz-nobody-expected-this-9000")
    (repo / "README.md").write_text("changed\n")

    run_git_host_side(repo, ["diff"])
    assert not marker.exists()


def test_run_git_host_side_neutralizes_diff_external(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "diff-external-ran.marker"
    script = tmp_path / "diff-external.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\necho ext\n")
    script.chmod(0o755)
    subprocess.run(["git", "config", "diff.external", str(script)], cwd=repo, check=True)
    (repo / "README.md").write_text("changed\n")

    run_git_host_side(repo, ["diff"])
    assert not marker.exists()


def test_run_git_host_side_ignores_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HR-002(3): `GIT_CONFIG_GLOBAL=/dev/null` — a host-side read
    depends only on the repository's own configuration, never the host's.
    A dangerous key set only at global scope (never local/worktree, so
    the dynamic-discovery mechanism above would not even see it) must
    still never reach the real `git` invocation."""
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "global-hook-ran.marker"
    global_config = tmp_path / "fake-global-gitconfig"
    script = tmp_path / "global-driver.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\necho global\n")
    script.chmod(0o755)
    global_config.write_text(f"[diff]\n\texternal = {script}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    (repo / "README.md").write_text("changed\n")

    # baseline: the same env, direct subprocess — confirms the global
    # config genuinely would be honored without `run_git_host_side`'s
    # override.
    subprocess.run(["git", "-C", str(repo), "diff"], check=True)
    assert marker.exists(), "the global-config mechanism itself is not actually being tested"
    marker.unlink()

    run_git_host_side(repo, ["diff"])
    assert not marker.exists(), "run_git_host_side honored host global config"


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
