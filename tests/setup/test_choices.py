from __future__ import annotations

from dev_workspace_mcp.setup.choices import SetupChoices
from dev_workspace_mcp.setup.model import SetupState, SetupStep, invalidate_from_step


def test_wizv_002_non_secret_choices_round_trip() -> None:
    state = SetupState()
    state.choices.state_db_path = "/tmp/state.db"
    state.choices.github_auth_file = "/tmp/github-api.txt"
    state.choices.tunnel_id = "tunnel-example"
    state.reviewed_fields = frozenset({"state_db_path", "github"})
    clone = SetupState.from_dict(state.to_dict())
    assert clone.choices == state.choices
    assert clone.reviewed_fields == state.reviewed_fields


def test_wizv_002_choices_schema_has_no_raw_runtime_secret_field() -> None:
    keys = set(SetupChoices().to_dict())
    assert "runtime_key" not in keys
    assert "api_token" not in keys
    assert "password" not in keys


def test_wizv_008_upstream_change_invalidates_review_and_final_validation() -> None:
    state = SetupState(current_step=SetupStep.FINAL_VALIDATION)
    for step in SetupStep:
        state.statuses[step] = "complete"  # type: ignore[assignment]
    invalidated = invalidate_from_step(state, SetupStep.DOCKER)
    assert invalidated.current_step is SetupStep.DOCKER
    assert SetupStep.REVIEW not in invalidated.statuses
    assert SetupStep.FINAL_VALIDATION not in invalidated.statuses
