from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

from dev_workspace_mcp.setup.app import SetupWizardApp
from dev_workspace_mcp.setup.choices import SetupChoices
from dev_workspace_mcp.setup.model import SetupRepositoryState
from dev_workspace_mcp.setup.repository_setup import verify_repository
from dev_workspace_mcp.setup.step_panels import WizardStepPanels

_SECRET_FIELD_FRAGMENTS = (
    "api_key",
    "api-key",
    "token",
    "secret",
    "password",
    "private_key",
    "private-key",
    "credential",
    "runtime-key",
)


def _git(path: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
    ).stdout


def _repository_snapshot(path: Path) -> tuple[bytes, ...]:
    return (
        _git(path, "config", "--local", "--list", "--null"),
        _git(path, "remote", "-v"),
        _git(path, "symbolic-ref", "HEAD"),
        _git(path, "rev-parse", "HEAD"),
        _git(path, "status", "--porcelain=v1", "-uall"),
    )


def test_wcr_007_repository_verification_has_no_git_side_effects(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:example/example.git",
        ],
        check=True,
    )
    repository = SetupRepositoryState.from_dict(
        {"id": "example", "path": str(tmp_path), "github": "example/example"}
    )
    before = _repository_snapshot(tmp_path)

    result = verify_repository(repository)

    assert result.valid
    assert _repository_snapshot(tmp_path) == before


def test_wcr_004_resumable_choices_have_no_raw_secret_field_names() -> None:
    keys = SetupChoices().to_dict().keys()
    for key in keys:
        lowered = key.casefold()
        assert not any(fragment in lowered for fragment in _SECRET_FIELD_FRAGMENTS)


def test_wcr_004_012_tui_defines_no_raw_secret_input() -> None:
    source = inspect.getsource(WizardStepPanels)
    input_ids = re.findall(r'id="([^"]+)"', source)
    assert input_ids
    for input_id in input_ids:
        lowered = input_id.casefold()
        assert not any(fragment in lowered for fragment in _SECRET_FIELD_FRAGMENTS)


def test_wcr_005_write_action_cannot_prepare_and_write_in_one_click() -> None:
    source = inspect.getsource(SetupWizardApp.write_reviewed_config)
    assert "self._review_plan is None" in source
    assert "commit_config_update" in source
    assert "prepare_config_update" not in source
