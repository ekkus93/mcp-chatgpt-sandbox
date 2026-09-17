"""PSCR-001/PSCR-002 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`): the
bundled Docker build context is a source-checkout asset, and its resolution
must not depend on the repository layout.

PSCRV-002 requires the installed-layout case to be proven *without* depending on
the development layout. That constraint is the whole point: the defect these
tests cover survived SWPH closure precisely because the project is installed
editable, so `__file__` always pointed into `src/` and every existing test took
the checkout branch. A test that merely ran from the checkout could not observe
it. These tests therefore synthesize the module location instead of trusting the
one this process happens to have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.setup import docker_setup
from mcp_chatgpt_sandbox.setup.docker_setup import (
    SetupDockerError,
    bundled_default_image_build_context,
    prepare_default_image_build_plan,
)


def _relocate_module(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Point the module's ``__file__`` at a synthesized location."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(docker_setup, "__file__", str(path))


def test_pscrv_002_context_resolves_from_a_source_checkout() -> None:
    context = bundled_default_image_build_context()

    assert context.is_dir()
    assert (context / "Dockerfile").is_file()


def test_pscrv_002_installed_layout_without_a_checkout_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mirrors a real wheel install: no ancestor carries pyproject.toml, and no
    # ancestor carries docker/default. The pre-PSCR arithmetic returned
    # <site-packages>/../../docker/default here and only failed later, inside
    # validate_default_image_build_context.
    installed = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    _relocate_module(monkeypatch, installed / "mcp_chatgpt_sandbox" / "setup" / "docker_setup.py")

    with pytest.raises(SetupDockerError) as excinfo:
        bundled_default_image_build_context()

    message = str(excinfo.value)
    assert "source checkout" in message
    assert "docker build" in message


def test_pscrv_002_upward_search_requires_both_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pyproject.toml alone must not satisfy the search: an installed
    # distribution can easily sit under an unrelated project root, and resolving
    # to it would resurrect the original defect in a new disguise.
    root = tmp_path / "unrelated-project"
    (root / "src" / "mcp_chatgpt_sandbox" / "setup").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    _relocate_module(
        monkeypatch, root / "src" / "mcp_chatgpt_sandbox" / "setup" / "docker_setup.py"
    )

    with pytest.raises(SetupDockerError):
        bundled_default_image_build_context()

    (root / "docker" / "default").mkdir(parents=True)
    (root / "docker" / "default" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    assert bundled_default_image_build_context() == (root / "docker" / "default").resolve()


def test_pscrv_002_preview_and_execution_resolve_the_same_context() -> None:
    plan = prepare_default_image_build_plan(image="mcp-chatgpt-sandbox-test:24.04")

    assert plan.argv[-1] == str(plan.build_context)
    assert plan.argv[3] == plan.image
