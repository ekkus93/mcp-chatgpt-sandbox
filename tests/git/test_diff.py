"""S21: `git_diff` (§17.3, §46)."""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.git.diff import _trim_incomplete_trailing_char, read_git_diff


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


# --- HR-001 (docs/HARDENING_SPEC.md §4): option injection via base/head ----------


def test_at_h001_an_option_shaped_base_is_rejected_not_executed(tmp_path: Path) -> None:
    """A real regression: `base="--output=<path>"` used to reach a
    host-side `git diff` unguarded and write that file to the host,
    exit 0, no error. Confirms it is rejected before git ever runs, and
    that no file is created."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    _commit_all(repo, "v1")
    target = tmp_path / "PWNED_VIA_BASE"

    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, base=f"--output={target}")

    assert not target.exists()


def test_at_h001_an_option_shaped_head_is_rejected_not_executed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    sha = _commit_all(repo, "v1")
    target = tmp_path / "PWNED_VIA_HEAD"

    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, base=sha, head=f"--output={target}")

    assert not target.exists()


def test_at_h001_a_legitimate_base_and_head_still_work(tmp_path: Path) -> None:
    """The fix must not have broken the ordinary commit-to-commit case —
    validated separately from every other diff test in this file so a
    regression here is unambiguous."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    first_sha = _commit_all(repo, "v1")
    (repo / "a.txt").write_text("v2\n")
    second_sha = _commit_all(repo, "v2")

    result = read_git_diff(repo, base=first_sha, head=second_sha)
    assert "+v2" in result.content


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


# --- H24 (`docs/HARDENING_TODO.md`, HR-022): next_offset + character-safe
# pagination + offset/max_bytes validation -----------------------------------


def test_negative_offset_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    _commit_all(repo, "v1")
    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, offset=-1)


def test_negative_max_bytes_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    _commit_all(repo, "v1")
    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, max_bytes=-1)


def test_next_offset_matches_the_actual_bytes_consumed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x\n" * 1000)
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("y\n" * 1000)

    chunk = read_git_diff(repo, max_bytes=100)
    assert chunk.next_offset == len(chunk.content.encode("utf-8"))
    assert chunk.truncated is True

    final = read_git_diff(repo, max_bytes=10**9, offset=chunk.next_offset)
    assert final.truncated is False
    assert final.next_offset == final.total_bytes


def test_at_hr022_pagination_never_splits_a_multibyte_character(tmp_path: Path) -> None:
    """A real regression: byte-offset windowing used to slice straight
    through a multi-byte UTF-8 character, so `errors="replace"` turned
    the *whole* character into U+FFFD in whichever window it landed —
    and, since the split point was computed purely from `max_bytes`
    (never returned to the caller), the following window started
    mid-character too and corrupted it a second time. Paginating with a
    tight `max_bytes` (still >= 4, the widest a single UTF-8 character
    can be, so this can never degrade to the zero-progress edge case)
    over genuinely multi-byte content must reassemble byte-for-byte
    identical to an unpaginated read, with no replacement character
    anywhere."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("original\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("日本語のテスト文字列です — café — naïve\n" * 5)

    full = read_git_diff(repo, max_bytes=10**9)
    assert "�" not in full.content  # sanity: an unpaginated read is already clean

    reassembled = ""
    offset = 0
    for _ in range(full.total_bytes + 10):  # generous bound against a stuck loop
        chunk = read_git_diff(repo, max_bytes=4, offset=offset)
        reassembled += chunk.content
        if not chunk.truncated:
            break
        assert chunk.next_offset > offset, "pagination must always make forward progress"
        offset = chunk.next_offset
    else:
        pytest.fail("pagination did not terminate")

    assert reassembled == full.content
    assert "�" not in reassembled


def test_pagination_terminates_even_when_max_bytes_is_smaller_than_one_character(
    tmp_path: Path,
) -> None:
    """The degenerate case `_trim_incomplete_trailing_char`'s "keep the
    untrimmed window" fallback exists for: `max_bytes=1` can never hold
    even the smallest multi-byte character, so trimming would otherwise
    zero out the window and make no progress, forever. Progress (and
    eventual termination) is guaranteed even though the content itself
    may be lossy at some cut points."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("original\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_text("日本語のテスト\n")

    full = read_git_diff(repo, max_bytes=10**9)

    offset = 0
    for _ in range(full.total_bytes + 10):
        chunk = read_git_diff(repo, max_bytes=1, offset=offset)
        if not chunk.truncated:
            break
        assert chunk.next_offset > offset, "pagination must always make forward progress"
        offset = chunk.next_offset
    else:
        pytest.fail("pagination did not terminate")


