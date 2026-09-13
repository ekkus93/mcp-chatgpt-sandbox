from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.config import load_config
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.setup.choices import SetupChoices
from dev_workspace_mcp.setup.config_writer import (
    SetupConfigError,
    commit_config_update,
    prepare_config_update,
)
from dev_workspace_mcp.setup.diagnostics import DiagnosticCode, DiagnosticStatus, run_diagnostics
from dev_workspace_mcp.setup.filesystem import SetupFilesystemError, atomic_write_text
from dev_workspace_mcp.setup.host import (
    SetupHostError,
    StorageChoices,
    prepare_storage_directories,
)
from dev_workspace_mcp.setup.materialize import materialize_setup, state_from_existing_config
from dev_workspace_mcp.setup.model import (
    SetupRepositoryState,
    SetupState,
    SetupStateError,
    load_setup_state,
)


def _init_repository(path: Path) -> SetupRepositoryState:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            "git@github.com:example/example.git",
        ],
        check=True,
    )
    return SetupRepositoryState.from_dict(
        {
            "id": "example",
            "path": str(path),
            "github": "example/example",
            "ssh_key": "example.ed25519",
        }
    )


def _storage(tmp_path: Path) -> StorageChoices:
    return StorageChoices(
        state_db_path=tmp_path / "state" / "state.db",
        audit_path=tmp_path / "audit" / "audit.jsonl",
        keys_dir=tmp_path / "keys",
        known_hosts=tmp_path / "ssh" / "known_hosts",
    )


def _fresh_state(tmp_path: Path, repository: SetupRepositoryState) -> SetupState:
    storage = _storage(tmp_path)
    choices = SetupChoices(
        state_db_path=str(storage.state_db_path),
        audit_path=str(storage.audit_path),
        keys_dir=str(storage.keys_dir),
        known_hosts=str(storage.known_hosts),
        docker_image="mcp-dev-test-s7:24.04",
    )
    return SetupState(
        config_path=str(tmp_path / "config.yml"),
        repositories=(repository,),
        choices=choices,
        reviewed_fields=frozenset(
            {
                "state_db_path",
                "audit",
                "docker",
                "workspace",
                "container",
                "repositories",
                "repository_roots",
                "ssh",
                "github",
                "git_policy",
            }
        ),
    )


def test_wizv_016_fresh_install_generates_and_validates_without_yaml_editing(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repo")
    storage = _storage(tmp_path)
    prepare_storage_directories(storage, approved=True)
    key = storage.keys_dir / "example.ed25519"
    key.write_text("fixture-sensitive-material\n")
    key.chmod(0o600)

    state = _fresh_state(tmp_path, repository)
    materialized = materialize_setup(state, new_config=True)
    plan = prepare_config_update(
        tmp_path / "config.yml",
        wizard_values=materialized.values,
        managed_fields=materialized.managed_fields,
    )
    result = commit_config_update(tmp_path / "config.yml", plan)

    config = load_config(result.path)
    assert config.repositories[0].id == "example"
    assert config.repositories[0].github == "example/example"
    assert config.ssh.known_hosts.read_text().startswith("github.com ")
    assert create_server(config).name == "dev-workspace-mcp"

    state_text = json.dumps(state.to_dict())
    assert "fixture-sensitive-material" not in state_text
    report = run_diagnostics(result.path)
    assert report.ready_to_finish
    assert all(
        item.status in {DiagnosticStatus.PASS, DiagnosticStatus.SKIPPED} for item in report.results
    )


def test_wizv_017_existing_config_preserves_unedited_advanced_values_and_cas(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repo")
    storage = _storage(tmp_path)
    prepare_storage_directories(storage, approved=True)
    key = storage.keys_dir / "example.ed25519"
    key.write_text("fixture\n")
    key.chmod(0o600)

    original = _fresh_state(tmp_path, repository)
    original.reviewed_fields = original.reviewed_fields | frozenset({"filesystem", "logging"})
    original.choices.runtime_overrides = {
        "filesystem": {"max_read_bytes": 12345},
        "logging": {"level": "DEBUG"},
    }
    initial = materialize_setup(original, new_config=True)
    initial_plan = prepare_config_update(
        tmp_path / "config.yml",
        wizard_values=initial.values,
        managed_fields=initial.managed_fields,
    )
    commit_config_update(tmp_path / "config.yml", initial_plan)

    edit_state = state_from_existing_config(tmp_path / "config.yml", advanced=False)
    edit_state.choices.docker_image = "mcp-dev-test-s7:24.04"
    edit_state.reviewed_fields = frozenset({"docker"})
    edited = materialize_setup(edit_state, new_config=False)
    plan = prepare_config_update(
        tmp_path / "config.yml",
        wizard_values=edited.values,
        managed_fields=edited.managed_fields,
    )
    result = commit_config_update(tmp_path / "config.yml", plan)
    assert result.backup_path is not None
    reloaded = load_config(result.path)
    assert reloaded.filesystem.max_read_bytes == 12345
    assert reloaded.logging.level == "DEBUG"

    stale = prepare_config_update(
        tmp_path / "config.yml",
        wizard_values={"logging": {"level": "INFO"}},
        managed_fields={"logging"},
    )
    result.path.write_text(result.path.read_text() + "# concurrent operator edit\n")
    with pytest.raises(SetupConfigError, match="changed after review"):
        commit_config_update(result.path, stale)


def test_wizv_018_repair_and_failure_paths_preserve_recovery_material(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yml"
    target.write_text("broken: [yaml")
    values = {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
        "docker": {"default_image": "mcp-dev-test-s7:24.04"},
        "ssh": {
            "keys_dir": str(tmp_path / "keys"),
            "known_hosts": str(tmp_path / "known_hosts"),
        },
        "audit": {"path": str(tmp_path / "audit.jsonl")},
    }
    with pytest.raises(SetupConfigError, match="explicit repair"):
        prepare_config_update(target, wizard_values=values, managed_fields=set(values))

    plan = prepare_config_update(
        target,
        wizard_values=values,
        managed_fields=set(values),
        repair_invalid=True,
    )
    result = commit_config_update(target, plan)
    assert result.backup_path is not None
    assert result.backup_path.read_text() == "broken: [yaml"

    unsafe = tmp_path / "unsafe.yml"
    unsafe.symlink_to(target)
    with pytest.raises(SetupConfigError, match="unsafe"):
        prepare_config_update(unsafe, wizard_values=values, managed_fields=set(values))

    state_path = tmp_path / "setup-state.json"
    atomic_write_text(state_path, "{not-json}\n", create_backup=False)
    with pytest.raises(SetupStateError):
        load_setup_state(state_path)
    assert load_config(target).docker.default_image == "mcp-dev-test-s7:24.04"

    statuses = {item.code: item.status for item in run_diagnostics(target).results}
    assert statuses[DiagnosticCode.CONFIG_RELOAD] is DiagnosticStatus.PASS


def test_wizv_018_atomic_writer_refuses_symlink_even_after_review(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("original\n")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(target)
    with pytest.raises(SetupFilesystemError, match="symlink"):
        atomic_write_text(alias, "replacement\n")
    assert target.read_text() == "original\n"


def test_wizv_018_storage_provisioning_refuses_symlink_known_hosts(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.known_hosts.parent.mkdir(parents=True)
    target = tmp_path / "operator-known-hosts"
    target.write_text("operator-managed\n")
    storage.known_hosts.symlink_to(target)

    with pytest.raises(SetupHostError, match="symlink"):
        prepare_storage_directories(storage, approved=True)

    assert target.read_text() == "operator-managed\n"
