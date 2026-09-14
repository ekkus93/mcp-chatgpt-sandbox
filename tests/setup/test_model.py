from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from dev_workspace_mcp.setup.model import (
    SETUP_STATE_VERSION,
    SetupRepositoryState,
    SetupState,
    SetupStateError,
    SetupStep,
    SetupStepStatus,
    clone_setup_state,
    dependent_steps,
    invalidate_from_step,
    load_setup_state,
    rewind_for_missing_process_local_material,
    save_setup_state,
)


def _repository() -> SetupRepositoryState:
    return SetupRepositoryState.from_dict(
        {
            "id": "example",
            "path": "/srv/repos/example",
            "github": "example/example",
            "ssh_key": "example.ed25519",
        }
    )


def test_wizv_002_state_round_trip_and_owner_only_persistence(tmp_path: Path) -> None:
    state = SetupState(
        current_step=SetupStep.REPOSITORIES,
        statuses={SetupStep.PREREQUISITES: SetupStepStatus.COMPLETE},
        repositories=(_repository(),),
        advanced=True,
    )
    path = tmp_path / "state" / "setup.json"
    save_setup_state(state, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_setup_state(path) == state
    assert clone_setup_state(state) == state


def test_wizv_002_unknown_version_step_status_and_key_are_rejected() -> None:
    base = SetupState().to_dict()
    for changed in (
        {**base, "version": SETUP_STATE_VERSION + 1},
        {**base, "current_step": "future-step"},
        {**base, "statuses": {"prerequisites": "future-status"}},
        {**base, "future": True},
    ):
        with pytest.raises(ValueError):
            SetupState.from_dict(changed)


def test_wizv_002_secret_bearing_state_fields_are_rejected() -> None:
    payload = SetupState().to_dict()
    payload["repositories"] = [
        {
            "id": "example",
            "path": "/srv/repos/example",
            "github": "example/example",
            "api_token": "never persist this",
        }
    ]
    with pytest.raises(ValueError):
        SetupState.from_dict(payload)


@pytest.mark.parametrize(
    "secret_field",
    [
        "api_key",
        "token",
        "secret",
        "password",
        "private_key",
        "credential",
        "runtime_key",
    ],
)
def test_swf_005_secret_shaped_state_fields_are_rejected(secret_field: str) -> None:
    payload = SetupState().to_dict()
    payload["choices"] = {**payload["choices"], secret_field: "raw-secret-value"}
    with pytest.raises(ValueError, match="secret-bearing"):
        SetupState.from_dict(payload)


def test_swf_005_secret_directory_path_references_remain_allowed() -> None:
    payload = SetupState().to_dict()
    payload["choices"] = {
        **payload["choices"],
        "github_auth_file": "/run/secrets/github-token",
        "tunnel_id": "tun_abc123_non_secret_identifier",
        "tunnel_profile": "chatgpt-sandbox-prod",
    }
    state = SetupState.from_dict(payload)
    assert state.choices.github_auth_file == "/run/secrets/github-token"
    assert state.choices.tunnel_id == "tun_abc123_non_secret_identifier"
    assert state.choices.tunnel_profile == "chatgpt-sandbox-prod"


def test_wizv_002_duplicate_or_conflicting_repository_state_is_rejected() -> None:
    repo = _repository().to_dict()
    base = SetupState().to_dict()
    base["repositories"] = [repo, repo]
    with pytest.raises(ValueError, match="duplicate"):
        SetupState.from_dict(base)


def test_swf_002_duplicate_repository_paths_are_rejected() -> None:
    base = SetupState().to_dict()
    base["repositories"] = [
        {"id": "one", "path": "/srv/repos/example", "github": "example/example"},
        {"id": "two", "path": "/srv/repos/example", "github": "example/other"},
    ]
    with pytest.raises(ValueError, match="duplicate paths"):
        SetupState.from_dict(base)


def test_swf_002_duplicate_github_identities_are_allowed_with_distinct_ids_and_paths() -> None:
    base = SetupState().to_dict()
    base["repositories"] = [
        {"id": "one", "path": "/srv/repos/example-one", "github": "example/example"},
        {"id": "two", "path": "/srv/repos/example-two", "github": "example/example"},
    ]
    state = SetupState.from_dict(base)
    assert [repository.github for repository in state.repositories] == [
        "example/example",
        "example/example",
    ]


def test_wizv_002_runtime_secret_never_reaches_serialized_state() -> None:
    runtime_secret = "runtime-only-value"
    state = SetupState(current_step=SetupStep.CHATGPT, chatgpt_requested=True)
    serialized = json.dumps(state.to_dict())
    assert runtime_secret not in serialized
    assert "token" not in serialized.casefold()
    assert "password" not in serialized.casefold()


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (SetupStep.PREREQUISITES, {SetupStep.FINAL_VALIDATION}),
        (
            SetupStep.STORAGE,
            {SetupStep.CREDENTIALS, SetupStep.REVIEW, SetupStep.FINAL_VALIDATION},
        ),
        (SetupStep.DOCKER, {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
        (
            SetupStep.REPOSITORIES,
            {SetupStep.CREDENTIALS, SetupStep.REVIEW, SetupStep.FINAL_VALIDATION},
        ),
        (SetupStep.CREDENTIALS, {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
        (SetupStep.GIT_POLICY, {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
        (SetupStep.RUNTIME_LIMITS, {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
        (SetupStep.CHATGPT, {SetupStep.FINAL_VALIDATION}),
        (SetupStep.REVIEW, {SetupStep.FINAL_VALIDATION}),
        (SetupStep.FINAL_VALIDATION, set()),
    ],
)
def test_wcr_003_dependency_graph_is_explicit(changed: SetupStep, expected: set[SetupStep]) -> None:
    assert dependent_steps(changed) == expected


@pytest.mark.parametrize("changed", list(SetupStep))
def test_wcr_003_invalidation_preserves_unrelated_completion(changed: SetupStep) -> None:
    state = SetupState(
        current_step=changed,
        statuses=dict.fromkeys(SetupStep, SetupStepStatus.COMPLETE),
    )

    invalidated = invalidate_from_step(state, changed)
    expected = dependent_steps(changed)
    assert invalidated.statuses[changed] is SetupStepStatus.ACTION_REQUIRED
    for step in SetupStep:
        if step is changed:
            continue
        if step in expected:
            assert step not in invalidated.statuses
        else:
            assert invalidated.statuses[step] is SetupStepStatus.COMPLETE


def test_wizv_002_resume_rewinds_for_missing_process_local_material() -> None:
    state = SetupState(
        current_step=SetupStep.REVIEW,
        statuses={
            SetupStep.CREDENTIALS: SetupStepStatus.COMPLETE,
            SetupStep.CHATGPT: SetupStepStatus.COMPLETE,
            SetupStep.FINAL_VALIDATION: SetupStepStatus.COMPLETE,
        },
    )
    rewound = rewind_for_missing_process_local_material(
        state,
        required_steps={SetupStep.CREDENTIALS, SetupStep.CHATGPT},
        available_steps={SetupStep.CREDENTIALS},
    )
    assert rewound.current_step is SetupStep.CHATGPT
    assert rewound.statuses[SetupStep.CHATGPT] is SetupStepStatus.ACTION_REQUIRED
    assert SetupStep.FINAL_VALIDATION not in rewound.statuses
    assert rewound.statuses[SetupStep.CREDENTIALS] is SetupStepStatus.COMPLETE


def test_wizv_002_invalid_state_file_fails_without_echoing_contents(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"password":"do-not-echo"}')
    with pytest.raises(SetupStateError) as exc_info:
        load_setup_state(path)
    assert "do-not-echo" not in str(exc_info.value)
