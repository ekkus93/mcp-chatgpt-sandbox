from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from docker.errors import DockerException
from textual.widgets import Button, Static

from dev_workspace_mcp.setup.app import STEPS, SetupWizardApp
from dev_workspace_mcp.setup.docker_setup import DockerBuildPlan, SetupDockerError
from dev_workspace_mcp.setup.model import SetupStep, SetupStepStatus


def _go_to(app: SetupWizardApp, step: SetupStep) -> None:
    app._current_index = next(
        index for index, definition in enumerate(STEPS) if definition.step is step
    )
    app._refresh()


def _static_text(app: SetupWizardApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


@pytest.mark.asyncio
async def test_swphv_002_validate_missing_build_context_is_action_required_before_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker_calls: list[str] = []

    def missing_command(_image: str) -> tuple[str, ...]:
        raise SetupDockerError(
            "Default Docker build context is unavailable; build or pull the image manually."
        )

    def unexpected_docker() -> Any:
        docker_calls.append("from_env")
        raise AssertionError("Docker must not be contacted when build-context resolution fails")

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.step_actions_hardened.default_image_build_command",
        missing_command,
    )
    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.docker.from_env", unexpected_docker)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")

    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        message = _static_text(app, "#validation-message")
        assert docker_calls == []
        assert "build context is unavailable" in message
        assert "build or pull the image manually" in message
        assert app.query_one("#next", Button).disabled


@pytest.mark.asyncio
async def test_swphv_002_apply_missing_reviewed_context_never_contacts_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_sentinel = "context-secret-sentinel"
    missing_context = tmp_path / secret_sentinel / "docker" / "default"
    docker_calls: list[str] = []
    build_calls: list[str] = []

    def unexpected_docker() -> Any:
        docker_calls.append("from_env")
        raise AssertionError("Docker must not be contacted when the reviewed context is missing")

    def unexpected_build(*_args: Any, **_kwargs: Any) -> None:
        build_calls.append("build")
        raise AssertionError("Docker build must not run when the reviewed context is missing")

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.docker.from_env", unexpected_docker)
    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.build_default_image", unexpected_build)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")

    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app._pending_docker_build_plan = DockerBuildPlan(
            image="reviewed-image:latest",
            build_context=missing_context,
            argv=("docker", "build", "-t", "reviewed-image:latest", str(missing_context)),
        )
        app.query_one("#build-docker-image", Button).disabled = False
        app.build_docker_image()
        await pilot.pause()

        message = _static_text(app, "#validation-message")
        assert docker_calls == []
        assert build_calls == []
        assert "build context is unavailable" in message
        assert "build or pull the image manually" in message
        assert secret_sentinel not in message
        assert app._pending_docker_build_plan is not None


@pytest.mark.asyncio
async def test_swphv_002_docker_daemon_unavailable_remains_degraded_and_non_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    daemon_sentinel = "daemon-secret-sentinel"

    def unavailable() -> Any:
        raise DockerException(daemon_sentinel)

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions.docker.from_env", unavailable)
    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")

    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        message = _static_text(app, "#validation-message")
        assert "Docker is unavailable" in message
        assert "action-required" in message
        assert daemon_sentinel not in message
        assert app.state.statuses[SetupStep.DOCKER] is SetupStepStatus.ACTION_REQUIRED
        assert not app.query_one("#next", Button).disabled
