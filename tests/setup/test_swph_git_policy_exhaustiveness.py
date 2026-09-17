from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.config import GitPolicyConfig
from mcp_chatgpt_sandbox.setup.git_policy_form import (
    GIT_POLICY_GUIDED_CONTROLS,
    GIT_POLICY_GUIDED_FIELDS,
    GitPolicyEditorValues,
)
from mcp_chatgpt_sandbox.setup.git_policy_setup import GIT_POLICY_UI_FIELDS

_REPO_ROOT = Path(__file__).parents[2]
_SETUP_ROOT = _REPO_ROOT / "src" / "mcp_chatgpt_sandbox" / "setup"


def _check_guided_manifest(
    *,
    policy_model: type[GitPolicyConfig],
    guided_controls: Mapping[str, tuple[str, ...]],
    ui_fields: frozenset[str],
) -> None:
    """The real guided-support exhaustiveness contract, over substitutable inputs.

    PSCR-006 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`) extracted this so
    the mutation tests below can run *this* check against a mutated model or
    manifest. They previously proved only that a locally built set differed from
    the manifest — `A | {x} != A` — which never invoked the contract at all and
    so could not show that adding a field to the real `GitPolicyConfig` would
    fail anything. That was a milder instance of the self-derived comparison
    SWPH-004 existed to remove.
    """

    guided_fields = frozenset(guided_controls)
    production_fields = frozenset(policy_model.model_fields)
    editor_fields = frozenset(field.name for field in fields(GitPolicyEditorValues))

    assert production_fields == guided_fields
    assert editor_fields == guided_fields
    assert ui_fields == guided_fields

    values = GitPolicyEditorValues.from_policy(policy_model())
    assert frozenset(values.to_mapping()) == guided_fields


def test_swphv_004_manifest_matches_production_and_editor_fields_bidirectionally() -> None:
    _check_guided_manifest(
        policy_model=GitPolicyConfig,
        guided_controls=GIT_POLICY_GUIDED_CONTROLS,
        ui_fields=GIT_POLICY_UI_FIELDS,
    )
    # The manifest is the source of the field set, so pin that relationship too.
    assert frozenset(GIT_POLICY_GUIDED_CONTROLS) == GIT_POLICY_GUIDED_FIELDS


def test_swphv_004_future_production_field_fails_guided_exhaustiveness_contract() -> None:
    """A new production field with no guided control must fail the real contract."""

    class FutureGitPolicyConfig(GitPolicyConfig):
        future_policy_field: bool = False

    with pytest.raises(AssertionError):
        _check_guided_manifest(
            policy_model=FutureGitPolicyConfig,
            guided_controls=GIT_POLICY_GUIDED_CONTROLS,
            ui_fields=GIT_POLICY_UI_FIELDS,
        )

    # Sanity: the same call against the unmutated model passes, so the failure
    # above is attributable to the added field and not to a broken helper.
    _check_guided_manifest(
        policy_model=GitPolicyConfig,
        guided_controls=GIT_POLICY_GUIDED_CONTROLS,
        ui_fields=GIT_POLICY_UI_FIELDS,
    )


def test_swphv_004_stale_guided_field_fails_production_contract() -> None:
    """A manifest entry with no production field must fail the real contract."""

    stale_controls = {
        **GIT_POLICY_GUIDED_CONTROLS,
        "retired_policy_field": ("git-policy-retired",),
    }

    with pytest.raises(AssertionError):
        _check_guided_manifest(
            policy_model=GitPolicyConfig,
            guided_controls=stale_controls,
            ui_fields=frozenset(stale_controls),
        )


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
