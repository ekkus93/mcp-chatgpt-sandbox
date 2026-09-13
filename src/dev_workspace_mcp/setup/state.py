"""Secure persistence for versioned non-secret setup progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    read_text_bounded,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.model import SetupState

DEFAULT_SETUP_STATE_PATH = Path("~/.config/dev-workspace-mcp/setup-state.json")
_MAX_STATE_BYTES = 1024 * 1024


class SetupStateError(RuntimeError):
    """Secret-safe setup-state load/save failure."""


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate setup-state key: {key!r}")
        result[key] = value
    return result


def load_setup_state(path: Path = DEFAULT_SETUP_STATE_PATH) -> SetupState | None:
    expanded = path.expanduser()
    try:
        reject_unsafe_sensitive_target(expanded)
    except SetupFilesystemError as exc:
        raise SetupStateError(str(exc)) from exc
    if not expanded.exists():
        return None
    try:
        raw = read_text_bounded(expanded, max_bytes=_MAX_STATE_BYTES)
        data = json.loads(raw, object_pairs_hook=_no_duplicate_object)
        return SetupState.from_dict(data)
    except (SetupFilesystemError, json.JSONDecodeError, ValueError) as exc:
        raise SetupStateError(f"Invalid setup state at {expanded}: {exc}") from exc


def save_setup_state(
    state: SetupState,
    path: Path = DEFAULT_SETUP_STATE_PATH,
    *,
    create_backup: bool = False,
) -> Path:
    # Round-trip through the strict schema before materializing bytes.
    validated = SetupState.from_dict(state.to_dict())
    payload = json.dumps(validated.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        result = atomic_write_text(
            path.expanduser(), payload, mode=0o600, create_backup=create_backup
        )
    except SetupFilesystemError as exc:
        raise SetupStateError(f"Could not persist setup state at {path.expanduser()}") from exc
    return result.path


__all__ = ["DEFAULT_SETUP_STATE_PATH", "SetupStateError", "load_setup_state", "save_setup_state"]
