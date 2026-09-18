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

_PINNED_INSTALLED_VERSION = "0.1.0"


@pytest.fixture(autouse=True)
def _pin_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the PSCR2-002 version check deterministic across this module.

    Several tests relocate the module *outside* a checkout's `src/` tree, which
    routes them through the installed-version comparison. Without this pin they
    would pass only because the real installed version happens to equal the
    fixture default, and would break on the next version bump for reasons that
    have nothing to do with what they test.

    `raising=False` is deliberate: it lets this module run against the code as
    it was *before* PSCR2-002, which is how these tests were shown to
    discriminate. With the default it errored in setup instead, so every test
    "failed" without reaching an assertion.
    """

    # PSCR3-004: `raising=False` *creates* a missing attribute, so on its own it
    # would let a rename of `_installed_version` silently unpin every test here:
    # they would fall back to the real installed version and keep passing only
    # while that happens to equal the pin. The assertion is conditional on the
    # PSCR2-002 check existing, because against pre-PSCR2-002 code neither name
    # exists and the run must still be possible.
    if hasattr(docker_setup, "_check_context_matches_installed_version"):
        assert hasattr(docker_setup, "_installed_version"), (
            "docker_setup._installed_version is gone but the version check remains: "
            "the pin below would patch a name nothing reads. Update this fixture."
        )
    monkeypatch.setattr(
        docker_setup, "_installed_version", lambda: _PINNED_INSTALLED_VERSION, raising=False
    )


def _write_pyproject(root: Path, *, name: str, version: str = "0.1.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
    )


def _write_context(root: Path) -> Path:
    context = root / "docker" / "default"
    context.mkdir(parents=True, exist_ok=True)
    (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return context


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
    # PSCR2-001: this fixture used to write an *empty* pyproject.toml, which
    # declares no project and so can no longer resolve at all. It now declares
    # this project's name, so the test still isolates what it was written to
    # prove — that the layout markers are both required.
    root = tmp_path / "checkout"
    (root / "src" / "mcp_chatgpt_sandbox" / "setup").mkdir(parents=True)
    _write_pyproject(root, name="mcp-chatgpt-sandbox")
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


def test_pscr2v_001_a_right_layout_wrong_name_decoy_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PSCR2-001: the layout markers prove a root holds *a* context, not *ours*.

    Before this task any ancestor with some `pyproject.toml` and some
    `docker/default/` was accepted, so an installed distribution beneath an
    unrelated project with that layout would have built the wrong image.
    """

    decoy = tmp_path / "some-other-project"
    _write_pyproject(decoy, name="some-other-project")
    _write_context(decoy)
    _relocate_module(
        monkeypatch,
        decoy
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "mcp_chatgpt_sandbox"
        / "setup"
        / "docker_setup.py",
    )

    with pytest.raises(SetupDockerError, match="source checkout"):
        bundled_default_image_build_context()


def test_pscr2v_001_a_decoy_below_the_real_root_does_not_shadow_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping must continue the climb, not end it.

    The decoy is nearer to the module than the real root, so a search that
    stopped at the first right-layout candidate — or that returned None on the
    first wrong name — would never reach the correct answer.
    """

    real = tmp_path / "checkout"
    _write_pyproject(real, name="mcp-chatgpt-sandbox")
    real_context = _write_context(real)

    decoy = real / "vendor" / "nested-project"
    _write_pyproject(decoy, name="nested-project")
    _write_context(decoy)

    _relocate_module(
        monkeypatch, decoy / "src" / "mcp_chatgpt_sandbox" / "setup" / "docker_setup.py"
    )

    assert bundled_default_image_build_context() == real_context.resolve()


def test_pscr2v_001_a_malformed_pyproject_is_skipped_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable candidate disqualifies itself; it must not abort resolution.

    A TOML parse error escaping from "where is the build context?" would be
    unrelated to anything the operator can act on.
    """

    real = tmp_path / "checkout"
    _write_pyproject(real, name="mcp-chatgpt-sandbox")
    real_context = _write_context(real)

    broken = real / "vendor" / "broken"
    broken.mkdir(parents=True)
    (broken / "pyproject.toml").write_text("[project\nname = oops", encoding="utf-8")
    _write_context(broken)

    _relocate_module(
        monkeypatch, broken / "src" / "mcp_chatgpt_sandbox" / "setup" / "docker_setup.py"
    )

    assert bundled_default_image_build_context() == real_context.resolve()


def _installed_into_repo_venv(root: Path) -> Path:
    """Module path for a *non-editable* install inside the checkout's own venv."""

    return (
        root
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "mcp_chatgpt_sandbox"
        / "setup"
        / "docker_setup.py"
    )


def test_pscr2v_002_matching_versions_resolve_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    _write_pyproject(root, name="mcp-chatgpt-sandbox", version=_PINNED_INSTALLED_VERSION)
    context = _write_context(root)
    _relocate_module(monkeypatch, _installed_into_repo_venv(root))

    assert bundled_default_image_build_context() == context.resolve()


def test_pscr2v_002_mismatched_versions_fail_closed_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older wheel in a newer checkout must not build from the newer Dockerfile."""

    root = tmp_path / "checkout"
    _write_pyproject(root, name="mcp-chatgpt-sandbox", version="0.2.0")
    _write_context(root)
    _relocate_module(monkeypatch, _installed_into_repo_venv(root))

    with pytest.raises(SetupDockerError) as excinfo:
        bundled_default_image_build_context()

    message = str(excinfo.value)
    assert _PINNED_INSTALLED_VERSION in message
    assert "0.2.0" in message
    assert "Reinstall" in message  # names the remedy


def test_pscr2v_002_undeterminable_installed_version_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Option B fails closed: an unknown version cannot be shown to match."""

    root = tmp_path / "checkout"
    _write_pyproject(root, name="mcp-chatgpt-sandbox")
    _write_context(root)
    _relocate_module(monkeypatch, _installed_into_repo_venv(root))
    monkeypatch.setattr(docker_setup, "_installed_version", lambda: None, raising=False)

    with pytest.raises(SetupDockerError, match="unknown version"):
        bundled_default_image_build_context()


def test_pscr2v_002_an_in_tree_module_skips_the_check_despite_version_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The editable-install guarantee the spec wanted, held by the right property.

    The module lives in the checkout's own `src/` tree, so code and context are
    one tree by construction. The checkout declares a version the "installed"
    metadata disagrees with — precisely a half-done version bump. Comparing
    `__version__` against `pyproject.toml`, as the spec first proposed, would
    have failed the Docker step here over bookkeeping unrelated to Docker.
    """

    root = tmp_path / "checkout"
    _write_pyproject(root, name="mcp-chatgpt-sandbox", version="0.2.0")
    context = _write_context(root)
    _relocate_module(
        monkeypatch, root / "src" / "mcp_chatgpt_sandbox" / "setup" / "docker_setup.py"
    )

    assert bundled_default_image_build_context() == context.resolve()


def test_pscr2v_002_the_real_editable_checkout_passes() -> None:
    """The development install this suite runs from is in-tree, so it resolves."""

    root = docker_setup._source_checkout_root()
    assert root is not None
    assert docker_setup._running_from(root)
    assert (bundled_default_image_build_context() / "Dockerfile").is_file()
