from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    ensure_private_directory,
    is_owner_only_directory,
    is_owner_only_file,
)
from dev_workspace_mcp.setup.host import StorageChoices, prepare_storage_directories
from dev_workspace_mcp.setup.model import SetupState, save_setup_state


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_swphv_008_leaf_private_policy_under_permissive_umask(tmp_path: Path) -> None:
    operator_parent = tmp_path / "operator-owned"
    operator_parent.mkdir()
    operator_parent.chmod(0o755)
    intermediate = operator_parent / "created-intermediate"
    private_leaf = intermediate / "private-leaf"

    old_umask = os.umask(0)
    try:
        ensure_private_directory(private_leaf)
    finally:
        os.umask(old_umask)

    assert _mode(operator_parent) == 0o755
    assert _mode(intermediate) == 0o777
    assert _mode(private_leaf) == 0o700
    assert is_owner_only_directory(private_leaf)


def test_swphv_008_private_leaf_fails_closed_when_it_cannot_be_secured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_leaf = tmp_path / "created" / "private-leaf"
    real_chmod = Path.chmod

    def fail_selected_leaf(path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if path == private_leaf:
            raise OSError("simulated chmod failure")
        real_chmod(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", fail_selected_leaf)
    with pytest.raises(SetupFilesystemError, match="Could not prepare setup directory"):
        ensure_private_directory(private_leaf)


def test_swphv_008_setup_state_uses_same_leaf_private_policy(tmp_path: Path) -> None:
    operator_parent = tmp_path / "operator-state-root"
    operator_parent.mkdir()
    operator_parent.chmod(0o755)
    intermediate = operator_parent / "created-intermediate"
    private_state_dir = intermediate / "setup-state"
    state_path = private_state_dir / "state.json"

    old_umask = os.umask(0)
    try:
        save_setup_state(SetupState(), state_path)
    finally:
        os.umask(old_umask)

    assert _mode(operator_parent) == 0o755
    assert _mode(intermediate) == 0o777
    assert _mode(private_state_dir) == 0o700
    assert is_owner_only_file(state_path)


def test_swphv_008_storage_helpers_use_same_leaf_private_policy(tmp_path: Path) -> None:
    operator_parent = tmp_path / "operator-storage-root"
    operator_parent.mkdir()
    operator_parent.chmod(0o755)
    choices = StorageChoices(
        state_db_path=operator_parent / "state-mid" / "private-state" / "state.db",
        audit_path=operator_parent / "audit-mid" / "private-audit" / "audit.jsonl",
        keys_dir=operator_parent / "keys-mid" / "private-keys",
        known_hosts=operator_parent / "ssh-mid" / "private-ssh" / "known_hosts",
    )

    old_umask = os.umask(0)
    try:
        prepare_storage_directories(choices, approved=True)
    finally:
        os.umask(old_umask)

    assert _mode(operator_parent) == 0o755
    for intermediate_name in ("state-mid", "audit-mid", "keys-mid", "ssh-mid"):
        assert _mode(operator_parent / intermediate_name) == 0o777
    for private_leaf in (
        choices.state_db_path.parent,
        choices.audit_path.parent,
        choices.keys_dir,
        choices.known_hosts.parent,
    ):
        assert _mode(private_leaf) == 0o700
        assert is_owner_only_directory(private_leaf)
