from __future__ import annotations

from pathlib import Path

import pytest
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Select

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
        await pilot.pause()
        assert app.current_step is SetupStep.STORAGE
        assert not back.disabled


@pytest.mark.asyncio
async def test_wizv_006_finish_only_appears_on_final_step(tmp_path: Path) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        for index, definition in enumerate(STEPS[:-1]):
            assert app.current_step is definition.step
            app.set_step_validation(definition.step, valid=True)
            await pilot.pause()
            next_button = app.query_one("#next", Button)
            assert not next_button.disabled
            await pilot.click("#next")
            await pilot.pause()
            assert app.current_step is STEPS[index + 1].step
        next_button = app.query_one("#next", Button)
        assert app.current_step is SetupStep.FINAL_VALIDATION
        assert str(next_button.label) == "Finish"
        assert next_button.disabled


@pytest.mark.asyncio
async def test_wizv_013_advanced_flag_exposes_guided_production_defaults(tmp_path: Path) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
        advanced=True,
    )
    async with app.run_test() as pilot:
        runtime_index = next(
            index for index, definition in enumerate(STEPS) if definition.step is SetupStep.RUNTIME_LIMITS
        )
        app._current_index = runtime_index
        app._refresh()
        await pilot.pause()

        assert app.query_one("#runtime-edit", Checkbox).value
        assert app.query_one("#runtime-advanced-fields", Vertical).display
        assert app.query_one("#runtime-fs-max-read-bytes", Input).value == str(10 * 1024 * 1024)
        assert app.query_one("#runtime-exec-sync-timeout-seconds", Input).value == "30"
        assert app.query_one("#runtime-resources-cpus", Input).value == ""
        assert app.query_one("#runtime-resources-memory", Input).value == ""
        assert app.query_one("#runtime-resources-pids-limit", Input).value == ""
        assert app.query_one("#runtime-logging-level", Select).value == "INFO"


@pytest.mark.asyncio
async def test_wizv_013_guided_runtime_controls_validate_with_production_models(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
        advanced=True,
    )
    async with app.run_test() as pilot:
        runtime_index = next(
            index for index, definition in enumerate(STEPS) if definition.step is SetupStep.RUNTIME_LIMITS
        )
        app._current_index = runtime_index
        app._refresh()
        await pilot.pause()

        app.query_one("#runtime-fs-max-read-bytes", Input).value = "0"
        await pilot.pause()
        await pilot.click("#validate-runtime")
        await pilot.pause()

        assert app.current_step is SetupStep.RUNTIME_LIMITS
        assert app.query_one("#next", Button).disabled
