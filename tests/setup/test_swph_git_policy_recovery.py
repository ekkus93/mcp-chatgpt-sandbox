from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Select

from dev_workspace_mcp.config import GitPolicyConfig
from dev_workspace_mcp.setup.app import SetupWizardApp
from dev_workspace_mcp.setup.git_policy_form import (
    GitPolicyEditorValues,
    SetupGitPolicyFormError,
)
from dev_workspace_mcp.setup.model import (
    SetupState,
    SetupStep,
    SetupStepStatus,
    save_setup_state,
)


def _persisted_custom_state(payload: dict[str, object]) -> SetupState:
    state = SetupState(
        current_step=SetupStep.REVIEW,
        statuses={
            SetupStep.GIT_POLICY: SetupStepStatus.COMPLETE,
            SetupStep.REVIEW: SetupStepStatus.COMPLETE,
            SetupStep.FINAL_VALIDATION: SetupStepStatus.ACTION_REQUIRED,
        },
    )
    state.choices.git_policy_preset = "custom"
    state.choices.git_policy_custom = payload
    return state


def _policy_payload() -> dict[str, object]:
    return GitPolicyConfig(
        push="deny",
        default_branch_push="require_approval",
        force_push="deny",
        force_with_lease="require_approval",
        delete_remote_branch="allow",
        delete_remote_tag="deny",
        default_branch_commit="deny",
        amend_published="deny",
        pull_strategy="rebase",
        require_remote_protection="deny",
        remote_protection_checks=["deletion"],
        allowed_url_schemes=["https"],
    ).model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("pull_strategy", "opaque-invalid-value"),
        ("allowed_url_schemes", ["https", "opaque-invalid-value"]),
    ],
)
async def test_swphv_005_incompatible_custom_state_is_recoverable_and_non_secret(
    tmp_path: Path,
    field: str,
    invalid: object,
) -> None:
    payload = _policy_payload()
    payload[field] = invalid
    state_path = tmp_path / "state.json"
    save_setup_state(_persisted_custom_state(payload), state_path)

    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=state_path,
        resume=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.fatal_error is None
        assert app.current_step is SetupStep.GIT_POLICY
        assert app.state.statuses[SetupStep.GIT_POLICY] is SetupStepStatus.ACTION_REQUIRED
        assert SetupStep.REVIEW not in app.state.statuses
        assert SetupStep.FINAL_VALIDATION not in app.state.statuses
        assert app.query_one("#next", Button).disabled
        message = app._validation_messages[SetupStep.GIT_POLICY]
        assert "incompatible" in message
        assert "Select Recommended or Conservative" in message
        assert "opaque-invalid-value" not in message
        assert app.state.choices.git_policy_custom == payload


@pytest.mark.asyncio
async def test_swphv_005_operator_can_recover_with_preset(tmp_path: Path) -> None:
    payload = _policy_payload()
    payload["pull_strategy"] = "obsolete"
    state_path = tmp_path / "state.json"
    save_setup_state(_persisted_custom_state(payload), state_path)

    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=state_path,
        resume=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#git-policy-preset", Select).value = "conservative"
        await pilot.pause()
        app.validate_git_policy()
        await pilot.pause()

        assert app._validation[SetupStep.GIT_POLICY]
        assert app.state.choices.git_policy_preset == "conservative"
        assert app.state.choices.git_policy_custom is None


@pytest.mark.asyncio
async def test_swphv_005_operator_can_reenter_valid_custom_values(tmp_path: Path) -> None:
    payload = _policy_payload()
    payload["allowed_url_schemes"] = ["file"]
    state_path = tmp_path / "state.json"
    save_setup_state(_persisted_custom_state(payload), state_path)

    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=state_path,
        resume=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#git-policy-preset", Select).value == "custom"
        app.query_one("#git-policy-push-allow", Checkbox).value = False
        app.query_one("#git-policy-pull-strategy", Select).value = "merge"
        app.query_one("#git-policy-url-ssh", Checkbox).value = True
        app.query_one("#git-policy-url-https", Checkbox).value = True
        await pilot.pause()
        app.validate_git_policy()
        await pilot.pause()

        assert app._validation[SetupStep.GIT_POLICY]
        assert app.state.choices.git_policy_preset == "custom"
        assert app.state.choices.git_policy_custom is not None
        assert app.state.choices.git_policy_custom["push"] == "deny"
        assert app.state.choices.git_policy_custom["pull_strategy"] == "merge"
        assert app.state.choices.git_policy_custom["allowed_url_schemes"] == ["ssh", "https"]


@pytest.mark.asyncio
async def test_swphv_005_valid_current_custom_state_still_populates_guided_controls(
    tmp_path: Path,
) -> None:
    payload = _policy_payload()
    state_path = tmp_path / "state.json"
    save_setup_state(_persisted_custom_state(payload), state_path)

    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=state_path,
        resume=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.current_step is SetupStep.REVIEW
        assert app.query_one("#git-policy-preset", Select).value == "custom"
        assert not app.query_one("#git-policy-push-allow", Checkbox).value
        assert app.query_one("#git-policy-default-branch-push", Select).value == "require_approval"
        assert app.query_one("#git-policy-pull-strategy", Select).value == "rebase"
        assert not app.query_one("#git-policy-url-ssh", Checkbox).value
        assert app.query_one("#git-policy-url-https", Checkbox).value


def test_swphv_005_legacy_missing_fields_use_production_defaults() -> None:
    payload = _policy_payload()
    payload.pop("allowed_url_schemes")

    values = GitPolicyEditorValues.from_mapping(payload)

    assert values.allowed_url_schemes == ("ssh", "https")


def test_swphv_005_legacy_extra_fields_require_repair() -> None:
    payload = _policy_payload()
    payload["removed_legacy_field"] = "value"

    with pytest.raises(SetupGitPolicyFormError):
        GitPolicyEditorValues.from_mapping(payload)
