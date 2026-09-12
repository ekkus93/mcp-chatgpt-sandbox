from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button

from dev_workspace_mcp.setup.app import STEPS, SetupWizardApp
from dev_workspace_mcp.setup.model import SetupStep


@pytest.mark.asyncio
async def test_wizv_006_navigation_is_validation_gated_and_keyboard_accessible(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        back = app.query_one("#back", Button)
        next_button = app.query_one("#next", Button)
        assert back.disabled
        assert next_button.disabled
        assert str(next_button.label) == "Next"

        await pilot.press("tab")
        assert app.focused is not None

        app.set_step_validation(SetupStep.PREREQUISITES, valid=True)
        await pilot.pause()
        assert not next_button.disabled
        await pilot.click("#next")
        assert app.current_step is SetupStep.STORAGE
        assert not back.disabled


@pytest.mark.asyncio
async def test_wizv_006_finish_only_appears_on_final_step(tmp_path: Path) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        for definition in STEPS[:-1]:
            app.set_step_validation(definition.step, valid=True)
            await pilot.pause()
            await pilot.click("#next")
        next_button = app.query_one("#next", Button)
        assert app.current_step is SetupStep.FINAL_VALIDATION
        assert str(next_button.label) == "Finish"
        assert next_button.disabled
