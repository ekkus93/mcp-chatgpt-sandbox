from __future__ import annotations

from pathlib import Path

import pytest
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Select

from dev_workspace_mcp.setup.app import STEPS, SetupStartChoice, SetupWizardApp
from dev_workspace_mcp.setup.model import (
    SetupState,
    SetupStep,
    SetupStepStatus,
    save_setup_state,
)


def _write_existing_config(tmp_path: Path, *, image: str = "operator-image:latest") -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            (
                "version: 1",
                f"state_db_path: {tmp_path / 'state.db'}",
                "docker:",
                f"  default_image: {image}",
                "ssh:",
                f"  keys_dir: {tmp_path / 'keys'}",
                f"  known_hosts: {tmp_path / 'known_hosts'}",
                "audit:",
                f"  path: {tmp_path / 'audit.jsonl'}",
                "logging:",
                "  level: DEBUG",
                "",
            )
        )
    )
    config_path.chmod(0o600)
    return config_path


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
        next_button.focus()
        await pilot.press("enter")
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
async def test_wizv_006_cancel_decline_preserves_session_and_confirm_returns_cancel_code(
    tmp_path: Path,
) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
    )
    async with app.run_test() as pilot:
        assert app.current_step is SetupStep.PREREQUISITES
        app._handle_cancel(False)
        await pilot.pause()
        assert app.setup_exit_code is None
        assert app.current_step is SetupStep.PREREQUISITES

        app._handle_cancel(True)
        await pilot.pause()
    assert app.setup_exit_code == 130


@pytest.mark.asyncio
async def test_wizv_006_resume_without_state_returns_fatal_code(tmp_path: Path) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "missing-state.json",
        resume=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
    assert app.setup_exit_code == 2
    assert app.fatal_error == "No incomplete setup state is available."


@pytest.mark.asyncio
async def test_wizv_007_resume_availability_tracks_incomplete_state(tmp_path: Path) -> None:
    incomplete_path = tmp_path / "incomplete.json"
    save_setup_state(
        SetupState(
            current_step=SetupStep.REPOSITORIES,
            statuses={SetupStep.PREREQUISITES: SetupStepStatus.COMPLETE},
        ),
        incomplete_path,
    )
    incomplete = SetupWizardApp(
        config_path=tmp_path / "absent.yml",
        state_path=incomplete_path,
    )
    async with incomplete.run_test() as pilot:
        await pilot.pause()
        assert not incomplete.screen.query_one("#welcome-resume", Button).disabled

    complete_path = tmp_path / "complete.json"
    save_setup_state(
        SetupState(
            current_step=SetupStep.FINAL_VALIDATION,
            statuses={SetupStep.FINAL_VALIDATION: SetupStepStatus.COMPLETE},
        ),
        complete_path,
    )
    complete = SetupWizardApp(
        config_path=tmp_path / "also-absent.yml",
        state_path=complete_path,
    )
    async with complete.run_test() as pilot:
        await pilot.pause()
        assert complete.screen.query_one("#welcome-resume", Button).disabled


@pytest.mark.asyncio
async def test_wizv_007_corrupt_state_keeps_config_and_offers_repair_or_fresh(
    tmp_path: Path,
) -> None:
    config_path = _write_existing_config(tmp_path)
    original_config = config_path.read_bytes()
    state_path = tmp_path / "state.json"
    state_path.write_text('{"password":"corrupt-state-sentinel"}')
    state_path.chmod(0o600)

    app = SetupWizardApp(config_path=config_path, state_path=state_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#welcome-repair", Button)
        assert app.screen.query_one("#welcome-fresh", Button)
        assert config_path.read_bytes() == original_config


@pytest.mark.asyncio
async def test_wizv_007_start_fresh_resets_only_wizard_state(tmp_path: Path) -> None:
    config_path = _write_existing_config(tmp_path)
    original_config = config_path.read_bytes()
    state_path = tmp_path / "setup-state.json"
    save_setup_state(SetupState(current_step=SetupStep.REPOSITORIES), state_path)

    operational_paths = (
        tmp_path / "state.db",
        tmp_path / "keys" / "operator.ed25519",
        tmp_path / "repo" / "operator-data.txt",
    )
    for path in operational_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("operator-owned\n")

    app = SetupWizardApp(config_path=config_path, state_path=state_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_start_choice(SetupStartChoice.START_FRESH)
        await pilot.pause()
        assert app.current_step is SetupStep.PREREQUISITES
        assert config_path.read_bytes() == original_config
        assert all(path.read_text() == "operator-owned\n" for path in operational_paths)
        assert list(tmp_path.glob("setup-state.json.bak.*"))


@pytest.mark.asyncio
async def test_wizv_007_edit_starts_from_production_valid_existing_values(tmp_path: Path) -> None:
    config_path = _write_existing_config(tmp_path, image="operator-image:2026")
    state_path = tmp_path / "state.json"
    app = SetupWizardApp(config_path=config_path, state_path=state_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_start_choice(SetupStartChoice.EDIT)
        await pilot.pause()
        assert app.current_step is SetupStep.STORAGE
        assert app.state.choices.docker_image == "operator-image:2026"
        assert app.state.choices.runtime_overrides is not None
        assert app.state.choices.runtime_overrides["logging"] == {"level": "DEBUG"}


@pytest.mark.asyncio
async def test_wizv_013_advanced_flag_exposes_guided_production_defaults(tmp_path: Path) -> None:
    app = SetupWizardApp(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "state.json",
        advanced=True,
    )
    async with app.run_test() as pilot:
        runtime_index = next(
            index
            for index, definition in enumerate(STEPS)
            if definition.step is SetupStep.RUNTIME_LIMITS
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
            index
            for index, definition in enumerate(STEPS)
            if definition.step is SetupStep.RUNTIME_LIMITS
        )
        app._current_index = runtime_index
        app._refresh()
        await pilot.pause()

        app.query_one("#runtime-fs-max-read-bytes", Input).value = "0"
        await pilot.pause()
        app.validate_runtime()
        await pilot.pause()

        assert app.current_step is SetupStep.RUNTIME_LIMITS
        assert app.query_one("#next", Button).disabled
