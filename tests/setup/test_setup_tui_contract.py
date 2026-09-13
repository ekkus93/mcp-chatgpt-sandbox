"""Textual setup-shell support, keyboard, and exhaustiveness contracts."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, Input

from dev_workspace_mcp.config.models import GitPolicyConfig
from dev_workspace_mcp.setup.app import ConfirmScreen, GIT_POLICY_UI_FIELDS, SetupApp
from dev_workspace_mcp.setup.controller import SetupController
from dev_workspace_mcp.setup.model import SetupStep, SetupStepStatus


def _controller(tmp_path: Path) -> SetupController:
    return SetupController.create(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "setup-state.json",
        no_browser=True,
    )


def test_git_policy_editor_is_exhaustive() -> None:
    assert set(GIT_POLICY_UI_FIELDS) == set(GitPolicyConfig.model_fields)


async def test_fresh_shell_is_keyboard_operable_and_blocks_invalid_next(tmp_path: Path) -> None:
    app = SetupApp(_controller(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#back", Button).disabled
        assert app.query_one("#next", Button).disabled
        assert not app.query_one("#finish", Button).display

        await pilot.press("tab")
        assert app.focused is not None
        await pilot.press("shift+tab")
        assert app.focused is not None


async def test_final_finish_remains_disabled_until_machine_diagnostics_complete(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.state.current_step = SetupStep.FINAL_VALIDATION
    controller.state.statuses[SetupStep.FINAL_VALIDATION] = SetupStepStatus.ACTION_REQUIRED
    app = SetupApp(controller)
    async with app.run_test(size=(80, 24)):
        finish = app.query_one("#finish", Button)
        assert finish.display
        assert finish.disabled

        controller.state.statuses[SetupStep.FINAL_VALIDATION] = SetupStepStatus.COMPLETE
        app._update_controls()
        assert not finish.disabled


async def test_cancel_decline_preserves_session(tmp_path: Path) -> None:
    app = SetupApp(_controller(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        app.action_cancel_setup()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm_no")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert app.return_value is None


async def test_narrow_terminal_keeps_scrollable_content_and_controls(tmp_path: Path) -> None:
    app = SetupApp(_controller(tmp_path))
    async with app.run_test(size=(40, 12)) as pilot:
        assert app.query_one("#content") is not None
        assert app.query_one("#controls") is not None
        await pilot.press("tab")
        assert app.focused is not None


async def test_masked_runtime_key_input_is_password_field(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.state.current_step = SetupStep.CHATGPT_CONNECTION
    app = SetupApp(controller)
    async with app.run_test(size=(80, 24)):
        runtime_key = app.query_one("#chatgpt_runtime_key", Input)
        assert runtime_key.password
