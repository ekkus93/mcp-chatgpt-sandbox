"""S21: `git_log` (§17.3's bounded output)."""

import subprocess
from pathlib import Path

import pytest

import dev_workspace_mcp.git.service as git_service
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


# --- HR-001 (docs/HARDENING_SPEC.md §4): option injection via ref ----------------


def test_at_h001_an_option_shaped_ref_is_rejected_not_executed(tmp_path: Path) -> None:
    """A real regression: `ref="--output=<path>"` used to reach a
    host-side `git log` unguarded and write that file to the host, exit
    0, no error, empty result. Confirms it is rejected before git ever
    runs, and that no file is created."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "initial")
    target = tmp_path / "PWNED_VIA_REF"

    with pytest.raises(InvalidArgumentError):
        read_git_log(repo, ref=f"--output={target}")

    assert not target.exists()


def test_at_h001_a_legitimate_ref_still_works(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "initial")

    entries = read_git_log(repo, ref="HEAD")
    assert [e.subject for e in entries] == ["initial"]


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


def test_at_hat019_a_subject_containing_the_record_separator_does_not_break_parsing(
    tmp_path: Path,
) -> None:
    """HAT-019: a commit subject containing `\\x1e` SHALL not break
    `git_log`. Confirmed empirically that Git places no restriction on a
    commit message containing `\\x1e` (unlike NUL, which it refuses
    outright) — the earlier `\\x1e`-as-record-separator design assumed
    otherwise and mis-split exactly this case."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    msgfile = tmp_path / "commitmsg.txt"
    msgfile.write_bytes(b"evil\x1esubject with a record separator")
    subprocess.run(["git", "commit", "-q", "-F", str(msgfile)], cwd=repo, check=True)

    entries = read_git_log(repo)

    assert len(entries) == 1  # not mis-split into two broken records
    assert entries[0].subject == "evil\x1esubject with a record separator"


def test_rr027_end_of_options_precedes_ref_in_the_real_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RR-027 (`docs/REMEDIATION_SPEC.md` §27): every existing test for
    `validate_revision_argument` uses a leading-`-` value (see
    `ref=f"--output={target}"` above), which trips that (first,
    denylist) layer before the second, load-bearing one
    (`--end-of-options`, placed immediately before `ref` in the real
    argv `run_git_host_side` executes) is ever reached — deleting the
    `cmd.append(END_OF_OPTIONS)` line left the whole suite green. Spies
    on the real `subprocess.run` call, using an ordinary, non-hostile
    revision (`"HEAD"`) specifically so `validate_revision_argument`
    passes and the second layer is what this test actually exercises."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "first")

    captured_argv: list[list[str]] = []
    real_run = subprocess.run

    def _spy(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_argv.append(list(argv))
        return real_run(argv, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_service.subprocess, "run", _spy)

    entries = read_git_log(repo, ref="HEAD")
    assert entries  # sanity: the read actually succeeded

    # `_discover_code_executing_overrides` (HR-002) makes its own,
    # preceding `git config --list` calls through the same spy — the
    # actual `log` invocation is whichever captured call is a `log`.
    log_argvs = [argv for argv in captured_argv if "log" in argv]
    assert log_argvs, f"no 'log' invocation captured; got: {captured_argv}"
    argv = log_argvs[0]
    ref_index = argv.index("HEAD")
    assert argv[ref_index - 1] == "--end-of-options"
