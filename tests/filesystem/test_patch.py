"""S10: unified-diff parsing and application (§13.4)."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.filesystem.patch import (
    PatchFormatError,
    apply_unified_diff,
    parse_unified_diff,
)

ORIGINAL = "one\ntwo\nthree\nfour\nfive\n"

SIMPLE_DIFF = "--- a/file.txt\n+++ b/file.txt\n@@ -2,1 +2,1 @@\n-two\n+TWO\n"


def test_parse_simple_hunk() -> None:
    hunks = parse_unified_diff(SIMPLE_DIFF)
    assert len(hunks) == 1
    assert hunks[0].old_start == 2
    assert hunks[0].old_lines == ["two"]
    assert hunks[0].new_lines == ["TWO"]


def test_parse_rejects_diff_with_no_hunks() -> None:
    with pytest.raises(PatchFormatError):
        parse_unified_diff("--- a/file.txt\n+++ b/file.txt\n")


def test_parse_rejects_truncated_hunk() -> None:
    with pytest.raises(PatchFormatError):
        parse_unified_diff("@@ -1,2 +1,2 @@\n one\n")


def test_apply_simple_replacement() -> None:
    result = apply_unified_diff(ORIGINAL, SIMPLE_DIFF)
    assert result.applied is True
    assert result.new_content == "one\nTWO\nthree\nfour\nfive\n"
    assert result.rejected_hunks == []


def test_apply_with_context_lines() -> None:
    diff = "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is True
    assert result.new_content == "one\nTWO\nthree\nfour\nfive\n"


def test_apply_insertion_only_hunk() -> None:
    diff = "@@ -2,0 +3,1 @@\n+INSERTED\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is True
    assert result.new_content == "one\ntwo\nINSERTED\nthree\nfour\nfive\n"


def test_apply_deletion_only_hunk() -> None:
    diff = "@@ -2,1 +1,0 @@\n-two\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is True
    assert result.new_content == "one\nthree\nfour\nfive\n"


def test_apply_multiple_hunks_with_offset_tracking() -> None:
    diff = "@@ -1,1 +1,2 @@\n one\n+INSERTED\n@@ -4,1 +5,1 @@\n-four\n+FOUR\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is True
    assert result.new_content == "one\nINSERTED\ntwo\nthree\nFOUR\nfive\n"


def test_apply_rejects_hunk_whose_context_does_not_match() -> None:
    diff = "@@ -2,1 +2,1 @@\n-not-actually-there\n+TWO\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is False
    assert result.new_content is None
    assert len(result.rejected_hunks) == 1
    assert result.rejected_hunks[0].old_start == 2


def test_apply_is_atomic_one_bad_hunk_rejects_the_whole_patch() -> None:
    """A patch with one good hunk and one mismatching hunk applies
    neither — never a half-applied file."""
    diff = "@@ -1,1 +1,1 @@\n-one\n+ONE\n@@ -4,1 +4,1 @@\n-not-four\n+FOUR\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is False
    assert result.new_content is None
    assert len(result.rejected_hunks) == 1
    assert result.rejected_hunks[0].old_start == 4


def test_apply_ignores_no_newline_marker() -> None:
    diff = "@@ -5,1 +5,1 @@\n-five\n+FIVE\n\\ No newline at end of file\n"
    result = apply_unified_diff(ORIGINAL, diff)
    assert result.applied is True
    assert "FIVE" in result.new_content


# --- cross-checked against real `diff -u` output, not just hand-built hunks -----


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("one\ntwo\nthree\nfour\nfive\n", "one\ntwo\nINSERTED\nthree\nfour\nfive\n"),
        ("one\ntwo\nthree\nfour\nfive\n", "ZERO\none\ntwo\nthree\nfour\nfive\n"),
        ("one\ntwo\nthree\nfour\nfive\n", "one\ntwo\nthree\nfour\nfive\nLAST\n"),
        ("one\ntwo\nthree\nfour\nfive\n", "one\nTWO\nthree\nFOUR\nfive\n"),
        ("one\ntwo\nthree\nfour\nfive\n", "one\nfive\n"),
    ],
)
def test_apply_matches_real_diff_output(before: str, after: str) -> None:
    """Generates a real unified diff with GNU `diff -u`, applies it with
    `apply_unified_diff`, and checks the result equals `after` exactly —
    proving compatibility with what a real diffing tool actually emits,
    not just the hunks this test file happens to hand-construct."""
    with tempfile.TemporaryDirectory() as tmp:
        before_path = Path(tmp) / "before.txt"
        after_path = Path(tmp) / "after.txt"
        before_path.write_text(before)
        after_path.write_text(after)
        result = subprocess.run(
            ["diff", "-u", str(before_path), str(after_path)],
            capture_output=True,
            text=True,
        )
    assert result.returncode == 1  # diff: files differ, as expected

    patched = apply_unified_diff(before, result.stdout)
    assert patched.applied is True
    assert patched.new_content == after
