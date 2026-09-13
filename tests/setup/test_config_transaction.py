"""Reviewed configuration plan and commit contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_workspace_mcp.setup.config_plan import ConfigPlanError, prepare_config_update
from dev_workspace_mcp.setup.config_writer import ConfigCommitError, commit_config_update
from dev_workspace_mcp.setup.model import SetupRepository, SetupState, SetupStep, SetupStepStatus


def _state(tmp_path: Path) -> SetupState:
    repository = tmp_path / "repo"
    repository.mkdir(exist_ok=True)
    return SetupState(
        state_db_path=str(tmp_path / "state.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        ssh_keys_dir=str(tmp_path / "keys"),
        known_hosts_path=str(tmp_path / "known_hosts"),
        docker_image="mcp-dev-test:latest",
        repositories=(
            SetupRepository(id="demo", path=str(repository), github="owner/demo", ssh_key=None),
        ),
        statuses={SetupStep.REPOSITORY_ACCESS: SetupStepStatus.COMPLETE},
    )


def test_new_config_plan_and_commit_round_trip(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    plan = prepare_config_update(target, state)
    result = commit_config_update(target, plan, state)
    assert result.config.docker.default_image == "mcp-dev-test:latest"
    assert result.config.repositories[0].id == "demo"
    assert target.stat().st_mode & 0o777 == 0o600
    assert result.backup_path is None


def test_existing_advanced_settings_are_preserved(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    initial = {
        "version": 1,
        "state_db_path": str(tmp_path / "old-state.db"),
        "docker": {"default_image": "old", "workspace_path": "/custom-workspace"},
        "ssh": {"keys_dir": str(tmp_path / "old-keys"), "known_hosts": str(tmp_path / "old-hosts")},
        "audit": {"path": str(tmp_path / "old-audit")},
        "logging": {"level": "DEBUG"},
        "filesystem": {"max_read_bytes": 1234},
    }
    target.write_text(yaml.safe_dump(initial, sort_keys=False), encoding="utf-8")
    plan = prepare_config_update(target, state)
    data = yaml.safe_load(plan.candidate_text)
    assert data["logging"]["level"] == "DEBUG"
    assert data["filesystem"]["max_read_bytes"] == 1234
    assert data["docker"]["workspace_path"] == "/custom-workspace"


def test_duplicate_yaml_keys_are_rejected_without_repair(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    target.write_text("version: 1\nversion: 1\n", encoding="utf-8")
    with pytest.raises(ConfigPlanError, match="explicitly approve repair"):
        prepare_config_update(target, _state(tmp_path))


def test_source_change_after_review_blocks_commit(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    plan = prepare_config_update(target, state)
    target.write_text("changed: true\n", encoding="utf-8")
    with pytest.raises(ConfigCommitError, match="source changed"):
        commit_config_update(target, plan, state)


def test_state_change_after_review_blocks_commit(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    plan = prepare_config_update(target, state)
    state.docker_image = "other-image"
    with pytest.raises(ConfigCommitError, match="inputs changed"):
        commit_config_update(target, plan, state)


def test_existing_file_gets_backup(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    first = prepare_config_update(target, state)
    commit_config_update(target, first, state)
    state.docker_image = "second-image"
    second = prepare_config_update(target, state)
    result = commit_config_update(target, second, state)
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_invalid_existing_config_requires_explicit_repair(tmp_path: Path) -> None:
    state = _state(tmp_path)
    target = tmp_path / "config.yml"
    target.write_text("not: valid-for-production\n", encoding="utf-8")
    with pytest.raises(ConfigPlanError, match="explicitly approve repair"):
        prepare_config_update(target, state)
    plan = prepare_config_update(target, state, repair_invalid=True)
    assert plan.repaired_invalid_source
