from __future__ import annotations

import os
import stat
from pathlib import Path

from dev_workspace_mcp.setup.filesystem import (
    PRIVATE_DIRECTORY_POLICY,
    ensure_private_directory,
)
from dev_workspace_mcp.setup.host import StorageChoices, prepare_storage_directories
from dev_workspace_mcp.setup.model import SetupState, save_setup_state


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_swphv_008_leaf_private_policy_under_permissive_umask(tmp_path: Path) -> None:
    intermediate = tmp_path / "umask-owned-intermediate"
    leaf = intermediate / "private-leaf"

    previous = os.umask(0)
    try:
        ensure_private_directory(leaf)
    finally:
        os.umask(previous)

    assert PRIVATE_DIRECTORY_POLICY == "leaf-private"
    assert _mode(leaf) == 0o700
    assert _mode(intermediate) == 0o777


def test_swphv_008_existing_parent_is_never_chmodded(tmp_path: Path) -> None:
    existing = tmp_path / "operator-owned"
    existing.mkdir()
    existing.chmod(0o755)

    ensure_private_directory(existing)

    assert _mode(existing) == 0o755


def test_swphv_008_setup_state_uses_same_leaf_private_policy(tmp_path: Path) -> None:
    intermediate = tmp_path / "state-intermediate"
    private_parent = intermediate / "state-private"
    state_path = private_parent / "state.json"

    previous = os.umask(0)
    try:
        save_setup_state(SetupState(), state_path)
    finally:
        os.umask(previous)

    assert _mode(private_parent) == 0o700
    assert _mode(intermediate) == 0o777
    assert _mode(state_path) == 0o600


def test_swphv_008_storage_creation_secures_only_missing_selected_leaves(tmp_path: Path) -> None:
    intermediate = tmp_path / "storage-intermediate"
    existing_audit_parent = tmp_path / "operator-audit-parent"
    existing_audit_parent.mkdir()
    existing_audit_parent.chmod(0o755)

    choices = StorageChoices(
        state_db_path=intermediate / "state-private" / "state.db",
        audit_path=existing_audit_parent / "audit.jsonl",
        keys_dir=intermediate / "keys-private",
        known_hosts=intermediate / "known-hosts-private" / "known_hosts",
    )

    previous = os.umask(0)
    try:
        prepare_storage_directories(choices, approved=True)
    finally:
        os.umask(previous)

    assert _mode(intermediate) == 0o777
    assert _mode(choices.state_db_path.parent) == 0o700
    assert _mode(choices.keys_dir) == 0o700
    assert _mode(choices.known_hosts.parent) == 0o700
    assert _mode(existing_audit_parent) == 0o755
