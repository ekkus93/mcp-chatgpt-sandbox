from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_workspace_mcp.setup.config_writer import (
    SetupConfigError,
    commit_config_update,
    inspect_config,
    prepare_config_update,
)


def _minimal(tmp_path: Path) -> dict[str, object]:
    return {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
        "docker": {"default_image": "mcp-dev-test:24.04"},
        "ssh": {
            "keys_dir": str(tmp_path / "keys"),
            "known_hosts": str(tmp_path / "known_hosts"),
        },
        "audit": {"path": str(tmp_path / "audit.jsonl")},
    }


def test_wizv_004_new_minimal_plan_is_production_model_valid(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    values = _minimal(tmp_path)
    plan = prepare_config_update(target, wizard_values=values, managed_fields=set(values))
    assert not plan.source_exists
    assert "new configuration" in plan.summary[0]
    parsed = yaml.safe_load(plan.candidate_text)
    assert parsed["docker"]["default_image"] == "mcp-dev-test:24.04"


def test_wizv_004_existing_advanced_values_are_preserved(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    existing = _minimal(tmp_path)
    existing["filesystem"] = {"max_read_bytes": 12345}
    target.write_text(yaml.safe_dump(existing, sort_keys=False))

    plan = prepare_config_update(
        target,
        wizard_values={"logging": {"level": "DEBUG"}},
        managed_fields={"logging"},
    )
    candidate = yaml.safe_load(plan.candidate_text)
    assert candidate["filesystem"] == {"max_read_bytes": 12345}
    assert candidate["logging"] == {"level": "DEBUG"}


def test_wizv_004_unknown_or_duplicate_yaml_remains_rejected(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    valid = _minimal(tmp_path)
    valid["unknown"] = True
    target.write_text(yaml.safe_dump(valid))
    assert inspect_config(target).valid is False
    with pytest.raises(SetupConfigError, match="explicit repair"):
        prepare_config_update(
            target,
            wizard_values=_minimal(tmp_path),
            managed_fields=set(_minimal(tmp_path)),
        )

    target.write_text(
        "version: 1\nversion: 1\nstate_db_path: /tmp/state\n"
        "docker: {default_image: image}\n"
        "ssh: {keys_dir: /tmp/keys, known_hosts: /tmp/known}\n"
        "audit: {path: /tmp/audit}\n"
    )
    assert inspect_config(target).valid is False


def test_wizv_004_repair_requires_approval_and_summary_covers_safety_changes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yml"
    target.write_text("not: [valid")
    values = _minimal(tmp_path)
    values["git_policy"] = {"force_push": "deny", "pull_strategy": "ff_only"}
    with pytest.raises(SetupConfigError, match="explicit repair"):
        prepare_config_update(target, wizard_values=values, managed_fields=set(values))

    plan = prepare_config_update(
        target,
        wizard_values=values,
        managed_fields=set(values),
        repair_invalid=True,
    )
    assert plan.repaired_invalid_source
    assert any(line.startswith("git_policy.force_push:") for line in plan.summary)


def test_wizv_004_repository_summary_reports_add_remove_and_change(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    existing = _minimal(tmp_path)
    existing["repositories"] = [
        {"id": "old", "path": "/srv/old", "github": "org/old"},
        {"id": "changed", "path": "/srv/changed", "github": "org/changed"},
    ]
    target.write_text(yaml.safe_dump(existing, sort_keys=False))
    new_repositories = [
        {"id": "changed", "path": "/srv/changed2", "github": "org/changed"},
        {"id": "new", "path": "/srv/new", "github": "org/new"},
    ]
    plan = prepare_config_update(
        target,
        wizard_values={"repositories": new_repositories},
        managed_fields={"repositories"},
    )
    assert "repository - old" in plan.summary
    assert "repository ~ changed" in plan.summary
    assert "repository + new" in plan.summary


def test_wizv_005_new_file_commit_reloads_production_config(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    values = _minimal(tmp_path)
    plan = prepare_config_update(target, wizard_values=values, managed_fields=set(values))
    result = commit_config_update(target, plan)
    assert result.backup_path is None
    assert result.config.docker.default_image == "mcp-dev-test:24.04"
    assert target.read_text() == plan.candidate_text


def test_wizv_005_existing_update_creates_recoverable_backup(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    existing = _minimal(tmp_path)
    target.write_text(yaml.safe_dump(existing, sort_keys=False))
    plan = prepare_config_update(
        target,
        wizard_values={"logging": {"level": "DEBUG"}},
        managed_fields={"logging"},
    )
    result = commit_config_update(target, plan)
    assert result.backup_path is not None
    assert yaml.safe_load(result.backup_path.read_text()) == existing
    assert result.config.logging.level == "DEBUG"


def test_wizv_005_source_change_after_review_blocks_commit(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    existing = _minimal(tmp_path)
    target.write_text(yaml.safe_dump(existing, sort_keys=False))
    plan = prepare_config_update(
        target,
        wizard_values={"logging": {"level": "DEBUG"}},
        managed_fields={"logging"},
    )
    target.write_text(target.read_text() + "# concurrent edit\n")
    with pytest.raises(SetupConfigError, match="changed after review"):
        commit_config_update(target, plan)


def test_wizv_005_production_reload_failure_preserves_backup(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    existing = _minimal(tmp_path)
    target.write_text(yaml.safe_dump(existing, sort_keys=False))
    plan = prepare_config_update(
        target,
        wizard_values={"repository_roots": [str(tmp_path / "does-not-exist")]},
        managed_fields={"repository_roots"},
    )
    with pytest.raises(SetupConfigError, match="backup was preserved"):
        commit_config_update(target, plan)
    backups = list(tmp_path.glob("config.yml.bak.*"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text()) == existing
