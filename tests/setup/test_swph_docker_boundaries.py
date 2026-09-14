from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
from docker.errors import DockerException
from textual.widgets import Input, Static

from dev_workspace_mcp.setup.app import STEPS, SetupWizardApp
from dev_workspace_mcp.setup.docker_setup import (
    SetupDockerError,
    build_default_image,
    prepare_default_image_build_plan,
)
from dev_workspace_mcp.setup.model import SetupStep


def _go_to(app: SetupWizardApp, step: SetupStep) -> None:
    app._current_index = next(
        index for index, definition in enumerate(STEPS) if definition.step is step
    )
    app._refresh()


def _static_text(app: SetupWizardApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


@pytest.mark.asyncio
async def test_swph_002_missing_context_during_validation_stays_inside_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker_calls = 0

    def missing_context() -> Path:
        raise SetupDockerError("Docker build context is unavailable")

    def fail_docker() -> Any:
        nonlocal docker_calls
        docker_calls += 1
        raise AssertionError("Docker must not be touched after context resolution fails")

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.docker_setup.bundled_default_image_build_context",
        missing_context,
    )
    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions_docker.docker.from_env", fail_docker)

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is False
        assert "context is unavailable" in app._validation_messages[SetupStep.DOCKER]

    assert docker_calls == 0


@pytest.mark.asyncio
async def test_swph_002_context_removed_before_apply_never_acquires_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = tmp_path / "reviewed-context"
    context.mkdir()
    plan = prepare_default_image_build_plan(
        image="reviewed-image:latest",
        build_context=context,
    )
    context.rmdir()
    docker_calls = 0
    build_calls = 0

    def fail_docker() -> Any:
        nonlocal docker_calls
        docker_calls += 1
        raise AssertionError("Docker client must not be acquired for a missing context")

    def fail_build(*_args: Any, **_kwargs: Any) -> None:
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("Docker build must not run for a missing context")

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions_docker.docker.from_env", fail_docker)
    monkeypatch.setattr(
        "dev_workspace_mcp.setup.step_actions_docker.build_default_image", fail_build
    )

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app._pending_docker_build_plan = plan
        app.build_docker_image()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is False
        assert "context is unavailable" in app._validation_messages[SetupStep.DOCKER]
        assert app._pending_docker_build_plan is plan

    assert docker_calls == 0
    assert build_calls == 0


def test_swph_002_domain_build_rejects_missing_context_before_docker_mutation(
    tmp_path: Path,
) -> None:
    build_calls = 0

    class Images:
        def build(self, **_: object) -> None:
            nonlocal build_calls
            build_calls += 1

    class Client:
        images = Images()

    with pytest.raises(SetupDockerError, match="context is unavailable"):
        build_default_image(
            Client(),  # type: ignore[arg-type]
            image="reviewed-image:latest",
            approved=True,
            build_context=tmp_path / "missing-context",
        )

    assert build_calls == 0


@pytest.mark.asyncio
async def test_swph_003_preview_round_trips_and_apply_uses_exact_reviewed_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = tmp_path / "build context $HOME;quo'te"
    context.mkdir()
    secret = "SWPH_SECRET_SHOULD_NOT_APPEAR"
    monkeypatch.setenv("SWPH_TEST_SECRET", secret)
    real_prepare = prepare_default_image_build_plan

    def prepare_with_sensitive_path(*, image: str) -> Any:
        return real_prepare(image=image, build_context=context)

    built: list[tuple[str, Path, bool]] = []

    class FakeClient:
        def close(self) -> None:
            return None

    def fake_build(
        _client: FakeClient,
        *,
        image: str,
        build_context: Path,
        approved: bool,
    ) -> None:
        built.append((image, build_context, approved))

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.step_actions_docker.prepare_default_image_build_plan",
        prepare_with_sensitive_path,
    )
    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions_docker.docker.from_env", FakeClient)
    monkeypatch.setattr(
        "dev_workspace_mcp.setup.step_actions_docker.build_default_image", fake_build
    )

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        app.query_one("#docker-image", Input).value = "preview-image:latest"
        await pilot.pause()
        app.prepare_docker_build()
        await pilot.pause()

        plan = app._pending_docker_build_plan
        assert plan is not None
        preview = _static_text(app, "#docker-build-command")
        rendered = preview.removeprefix("Reviewed build plan: ")
        assert shlex.split(rendered) == list(plan.argv)
        assert plan.build_context == context.resolve()
        assert secret not in preview

        app.build_docker_image()
        await pilot.pause()

    assert built == [(plan.image, plan.build_context, True)]


@pytest.mark.asyncio
async def test_swph_002_docker_daemon_unavailable_remains_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unavailable() -> Any:
        raise DockerException("daemon unavailable")

    monkeypatch.setattr("dev_workspace_mcp.setup.step_actions_docker.docker.from_env", unavailable)

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        assert "Docker is unavailable" in app._validation_messages[SetupStep.DOCKER]
