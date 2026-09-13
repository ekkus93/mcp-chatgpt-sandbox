"""Filesystem and setup-state persistence safety tests."""

from pathlib import Path

import pytest

from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    read_text_bounded,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.model import SetupState, SetupStep
from dev_workspace_mcp.setup.state import SetupStateError, load_setup_state, save_setup_state


def test_atomic_write_creates_owner_only_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "state.json"
    result = atomic_write_text(target, "hello\n")
    assert result.path.read_text(encoding="utf-8") == "hello\n"
    assert result.backup_path is None
    assert result.path.stat().st_mode & 0o777 == 0o600


def test_atomic_replacement_creates_timestamped_backup(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    target.write_text("old\n", encoding="utf-8")
    result = atomic_write_text(target, "new\n", create_backup=True)
    assert target.read_text(encoding="utf-8") == "new\n"
    assert result.backup_path is not None
    assert result.backup_path.read_text(encoding="utf-8") == "old\n"


def test_sensitive_target_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(SetupFilesystemError, match="symlink"):
        reject_unsafe_sensitive_target(link / "config.yml")


def test_bounded_read_rejects_oversize(tmp_path: Path) -> None:
    target = tmp_path / "large"
    target.write_bytes(b"x" * 11)
    with pytest.raises(SetupFilesystemError, match="size limit"):
        read_text_bounded(target, max_bytes=10)


def test_bounded_read_rejects_invalid_utf8(tmp_path: Path) -> None:
    target = tmp_path / "bad"
    target.write_bytes(b"\xff")
    with pytest.raises(SetupFilesystemError, match="UTF-8"):
        read_text_bounded(target)


def test_setup_state_round_trip_uses_restrictive_mode(tmp_path: Path) -> None:
    target = tmp_path / "setup-state.json"
    state = SetupState(current_step=SetupStep.SERVER_STORAGE, config_path="/tmp/config.yml")
    save_setup_state(state, target)
    loaded = load_setup_state(target)
    assert loaded is not None
    assert loaded.to_dict() == state.to_dict()
    assert target.stat().st_mode & 0o777 == 0o600


def test_setup_state_rejects_duplicate_json_key(tmp_path: Path) -> None:
    target = tmp_path / "setup-state.json"
    target.write_text('{"version": 1, "version": 1}\n', encoding="utf-8")
    with pytest.raises(SetupStateError, match="duplicate"):
        load_setup_state(target)


def test_failed_atomic_destination_shape_leaves_original(tmp_path: Path) -> None:
    target = tmp_path / "directory"
    target.mkdir()
    marker = target / "marker"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(SetupFilesystemError, match="non-regular"):
        atomic_write_text(target, "replacement")
    assert marker.read_text(encoding="utf-8") == "untouched"


def test_atomic_writer_rejects_group_permissions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="group/other"):
        atomic_write_text(tmp_path / "bad", "x", mode=0o640)


def test_state_file_is_not_executable(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    save_setup_state(SetupState(), target)
    assert target.stat().st_mode & 0o111 == 0
