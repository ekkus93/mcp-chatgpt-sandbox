"""Fail-closed recovery contracts for reviewed setup config commits."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_workspace_mcp.config import ConfigError
from dev_workspace_mcp.setup.config_plan import prepare_config_update
from dev_workspace_mcp.setup.config_writer import ConfigCommitError, commit_config_update
from dev_workspace_mcp.setup.filesystem import AtomicWriteResult, SetupFilesystemError
from dev_workspace_mcp.setup.model import SetupState


def _state(tmp_path: Path, *, image: str = "mcp-dev-test-s7:24.04") -> SetupState:
    return SetupState(
        state_db_path=str(tmp_path / "state.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        ssh_keys_dir=str(tmp_path / "keys"),
        known_hosts_path=str(tmp_path / "known_hosts"),
        docker_image=image,
    )


def test_post_write_byte_mismatch_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yml"
    state = _state(tmp_path)
    plan = prepare_config_update(target, state)

    def corrupting_write(
        path: Path,
        text: str,
        *,
        mode: int = 0o600,
        create_backup: bool = False,
    ) -> AtomicWriteResult:
        del mode, create_backup
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "# unexpected mutation\n", encoding="utf-8")
        return AtomicWriteResult(path=path, backup_path=None)

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.config_writer.atomic_write_text",
        corrupting_write,
    )

    with pytest.raises(ConfigCommitError, match="did not match reviewed candidate"):
        commit_config_update(target, plan, state)


def test_atomic_write_failure_remains_a_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yml"
    state = _state(tmp_path)
    plan = prepare_config_update(target, state)

    def fail_write(*_args: object, **_kwargs: object) -> AtomicWriteResult:
        raise SetupFilesystemError("simulated filesystem failure")

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.config_writer.atomic_write_text",
        fail_write,
    )

    with pytest.raises(ConfigCommitError, match="Could not atomically update configuration"):
        commit_config_update(target, plan, state)


def test_post_write_production_reload_failure_preserves_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yml"
    initial_state = _state(tmp_path, image="first-image")
    initial_plan = prepare_config_update(target, initial_state)
    commit_config_update(target, initial_plan, initial_state)
    original = target.read_bytes()

    changed_state = _state(tmp_path, image="second-image")
    changed_plan = prepare_config_update(target, changed_state)

    def reject_reload(_path: Path) -> object:
        raise ConfigError("simulated production reload failure")

    monkeypatch.setattr("dev_workspace_mcp.setup.config_writer.load_config", reject_reload)

    with pytest.raises(ConfigCommitError, match="timestamped backup was preserved"):
        commit_config_update(target, changed_plan, changed_state)

    backups = sorted(tmp_path.glob("config.yml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert target.read_text(encoding="utf-8") == changed_plan.candidate_text


def test_explicit_repair_backs_up_invalid_source(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    invalid = b"not: production-valid\n"
    target.write_bytes(invalid)
    state = _state(tmp_path)

    plan = prepare_config_update(target, state, repair_invalid=True)
    result = commit_config_update(target, plan, state)

    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == invalid
