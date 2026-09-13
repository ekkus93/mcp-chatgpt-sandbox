from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.config import GitPolicyConfig
from dev_workspace_mcp.setup.docker_setup import DockerSetupChoices, SetupDockerError
from dev_workspace_mcp.setup.git_policy_setup import (
    GIT_POLICY_UI_FIELDS,
    GitPolicyPreset,
    conservative_policy,
    materialize_policy,
    recommended_policy,
)
from dev_workspace_mcp.setup.host import (
    SetupHostError,
    StorageChoices,
    check_prerequisites,
    parent_is_writable,
)
from dev_workspace_mcp.setup.model import SetupRepositoryState
from dev_workspace_mcp.setup.repository_setup import (
    SetupRepositoryError,
    add_repository,
    remove_repository,
    verify_repository,
)
from dev_workspace_mcp.setup.runtime_setup import RuntimeChoices, SetupRuntimeError
from dev_workspace_mcp.setup.ssh_github_setup import CredentialChoices, verify_credential_files


def test_wizv_008_storage_paths_use_production_absolute_path_rules(tmp_path: Path) -> None:
    valid = StorageChoices(
        state_db_path=tmp_path / "state.db",
        audit_path=tmp_path / "audit.jsonl",
        keys_dir=tmp_path / "keys",
        known_hosts=tmp_path / "known_hosts",
    )
    valid.validate()
    assert parent_is_writable(valid.state_db_path)

    invalid = StorageChoices(
        state_db_path=Path("relative.db"),
        audit_path=tmp_path / "audit",
        keys_dir=tmp_path / "keys",
        known_hosts=tmp_path / "known",
    )
    with pytest.raises(SetupHostError):
        invalid.validate()


def test_wizv_008_unwritable_parent_and_docker_outage_are_degraded_not_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dev_workspace_mcp.setup.host.os.access", lambda *args: False)
    assert not parent_is_writable(tmp_path / "nested" / "state.db")

    monkeypatch.setattr("dev_workspace_mcp.setup.host.docker_available", lambda: False)
    report = check_prerequisites()
    assert report.python_supported
    assert not report.docker_reachable
    assert not report.fatal


def test_wizv_009_docker_choices_validate_duration_and_workspace_path() -> None:
    fragments = DockerSetupChoices().config_fragments()
    assert fragments["docker"] == {
        "default_image": "mcp-dev-ubuntu:24.04",
        "workspace_path": "/workspace",
    }
    with pytest.raises(SetupDockerError):
        DockerSetupChoices(idle_stop_after="tomorrow").config_fragments()
    with pytest.raises(SetupDockerError):
        DockerSetupChoices(workspace_path="relative").config_fragments()


def _repo(path: Path, *, repository_id: str = "example") -> SetupRepositoryState:
    return SetupRepositoryState.from_dict(
        {
            "id": repository_id,
            "path": str(path),
            "github": "example/example",
            "ssh_key": f"{repository_id}.ed25519",
        }
    )


def test_wizv_010_temporary_git_repository_verifies_remote_identity(tmp_path: Path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:example/example.git",
        ],
        check=True,
    )
    report = verify_repository(_repo(tmp_path))
    assert report.valid
    assert report.remote_identity == "example/example"


def test_wizv_010_non_git_mismatch_and_duplicate_crud_are_explicit(tmp_path: Path) -> None:
    non_git = tmp_path / "plain"
    non_git.mkdir()
    assert not verify_repository(_repo(non_git)).valid

    first_path = tmp_path / "one"
    first_path.mkdir()
    first = _repo(first_path)
    repositories = add_repository((), first)
    with pytest.raises(SetupRepositoryError, match="id"):
        add_repository(repositories, first)
    with pytest.raises(SetupRepositoryError, match="confirmation"):
        remove_repository(repositories, repository_id="example", confirmed=False)


def test_wizv_011_recommended_ssh_mapping_and_missing_key_status(tmp_path: Path) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("github.com ssh-ed25519 public-host-key\n")
    keys = tmp_path / "keys"
    keys.mkdir()
    choices = CredentialChoices(keys_dir=keys, known_hosts=known)
    fragments = choices.config_fragments()
    assert fragments["ssh"]["scope"] == "repository"
    assert fragments["ssh"]["mode"] == "agent"
    reports = verify_credential_files(choices, (_repo(tmp_path),))
    assert any(report.status == "action_required" for report in reports)
    assert all("public-host-key" not in report.message for report in reports)


def test_wizv_011_unimplemented_github_app_verification_never_becomes_success(
    tmp_path: Path,
) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("github.com ssh-ed25519 public-host-key\n")
    keys = tmp_path / "keys"
    keys.mkdir()
    choices = CredentialChoices(
        keys_dir=keys,
        known_hosts=known,
        github_api_auth="app",
    )
    reports = verify_credential_files(choices, ())
    github = reports[-1]
    assert not github.valid
    assert github.status == "unverified"
    assert "not implemented" in github.message


def test_wizv_012_git_policy_presets_and_field_exhaustiveness() -> None:
    assert recommended_policy() == GitPolicyConfig()
    conservative = conservative_policy()
    assert conservative.push == "deny"
    assert conservative.default_branch_push == "deny"
    assert conservative.delete_remote_branch == "deny"
    assert conservative.require_remote_protection == "deny"
    assert frozenset(GitPolicyConfig.model_fields) == GIT_POLICY_UI_FIELDS
    custom = materialize_policy(
        GitPolicyPreset.CUSTOM,
        custom={**GitPolicyConfig().model_dump(), "pull_strategy": "rebase"},
    )
    assert custom.pull_strategy == "rebase"


def test_wizv_013_advanced_runtime_is_untouched_unless_reviewed() -> None:
    assert RuntimeChoices().reviewed_fragments() == {}
    fragments = RuntimeChoices(
        resources={"cpus": 2.0, "memory": "4g", "pids_limit": 512}
    ).reviewed_fragments()
    assert fragments["resources"] == {"cpus": 2.0, "memory": "4g", "pids_limit": 512}
    with pytest.raises(SetupRuntimeError):
        RuntimeChoices(execution={"sync_timeout_seconds": 0}).reviewed_fragments()
