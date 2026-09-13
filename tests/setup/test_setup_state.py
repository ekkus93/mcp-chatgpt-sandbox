"""Strict state and dependency-invalidation contracts for setup."""

from pathlib import Path

import pytest

from dev_workspace_mcp.setup.model import (
    SetupRepository,
    SetupState,
    SetupStep,
    SetupStepStatus,
)


def test_setup_state_round_trip_is_strict_and_non_secret() -> None:
    state = SetupState(
        current_step=SetupStep.REPOSITORY_ACCESS,
        statuses={SetupStep.SERVER_STORAGE: SetupStepStatus.COMPLETE},
        config_path="/tmp/config.yml",
        repositories=(
            SetupRepository(
                id="demo",
                path="/srv/demo",
                github="owner/demo",
                ssh_key="demo.ed25519",
            ),
        ),
    )
    encoded = state.to_dict()
    assert SetupState.from_dict(encoded).to_dict() == encoded
    serialized = repr(encoded).casefold()
    assert "private_key" not in serialized
    assert "password" not in serialized


@pytest.mark.parametrize("key", ["token", "password", "private_key", "api_key", "credential"])
def test_setup_state_rejects_unknown_secret_bearing_fields(key: str) -> None:
    raw = SetupState().to_dict()
    raw[key] = "sensitive"
    with pytest.raises(ValueError, match="forbidden secret-bearing"):
        SetupState.from_dict(raw)


def test_setup_state_rejects_unknown_future_version() -> None:
    raw = SetupState().to_dict()
    raw["version"] = 2
    with pytest.raises(ValueError, match="unsupported setup state version"):
        SetupState.from_dict(raw)


def test_setup_state_rejects_duplicate_repository_ids() -> None:
    repository = {"id": "demo", "path": "/srv/demo", "github": "owner/demo", "ssh_key": None}
    raw = SetupState().to_dict()
    raw["repositories"] = [repository, {**repository, "path": "/srv/other"}]
    with pytest.raises(ValueError, match="duplicate ids"):
        SetupState.from_dict(raw)


def test_setup_state_rejects_duplicate_repository_paths() -> None:
    raw = SetupState().to_dict()
    raw["repositories"] = [
        {"id": "one", "path": "/srv/demo", "github": "owner/one", "ssh_key": None},
        {"id": "two", "path": "/srv/demo", "github": "owner/two", "ssh_key": None},
    ]
    with pytest.raises(ValueError, match="duplicate paths"):
        SetupState.from_dict(raw)


def test_repository_change_invalidates_all_dependent_completion() -> None:
    state = SetupState(
        statuses={
            SetupStep.REPOSITORY_ACCESS: SetupStepStatus.COMPLETE,
            SetupStep.SSH_AND_GITHUB: SetupStepStatus.COMPLETE,
            SetupStep.CONFIGURATION_REVIEW: SetupStepStatus.COMPLETE,
            SetupStep.FINAL_VALIDATION: SetupStepStatus.COMPLETE,
            SetupStep.DOCKER_WORKSPACE: SetupStepStatus.COMPLETE,
        }
    )
    state.mark_changed(SetupStep.REPOSITORY_ACCESS)
    assert state.statuses[SetupStep.REPOSITORY_ACCESS] is SetupStepStatus.ACTION_REQUIRED
    assert state.statuses[SetupStep.SSH_AND_GITHUB] is SetupStepStatus.ACTION_REQUIRED
    assert state.statuses[SetupStep.CONFIGURATION_REVIEW] is SetupStepStatus.ACTION_REQUIRED
    assert state.statuses[SetupStep.FINAL_VALIDATION] is SetupStepStatus.ACTION_REQUIRED
    assert state.statuses[SetupStep.DOCKER_WORKSPACE] is SetupStepStatus.COMPLETE


def test_resume_rewinds_process_local_chatgpt_secret() -> None:
    state = SetupState(
        current_step=SetupStep.FINAL_VALIDATION,
        chatgpt_enabled=True,
        statuses={
            SetupStep.CHATGPT_CONNECTION: SetupStepStatus.COMPLETE,
            SetupStep.FINAL_VALIDATION: SetupStepStatus.COMPLETE,
        },
    )
    assert state.rewind_for_missing_process_local_secret()
    assert state.current_step is SetupStep.CHATGPT_CONNECTION
    assert state.statuses[SetupStep.CHATGPT_CONNECTION] is SetupStepStatus.ACTION_REQUIRED
    assert state.statuses[SetupStep.FINAL_VALIDATION] is SetupStepStatus.ACTION_REQUIRED


def test_repository_choice_requires_absolute_path() -> None:
    with pytest.raises(ValueError):
        SetupRepository(id="demo", path="relative/demo", github="owner/demo")


def test_setup_state_paths_are_data_not_secret_contents() -> None:
    state = SetupState(github_api_token_path="/run/secrets/github-token")
    assert state.github_api_token_path == "/run/secrets/github-token"
    assert Path(state.github_api_token_path).is_absolute()
