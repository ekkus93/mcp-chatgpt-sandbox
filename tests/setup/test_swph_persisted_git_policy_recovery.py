from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Select

from dev_workspace_mcp.setup.app import SetupWizardApp
from dev_workspace_mcp.setup.model import (
    SetupState,
    SetupStep,
    SetupStepStatus,
    clone_setup_state,
)


def _custom_state(
    app: SetupWizardApp,
    payload: dict[str, object],
) -> SetupState:
    state = clone_setup_state(app.state)
    state.current_step = SetupStep.GIT_POLICY
    state.statuses[SetupStep.GIT_POLICY] = SetupStepStatus.COMPLETE
    state.choices.git_policy_preset = "custom"
    state.choices.git_policy_custom = payload
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"pull_strategy": "SWPH_SECRET_BAD_ENUM"},
        {"remote_protection_checks": ["force_push", "legacy-check"]},
        {"future_legacy_field": "legacy-value"},
    ],
)
async def test_swphv_005_incompatible_custom_state_is_recoverable_and_non_secret(
    payload: dict[str, object],
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        original_payload = dict(payload)
        app._activate(_custom_state(app, payload))
        await pilot.pause()

        assert app.fatal_error is None
        assert app.current_step is SetupStep.GIT_POLICY
        assert app.state.statuses[SetupStep.GIT_POLICY] is SetupStepStatus.ACTION_REQUIRED
        assert app._validation[SetupStep.GIT_POLICY] is False
        message = app._validation_messages[SetupStep.GIT_POLICY]
        assert "Persisted custom Git policy is incompatible" in message
        assert "SWPH_SECRET_BAD_ENUM" not in message
        assert "legacy-check" not in message
        assert "legacy-value" not in message
        assert app.state.choices.git_policy_custom == original_payload
        assert app.query_one("#next", Button).disabled


@pytest.mark.asyncio
async def test_swphv_005_missing_legacy_fields_use_production_defaults_without_repair(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        payload: dict[str, object] = {"push": "deny"}
        app._activate(_custom_state(app, payload))
        await pilot.pause()

        assert app.state.choices.git_policy_custom == payload
        assert app.state.statuses[SetupStep.GIT_POLICY] is SetupStepStatus.COMPLETE
        assert SetupStep.GIT_POLICY not in app._validation
        assert not app.query_one("#git-policy-push-allow", Checkbox).value
        assert app.query_one("#git-policy-pull-strategy", Select).value == "ff_only"


@pytest.mark.asyncio
async def test_swphv_005_operator_recovers_incompatible_state_with_preset(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        app._activate(_custom_state(app, {"pull_strategy": "invalid"}))
        await pilot.pause()

        app.query_one("#git-policy-preset", Select).value = "recommended"
        await pilot.pause()
        app.validate_git_policy()
        await pilot.pause()

        assert app._validation[SetupStep.GIT_POLICY] is True
        assert app.state.choices.git_policy_preset == "recommended"
        assert app.state.choices.git_policy_custom is None
        assert not app.query_one("#next", Button).disabled


@pytest.mark.asyncio
async def test_swphv_005_operator_recovers_incompatible_state_with_new_custom_values(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        app._activate(_custom_state(app, {"pull_strategy": "invalid"}))
        await pilot.pause()

        app.query_one("#git-policy-preset", Select).value = "custom"
        app.query_one("#git-policy-push-allow", Checkbox).value = False
        app.query_one("#git-policy-pull-strategy", Select).value = "rebase"
        await pilot.pause()
        app.validate_git_policy()
        await pilot.pause()

        assert app._validation[SetupStep.GIT_POLICY] is True
        assert app.state.choices.git_policy_preset == "custom"
        assert app.state.choices.git_policy_custom is not None
        assert app.state.choices.git_policy_custom["push"] == "deny"
        assert app.state.choices.git_policy_custom["pull_strategy"] == "rebase"
        assert not app.query_one("#next", Button).disabled