# --- RR-016/RR-017 (`docs/REMEDIATION_SPEC.md` §12): pagination is character-
# safe under any content, including invalid bytes ----------------------------


def test_rr016_trim_finds_a_truncated_character_past_an_earlier_invalid_byte() -> None:
    """The exact reproduction: `window.decode("utf-8")` fails on the
    leading `0xff` first (an invalid byte, not a truncated sequence —
    reason "invalid start byte", not "unexpected end of data"), which
    used to make the old implementation give up without ever examining
    whether the *trailing* bytes were themselves a split character. Here
    they are: `\\xe6\\x97` is 2 of the 3 bytes of `日` (`\\xe6\\x97\\xa5`).
    Scanning backward from the end finds and trims it regardless of what
    precedes it."""
    window = b"\xff\xe6\x97"
    assert _trim_incomplete_trailing_char(window) == b"\xff"


def test_rr016_trim_leaves_a_complete_trailing_character_untouched() -> None:
    window = "hello 日".encode()  # ends on a complete 3-byte character
    assert _trim_incomplete_trailing_char(window) == window


def test_rr016_trim_leaves_a_lone_invalid_trailing_byte_untouched() -> None:
    """Not a truncated sequence — `0xff` is never a valid UTF-8 lead byte
    at all, so there is nothing "more" that could ever complete it;
    left in place for the caller's own `errors="replace"` decode."""
    window = b"hello \xff"
    assert _trim_incomplete_trailing_char(window) == window


def test_rr016_trim_leaves_a_run_of_bare_continuation_bytes_untouched() -> None:
    """No valid lead byte within UTF-8's own 4-byte maximum lookback —
    genuinely invalid, not "truncated"; nothing to trim."""
    window = b"\x80\x80\x80\x80"
    assert _trim_incomplete_trailing_char(window) == window


def test_rr016_max_bytes_zero_is_rejected(tmp_path: Path) -> None:
    """`< 1`, not `< 0` — `max_bytes=0` used to be accepted and produce a
    zero-byte window every call, `next_offset` never advancing: reproduced
    empirically as permanent zero progress across repeated calls."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    _commit_all(repo, "v1")
    with pytest.raises(InvalidArgumentError):
        read_git_diff(repo, max_bytes=0)


def test_rr017_next_offset_is_clamped_to_total_bytes_for_an_out_of_range_offset(
    tmp_path: Path,
) -> None:
    """An `offset` already past `total_bytes` (a client re-requesting past
    the true end) used to echo that same out-of-range value back as
    `next_offset` — reproduced empirically — rather than a value that
    actually means "the end"."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("v1\n")
    _commit_all(repo, "v1")
    (repo / "a.txt").write_text("v2\n")
    full = read_git_diff(repo, max_bytes=10**9)

    result = read_git_diff(repo, max_bytes=100, offset=full.total_bytes + 99999)

    assert result.next_offset == full.total_bytes
    assert result.content == ""
    assert result.truncated is False


def test_rr016_pagination_with_an_invalid_byte_for_every_max_bytes_from_1_to_64(
    tmp_path: Path,
) -> None:
    """The checklist's own ask: a diff containing a genuinely invalid
    byte (`errors="surrogateescape"` deliberately lets it survive,
    HR-022/H19) must still paginate correctly at every `max_bytes` from
    1 through 64 — no exception, always forward progress, termination."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_bytes(b"line one\n")
    _commit_all(repo, "initial")
    (repo / "a.txt").write_bytes(
        b"line one\nline two \xff invalid byte then \xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e text\n"
    )

    full = read_git_diff(repo, max_bytes=10**9)

    for max_bytes in range(1, 65):
        offset = 0
        for _ in range(full.total_bytes + 10):
            chunk = read_git_diff(repo, max_bytes=max_bytes, offset=offset)
            if not chunk.truncated:
                break
            assert chunk.next_offset > offset, (
                f"max_bytes={max_bytes}: pagination must always make forward progress"
            )
            offset = chunk.next_offset
        else:
            pytest.fail(f"max_bytes={max_bytes}: pagination did not terminate")
