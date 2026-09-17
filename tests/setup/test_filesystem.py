from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    backup_regular_file,
    ensure_private_directory,
    is_owner_only_directory,
    is_owner_only_file,
    read_bounded_utf8,
)


def test_wizv_003_atomic_new_file_is_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "private" / "state.json"
    result = atomic_write_text(target, "hello\n", create_backup=False)
    assert target.read_text() == "hello\n"
    assert result.backup_path is None
    assert is_owner_only_file(target)
    assert is_owner_only_directory(target.parent)


def test_swhv_001_existing_parent_mode_is_not_changed_by_write(tmp_path: Path) -> None:
    parent = tmp_path / "operator-owned"
    parent.mkdir()
    parent.chmod(0o755)
    target = parent / "config.yml"

    atomic_write_text(target, "version: 1\n", create_backup=False)

    assert target.read_text() == "version: 1\n"
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


def test_swhv_001_missing_setup_owned_directory_is_created_private(tmp_path: Path) -> None:
    setup_dir = tmp_path / "missing" / "setup-state"

    ensure_private_directory(setup_dir)

    assert is_owner_only_directory(setup_dir)


def test_wizv_003_replacement_creates_timestamped_owner_only_backup(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    target.write_text("old\n")
    result = atomic_write_text(target, "new\n")
    assert target.read_text() == "new\n"
    assert result.backup_path is not None
    assert result.backup_path.read_text() == "old\n"
    assert is_owner_only_file(result.backup_path)


def test_wizv_003_existing_file_mode_is_restricted_after_write(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old")
    target.chmod(0o666)
    atomic_write_text(target, "new", create_backup=False)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_wizv_003_backup_name_is_collision_safe(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    target.write_text("old")
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    first = backup_regular_file(target, now=now)
    second = backup_regular_file(target, now=now)
    assert first is not None and second is not None and first != second


def test_wizv_003_symlink_target_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("secret")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(SetupFilesystemError, match="symlink"):
        atomic_write_text(link, "replacement")
    assert real.read_text() == "secret"


def test_wizv_003_bounded_read_rejects_oversized_and_invalid_utf8(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"abcd")
    with pytest.raises(SetupFilesystemError, match="safety limit"):
        read_bounded_utf8(oversized, max_bytes=3)

    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"\xff")
    with pytest.raises(SetupFilesystemError, match="UTF-8"):
        read_bounded_utf8(invalid)


def test_wizv_003_failed_replace_leaves_original_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.yml"
    target.write_text("old\n")
    original_replace = os.replace

    def fail_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        del src, dst
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(SetupFilesystemError, match="Could not update"):
        atomic_write_text(target, "new\n")
    monkeypatch.setattr(os, "replace", original_replace)
    assert target.read_text() == "old\n"
