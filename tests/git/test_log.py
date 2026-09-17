"""S21: `git_log` (§17.3's bounded output).

H36 (`docs/HARDENING_TODO.md`, 4/8): `read_git_log` itself now needs a
running workspace's container context (it runs `git log` in-container).
`_log_of` below exercises the real, unchanged `build_log_argv` +
`parse_git_log_output` pair against a real host-side `git log` — the
same split production's own `read_git_log` uses, just fed by a host
subprocess here instead of `run_git_in_container` — keeping this whole
suite fast and Docker-free. `tests/workspace/test_git_diff_log_tools.py`
covers `read_git_log` end-to-end, in-container.
"""

import subprocess
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.errors import InvalidArgumentError
from mcp_chatgpt_sandbox.git.log import GitLogEntry, build_log_argv, parse_git_log_output


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


def _log_of(
    repo: Path, *, max_entries: int = 50, paths: list[str] | None = None, ref: str | None = None
) -> list[GitLogEntry]:
    argv = build_log_argv(max_entries=max_entries, paths=paths, ref=ref)
    result = subprocess.run(
        ["git", *argv], cwd=repo, capture_output=True, text=True, errors="replace", check=False
    )
    return parse_git_log_output(result.stdout, result.stderr)


def test_log_returns_commits_newest_first(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    first_sha = _commit(repo, "a.txt", "first")
    second_sha = _commit(repo, "a.txt", "second")

    entries = _log_of(repo)
    assert [e.sha for e in entries] == [second_sha, first_sha]
    assert entries[0].subject == "second"
    assert entries[1].subject == "first"
    assert "test@example.com" in entries[0].author


def test_log_on_an_unborn_branch_is_empty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert _log_of(repo) == []


def test_log_is_bounded_by_max_entries(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    for i in range(5):
        _commit(repo, "a.txt", f"commit {i}")

    entries = _log_of(repo, max_entries=2)
    assert len(entries) == 2
    assert entries[0].subject == "commit 4"
    assert entries[1].subject == "commit 3"


def test_log_rejects_non_positive_max_entries() -> None:
    with pytest.raises(InvalidArgumentError):
        build_log_argv(max_entries=0)


def test_log_path_filter(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "touch a")
    _commit(repo, "b.txt", "touch b")
    _commit(repo, "a.txt", "touch a again")

    entries = _log_of(repo, paths=["b.txt"])
    assert [e.subject for e in entries] == ["touch b"]


def test_log_ref_selects_a_specific_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "on main")
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    _commit(repo, "b.txt", "on feature")

    main_entries = _log_of(repo, ref="main")
    assert [e.subject for e in main_entries] == ["on main"]

    feature_entries = _log_of(repo, ref="feature")
    assert [e.subject for e in feature_entries] == ["on feature", "on main"]


# --- HR-001 (docs/HARDENING_SPEC.md §4): option injection via ref ----------------


def test_at_h001_an_option_shaped_ref_is_rejected_not_executed() -> None:
    """A real regression: `ref="--output=<path>"` used to reach a
    host-side `git log` unguarded and write that file to the host, exit
    0, no error, empty result. `build_log_argv` rejects it before any
    argv (and so any git invocation at all) is ever produced."""
    with pytest.raises(InvalidArgumentError):
        build_log_argv(ref="--output=/tmp/PWNED_VIA_REF")


def test_at_h001_a_legitimate_ref_still_works(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "initial")

    entries = _log_of(repo, ref="HEAD")
    assert [e.subject for e in entries] == ["initial"]


def test_log_subject_with_special_characters_survives_intact(tmp_path: Path) -> None:
    """The chosen field separator (ASCII 0x1f/0x1e) must not collide with
    anything a real commit subject could contain."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subject = 'fix: handle | and , and " quotes correctly'
    subprocess.run(["git", "commit", "-q", "-m", subject], cwd=repo, check=True)

    entries = _log_of(repo)
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

    entries = _log_of(repo)

    assert len(entries) == 1  # not mis-split into two broken records
    assert entries[0].subject == "evil\x1esubject with a record separator"


def test_end_of_options_precedes_ref_in_the_argv() -> None:
    """RR-027 (`docs/REMEDIATION_SPEC.md` §27): every existing test for
    `validate_revision_argument` uses a leading-`-` value (see
    `ref="--output=..."` above), which trips that (first, denylist) layer
    before the second, load-bearing one (`--end-of-options`, placed
    immediately before `ref`) is ever reached. `build_log_argv` being a
    pure argv builder (H36) makes this directly inspectable — no
    subprocess spying needed the way the pre-H36 version of this test
    required."""
    argv = build_log_argv(ref="HEAD")
    ref_index = argv.index("HEAD")
    assert argv[ref_index - 1] == "--end-of-options"
