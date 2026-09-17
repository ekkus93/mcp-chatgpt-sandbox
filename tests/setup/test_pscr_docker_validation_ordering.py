"""PSCR-003 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`): Docker step
validation must not be gated on build-context availability.

Before PSCR-003, `validate_docker()` built the image plan — and therefore
resolved the build context — in the same block that validated the widget values,
and returned early on failure. Because `docker/default/` ships only in a source
checkout (PSCR-001), an installed deployment could not validate this step even
with the configured image already present, `#next` stayed disabled, and the
`#docker-defer-image` escape hatch was unreachable because the checkbox was only
read after plan construction had already succeeded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageNotFound
from textual.widgets import Button, Checkbox, Static

from mcp_chatgpt_sandbox.setup.app import STEPS, SetupWizardApp
from mcp_chatgpt_sandbox.setup.docker_setup import SetupDockerError
from mcp_chatgpt_sandbox.setup.model import SetupStep


def _go_to(app: SetupWizardApp, step: SetupStep) -> None:
    app._current_index = next(
        index for index, definition in enumerate(STEPS) if definition.step is step
    )
    app._refresh()


def _static_text(app: SetupWizardApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


def _unavailable_context(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_context() -> Path:
        raise SetupDockerError("Docker build context is unavailable")

    monkeypatch.setattr(
        "mcp_chatgpt_sandbox.setup.docker_setup.bundled_default_image_build_context",
        missing_context,
    )


def _fake_docker(monkeypatch: pytest.MonkeyPatch, *, image_present: bool) -> dict[str, int]:
    counters = {"from_env": 0, "build": 0}

    class Images:
        def get(self, _image: str) -> object:
            if not image_present:
                raise ImageNotFound("absent")
            return object()

        def build(self, **_: object) -> None:
            counters["build"] += 1

    class Client:
        images = Images()

        def close(self) -> None:
            return None

    def from_env() -> Any:
        counters["from_env"] += 1
        return Client()

    monkeypatch.setattr("mcp_chatgpt_sandbox.setup.step_actions_docker.docker.from_env", from_env)
    return counters


@pytest.mark.asyncio
async def test_pscrv_003_present_image_validates_and_unblocks_next_without_a_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unavailable_context(monkeypatch)
    counters = _fake_docker(monkeypatch, image_present=True)

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is True
        # The operator must actually be able to leave the step, not merely see a
        # green message: a disabled #next is what made this a blocked wizard.
        assert app.query_one("#next", Button).disabled is False
        assert "Build command unavailable" in _static_text(app, "#docker-build-command")

    assert counters["build"] == 0


@pytest.mark.asyncio
async def test_pscrv_003_missing_image_without_context_is_actionable_and_deferral_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unavailable_context(monkeypatch)
    counters = _fake_docker(monkeypatch, image_present=False)

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is False
        assert "image is missing" in app._validation_messages[SetupStep.DOCKER]

        # The deferral is the documented way past a missing image. It used to be
        # unreachable whenever the context was unavailable.
        app.query_one("#docker-defer-image", Checkbox).value = True
        await pilot.pause()
        app.validate_docker()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is True
        assert "explicitly deferred" in app._validation_messages[SetupStep.DOCKER]

    assert counters["build"] == 0


@pytest.mark.asyncio
async def test_pscrv_003_build_path_still_refuses_an_unavailable_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PSCR-003 relaxes validation only. The build path genuinely needs a context,
    # so it must stay action-required and must never reach Docker.
    _unavailable_context(monkeypatch)
    counters = _fake_docker(monkeypatch, image_present=True)

    app = SetupWizardApp(config_path=tmp_path / "config.yml", state_path=tmp_path / "state.json")
    async with app.run_test() as pilot:
        _go_to(app, SetupStep.DOCKER)
        await pilot.pause()
        app.prepare_docker_build()
        await pilot.pause()

        assert app._validation[SetupStep.DOCKER] is False
        assert "context is unavailable" in app._validation_messages[SetupStep.DOCKER]
        assert app._pending_docker_build_plan is None
        assert app.query_one("#build-docker-image", Button).disabled is True

        app.build_docker_image()
        await pilot.pause()

        assert "Prepare and review" in app._validation_messages[SetupStep.DOCKER]

    assert counters["build"] == 0
