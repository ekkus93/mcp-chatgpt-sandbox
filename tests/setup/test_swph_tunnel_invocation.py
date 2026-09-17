from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Input

from mcp_chatgpt_sandbox.setup.app import STEPS, SetupWizardApp
from mcp_chatgpt_sandbox.setup.model import SetupStep
from mcp_chatgpt_sandbox.setup.tunnel import build_tunnel_profile_plan


def _go_to(app: SetupWizardApp, step: SetupStep) -> None:
    app._current_index = next(
        index for index, definition in enumerate(STEPS) if definition.step is step
    )
    app._refresh()


@pytest.mark.asyncio
async def test_swphv_001_tunnel_prepare_is_independent_of_caller_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    config_path = tmp_path / "config dir" / "config.yml"
    app = SetupWizardApp(config_path=config_path, state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app.query_one("#tunnel-id", Input).value = "tun_cwd_independent"
        app.query_one("#tunnel-profile", Input).value = "cwd-independent"
        await pilot.pause()
        app.prepare_tunnel()
        await pilot.pause()

        plan = app._pending_tunnel_plan
        assert plan is not None
        assert list(plan.server_argv) == [
            str(Path(sys.executable).expanduser()),
            "-m",
            "mcp_chatgpt_sandbox",
            "serve",
            "--config",
            str(config_path.resolve(strict=False)),
        ]
        assert str(unrelated) not in plan.server_command


def test_swphv_001_module_entrypoint_runs_from_unrelated_cwd(tmp_path: Path) -> None:
    unrelated = tmp_path / "module cwd"
    unrelated.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "mcp_chatgpt_sandbox", "--help"],
        cwd=unrelated,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "Usage:" in result.stdout


@pytest.mark.asyncio
async def test_swphv_001_unusable_python_is_action_required_and_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_python = tmp_path / "missing python"
    monkeypatch.setattr("mcp_chatgpt_sandbox.setup.tunnel.sys.executable", str(missing_python))
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app.query_one("#tunnel-id", Input).value = "tun_missing_python"
        await pilot.pause()
        app.prepare_tunnel()
        await pilot.pause()

        assert app._pending_tunnel_plan is None
        assert app.query_one("#tunnel-apply", Button).disabled
        assert app._validation[SetupStep.CHATGPT] is False
        assert "ACTION REQUIRED" in app._validation_messages[SetupStep.CHATGPT]


def test_swphv_001_shell_sensitive_paths_round_trip_and_plan_is_secret_free(
    tmp_path: Path,
) -> None:
    python_path = tmp_path / "python dir" / "python's executable"
    python_path.parent.mkdir()
    python_path.write_text("#!/bin/sh\nexit 0\n")
    python_path.chmod(0o700)
    config_path = tmp_path / "config $dir" / "config 'quoted'.yml"
    plan = build_tunnel_profile_plan(
        profile="chatgpt-sandbox",
        tunnel_id="tun_shellsafe",
        config_path=config_path,
        python_executable=python_path,
    )

    assert shlex.split(plan.server_command) == list(plan.server_argv)
    assert plan.server_argv[0] == str(python_path)
    assert plan.server_argv[-1] == str(config_path.resolve(strict=False))
    serialized = "\n".join((*plan.server_argv, *plan.argv)).casefold()
    assert "secret" not in serialized
