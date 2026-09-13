"""Temporary WIZ-001 resolver probe; removed after committing exact lock output."""

from __future__ import annotations

import subprocess


def test_uv_lock_matches_committed_resolution() -> None:
    result = subprocess.run(
        ["git", "diff", "--", "uv.lock"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not result.stdout, "uv sync generated this required lock delta:\n" + result.stdout
