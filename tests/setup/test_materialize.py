from __future__ import annotations

from pathlib import Path

import yaml

from mcp_chatgpt_sandbox.config import load_config
from mcp_chatgpt_sandbox.setup.config_writer import prepare_config_update
from mcp_chatgpt_sandbox.setup.materialize import (
    choices_from_config,
    materialize_setup,
    state_from_existing_config,
)
from mcp_chatgpt_sandbox.setup.model import SetupState


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "state_db_path": str(tmp_path / "state.db"),
                "docker": {"default_image": "custom:1"},
                "container": {"sudo": False},
                "ssh": {
                    "keys_dir": str(tmp_path / "keys"),
                    "known_hosts": str(tmp_path / "known_hosts"),
                },
                "github": {"repositories": ["org/allowed"]},
                "audit": {"enabled": False, "path": str(tmp_path / "audit.jsonl")},
            },
            sort_keys=False,
        )
    )
    return path


def test_wizv_007_edit_prepopulates_production_values(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state = state_from_existing_config(config_path, advanced=False)
    assert state.choices.docker_image == "custom:1"
    assert state.choices.state_db_path == str(tmp_path / "state.db")


def test_wizv_013_materialization_and_deep_merge_preserve_unedited_subfields(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    state = state_from_existing_config(config_path, advanced=False)
    state.reviewed_fields = frozenset({"docker", "container", "github", "audit"})
    materialized = materialize_setup(state, new_config=False)
    plan = prepare_config_update(
        config_path,
        wizard_values=materialized.values,
        managed_fields=materialized.managed_fields,
    )
    candidate = yaml.safe_load(plan.candidate_text)
    assert candidate["container"]["sudo"] is False
    assert candidate["github"]["repositories"] == ["org/allowed"]
    assert candidate["audit"]["enabled"] is False


def test_wizv_016_fresh_materialization_uses_production_defaults_for_unreviewed_fields(
    tmp_path: Path,
) -> None:
    state = SetupState()
    state.choices.state_db_path = str(tmp_path / "state.db")
    state.choices.audit_path = str(tmp_path / "audit.jsonl")
    state.choices.keys_dir = str(tmp_path / "keys")
    state.choices.known_hosts = str(tmp_path / "known_hosts")
    state.reviewed_fields = frozenset(
        {
            "state_db_path",
            "audit",
            "docker",
            "workspace",
            "container",
            "ssh",
            "github",
            "repositories",
            "repository_roots",
            "git_policy",
        }
    )
    materialized = materialize_setup(state, new_config=True)
    target = tmp_path / "new.yml"
    plan = prepare_config_update(
        target,
        wizard_values=materialized.values,
        managed_fields=materialized.managed_fields,
    )
    target.write_text(plan.candidate_text)
    target.chmod(0o600)
    config = load_config(target)
    assert config.container.sudo is True
    assert config.github.repositories == []


def test_choices_from_config_keeps_advanced_values_available_without_marking_reviewed(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    choices = choices_from_config(config)
    assert choices.runtime_overrides is not None
    assert "filesystem" in choices.runtime_overrides
