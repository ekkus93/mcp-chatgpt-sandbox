from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Select

from dev_workspace_mcp.setup.app import STEPS, SetupWizardApp
from dev_workspace_mcp.setup.model import SetupStep


@pytest.mark.asyncio
async def test_swhv_006_guided_git_policy_controls_apply_boolean_enum_and_sequence(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        git_policy_index = next(
            index
            for index, definition in enumerate(STEPS)
            if definition.step is SetupStep.GIT_POLICY
        )
        app._current_index = git_policy_index
        app._refresh()
        await pilot.pause()

        app.query_one("#git-policy-preset", Select).value = "custom"
        app.query_one("#git-policy-push-allow", Checkbox).value = False
        app.query_one("#git-policy-pull-strategy", Select).value = "rebase"
        app.query_one("#git-policy-force-with-lease", Select).value = "require_approval"
        app.query_one("#git-policy-check-force-push", Checkbox).value = False
        app.query_one("#git-policy-url-ssh", Checkbox).value = False
        await pilot.pause()

        app.validate_git_policy()
        await pilot.pause()

        assert app.state.choices.git_policy_preset == "custom"
        assert app.state.choices.git_policy_custom is not None
        assert app.state.choices.git_policy_custom["push"] == "deny"
        assert app.state.choices.git_policy_custom["pull_strategy"] == "rebase"
        assert app.state.choices.git_policy_custom["force_with_lease"] == "require_approval"
        assert app.state.choices.git_policy_custom["remote_protection_checks"] == ["deletion"]
        assert app.state.choices.git_policy_custom["allowed_url_schemes"] == ["https"]
        assert not app.query_one("#next", Button).disabled


@pytest.mark.asyncio
async def test_swhv_006_existing_custom_git_policy_populates_guided_controls(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    app.state.choices.git_policy_preset = "custom"
    app.state.choices.git_policy_custom = {
        "push": "deny",
        "default_branch_push": "require_approval",
        "force_push": "deny",
        "force_with_lease": "require_approval",
        "delete_remote_branch": "deny",
        "delete_remote_tag": "deny",
        "default_branch_commit": "deny",
        "amend_published": "deny",
        "pull_strategy": "merge",
        "require_remote_protection": "deny",
        "remote_protection_checks": ["force_push"],
        "allowed_url_schemes": ["ssh"],
    }
    async with app.run_test() as pilot:
        git_policy_index = next(
            index
            for index, definition in enumerate(STEPS)
            if definition.step is SetupStep.GIT_POLICY
        )
        app._current_index = git_policy_index
        app._populate_widgets()
        app._refresh()
        await pilot.pause()

        assert app.query_one("#git-policy-preset", Select).value == "custom"
        assert not app.query_one("#git-policy-push-allow", Checkbox).value
        assert app.query_one("#git-policy-default-branch-push", Select).value == "require_approval"
        assert app.query_one("#git-policy-pull-strategy", Select).value == "merge"
        assert app.query_one("#git-policy-check-force-push", Checkbox).value
        assert not app.query_one("#git-policy-check-deletion", Checkbox).value
        assert app.query_one("#git-policy-url-ssh", Checkbox).value
        assert not app.query_one("#git-policy-url-https", Checkbox).value
