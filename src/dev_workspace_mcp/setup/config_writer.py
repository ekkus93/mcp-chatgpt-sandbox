"""CAS-bound atomic commit of an exact reviewed setup configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.config import ConfigError, ServerConfig, load_config
from dev_workspace_mcp.setup.config_plan import (
    ConfigPlanError,
    ConfigUpdatePlan,
    _digest_text,
    _read_existing,
    state_digest,
)
from dev_workspace_mcp.setup.filesystem import SetupFilesystemError, atomic_write_text
from dev_workspace_mcp.setup.model import SetupState


class ConfigCommitError(RuntimeError):
    """Secret-safe failure while committing a reviewed configuration."""


@dataclass(frozen=True, slots=True)
class ConfigCommitResult:
    path: Path
    backup_path: Path | None
    config: ServerConfig


def commit_config_update(
    path: Path,
    plan: ConfigUpdatePlan,
    state: SetupState,
) -> ConfigCommitResult:
    """Write only if both source bytes and wizard choices still match review."""

    if state_digest(SetupState.from_dict(state.to_dict())) != plan.state_digest:
        raise ConfigCommitError("Setup inputs changed after review; review configuration again")
    try:
        exists, raw = _read_existing(path)
    except ConfigPlanError as exc:
        raise ConfigCommitError(str(exc)) from exc
    digest = _digest_text(raw) if exists else None
    if exists != plan.source_exists or digest != plan.source_digest:
        raise ConfigCommitError("Configuration source changed after review; review again")

    try:
        result = atomic_write_text(
            path,
            plan.candidate_text,
            mode=0o600,
            create_backup=exists,
        )
    except SetupFilesystemError as exc:
        raise ConfigCommitError(f"Could not atomically update configuration at {path}") from exc

    try:
        persisted = result.path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigCommitError("Could not verify newly written configuration") from exc
    if persisted != plan.candidate_text:
        raise ConfigCommitError("Written configuration did not match reviewed candidate")

    try:
        config = load_config(result.path)
    except ConfigError as exc:
        raise ConfigCommitError(
            "Written configuration failed production reload; timestamped backup was preserved"
        ) from exc
    return ConfigCommitResult(path=result.path, backup_path=result.backup_path, config=config)


__all__ = ["ConfigCommitError", "ConfigCommitResult", "commit_config_update"]
