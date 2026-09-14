from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from dev_workspace_mcp.config import GitPolicyConfig
from dev_workspace_mcp.setup.git_policy_form import (
    GIT_POLICY_GUIDED_CONTROLS,
    GIT_POLICY_GUIDED_FIELDS,
    GitPolicyEditorValues,
)
from dev_workspace_mcp.setup.git_policy_setup import GIT_POLICY_UI_FIELDS

_REPO_ROOT = Path(__file__).parents[2]
_SETUP_ROOT = _REPO_ROOT / "src" / "dev_workspace_mcp" / "setup"


def test_swphv_004_manifest_matches_production_and_editor_fields_bidirectionally() -> None:
    production_fields = frozenset(GitPolicyConfig.model_fields)
    editor_fields = frozenset(field.name for field in fields(GitPolicyEditorValues))

    assert production_fields == GIT_POLICY_GUIDED_FIELDS
    assert editor_fields == GIT_POLICY_GUIDED_FIELDS
    assert GIT_POLICY_UI_FIELDS == GIT_POLICY_GUIDED_FIELDS
    assert frozenset(GIT_POLICY_GUIDED_CONTROLS) == GIT_POLICY_GUIDED_FIELDS

    values = GitPolicyEditorValues.from_policy(GitPolicyConfig())
    assert frozenset(values.to_mapping()) == GIT_POLICY_GUIDED_FIELDS


def test_swphv_004_future_production_field_fails_guided_exhaustiveness_contract() -> None:
    class FutureGitPolicyConfig(GitPolicyConfig):
        future_policy_field: bool = False

    future_fields = frozenset(FutureGitPolicyConfig.model_fields)

    with pytest.raises(AssertionError):
        assert future_fields == GIT_POLICY_GUIDED_FIELDS


def test_swphv_004_stale_guided_field_fails_production_contract() -> None:
    stale_guided_fields = GIT_POLICY_GUIDED_FIELDS | {"retired_policy_field"}

    with pytest.raises(AssertionError):
        assert stale_guided_fields == frozenset(GitPolicyConfig.model_fields)


def test_swphv_004_every_supported_field_has_panel_population_and_extraction_controls() -> None:
    panel_source = (_SETUP_ROOT / "git_policy_panel.py").read_text(encoding="utf-8")
    population_source = (_SETUP_ROOT / "app.py").read_text(encoding="utf-8")
    extraction_source = (_SETUP_ROOT / "step_actions_hardened.py").read_text(encoding="utf-8")

    for field_name, control_ids in GIT_POLICY_GUIDED_CONTROLS.items():
        assert control_ids, f"guided field {field_name!r} has no UI controls"
        for control_id in control_ids:
            assert f'id="{control_id}"' in panel_source, (
                f"guided field {field_name!r} control {control_id!r} is missing from panel"
            )
            selector = f'"#{control_id}"'
            assert selector in population_source, (
                f"guided field {field_name!r} control {control_id!r} is not populated"
            )
            assert selector in extraction_source, (
                f"guided field {field_name!r} control {control_id!r} is not extracted"
            )
