from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, Input, Static

from dev_workspace_mcp.setup.app import STEPS, SetupWizardApp
from dev_workspace_mcp.setup.docker_setup import prepare_default_image_build_plan
from dev_workspace_mcp.setup.model import SetupStep
from dev_workspace_mcp.setup.tunnel import build_tunnel_profile_plan


def _go_to(app: SetupWizardApp, step: SetupStep) -> None:
    app._current_index = next(
        index for index, definition in enumerate(STEPS) if definition.step is step
    )
    app._refresh()


def _static_text(app: SetupWizardApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


@pytest.mark.asyncio
async def test_swf_001_docker_prepare_displays_plan_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_build(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("prepare must not build Docker images")

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.build_default_image", fail_build)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app.query_one("#docker-image", Input).value = "reviewed-image:latest"
        await pilot.pause()
        app.prepare_docker_build()
        await pilot.pause()

        assert app._pending_docker_build_plan is not None
        assert app._pending_docker_build_plan.image == "reviewed-image:latest"
        assert "reviewed-image:latest" in _static_text(app, "#docker-build-command")
        assert not app.query_one("#build-docker-image", Button).disabled


@pytest.mark.asyncio
async def test_swf_001_docker_apply_requires_pending_plan(tmp_path: Path) -> None:
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app.build_docker_image()
        await pilot.pause()

        assert app._pending_docker_build_plan is None
        assert app.query_one("#build-docker-image", Button).disabled
        assert app._validation.get(SetupStep.DOCKER, False) is False


@pytest.mark.asyncio
async def test_swf_001_docker_apply_uses_stored_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    built: list[str] = []

    class FakeClient:
        def close(self) -> None:
            return None

    def fake_build(_client: FakeClient, *, image: str, project_root: Path, approved: bool) -> None:
        del project_root
        assert approved
        built.append(image)

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.docker.from_env", FakeClient)
    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.build_default_image", fake_build)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app._pending_docker_build_plan = prepare_default_image_build_plan(
            image="stored-image:latest",
            project_root=tmp_path,
        )
        app.query_one("#docker-image", Input).value = "changed-image:latest"
        app.build_docker_image()
        await pilot.pause()

    assert built == ["stored-image:latest"]


@pytest.mark.asyncio
async def test_swf_001_docker_control_edits_invalidate_pending_plan(tmp_path: Path) -> None:
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app.query_one("#docker-image", Input).value = "planned-image:latest"
        await pilot.pause()
        app.prepare_docker_build()
        await pilot.pause()
        assert app._pending_docker_build_plan is not None

        app.query_one("#docker-image", Input).value = "changed-image:latest"
        await pilot.pause()

        assert app._pending_docker_build_plan is None
        assert app.query_one("#build-docker-image", Button).disabled


@pytest.mark.asyncio
async def test_swf_001_tunnel_prepare_displays_plan_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_apply(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("prepare must not mutate tunnel-client profiles")

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.apply_tunnel_profile", fail_apply)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app.query_one("#tunnel-id", Input).value = "tun_reviewed"
        app.query_one("#tunnel-profile", Input).value = "reviewed-profile"
        await pilot.pause()
        app.prepare_tunnel()
        await pilot.pause()

        assert app._pending_tunnel_plan is not None
        assert app._pending_tunnel_plan.tunnel_id == "tun_reviewed"
        assert "tun_reviewed" in _static_text(app, "#tunnel-command")
        assert not app.query_one("#tunnel-apply", Button).disabled


@pytest.mark.asyncio
async def test_swf_001_tunnel_apply_requires_pending_plan(tmp_path: Path) -> None:
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app.apply_tunnel()
        await pilot.pause()

        assert app._pending_tunnel_plan is None
        assert app.query_one("#tunnel-apply", Button).disabled
        assert app._validation.get(SetupStep.CHATGPT, False) is False


@pytest.mark.asyncio
async def test_swf_001_tunnel_apply_uses_stored_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    applied: list[str] = []

    def fake_apply(plan: Any, *, approved: bool) -> None:
        assert approved
        applied.append(plan.tunnel_id)

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.apply_tunnel_profile", fake_apply)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app._pending_tunnel_plan = build_tunnel_profile_plan(
            profile="stored-profile",
            tunnel_id="tun_stored",
            config_path=tmp_path / "config.yml",
            project_root=tmp_path,
        )
        app.query_one("#tunnel-id", Input).value = "tun_changed"
        app.apply_tunnel()
        await pilot.pause()

    assert applied == ["tun_stored"]


@pytest.mark.asyncio
async def test_swf_001_tunnel_control_edits_invalidate_pending_plan(tmp_path: Path) -> None:
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.CHATGPT)
        app.query_one("#tunnel-id", Input).value = "tun_planned"
        app.query_one("#tunnel-profile", Input).value = "planned-profile"
        await pilot.pause()
        app.prepare_tunnel()
        await pilot.pause()
        assert app._pending_tunnel_plan is not None

        app.query_one("#tunnel-id", Input).value = "tun_changed"
        await pilot.pause()

        assert app._pending_tunnel_plan is None
        assert app.query_one("#tunnel-apply", Button).disabled
