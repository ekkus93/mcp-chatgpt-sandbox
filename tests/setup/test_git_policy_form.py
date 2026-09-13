from __future__ import annotations

import pytest

from dev_workspace_mcp.config import GitPolicyConfig
from dev_workspace_mcp.setup.git_policy_form import (
    GIT_POLICY_GUIDED_FIELDS,
    GitPolicyEditorValues,
    SetupGitPolicyFormError,
    custom_policy_from_guided_values,
)


def test_swhv_006_guided_git_policy_values_round_trip_through_production_model() -> None:
    values = GitPolicyEditorValues(
        push="deny",
        default_branch_push="require_approval",
        force_push="deny",
        force_with_lease="require_approval",
        delete_remote_branch="allow",
        delete_remote_tag="deny",
        default_branch_commit="deny",
        amend_published="deny",
        pull_strategy="rebase",
        require_remote_protection="deny",
        remote_protection_checks=("deletion",),
        allowed_url_schemes=("https",),
    )

    policy = values.to_policy()

    assert policy.push == "deny"
    assert policy.default_branch_push == "require_approval"
    assert policy.force_with_lease == "require_approval"
    assert policy.delete_remote_branch == "allow"
    assert policy.default_branch_commit == "deny"
    assert policy.pull_strategy == "rebase"
    assert policy.require_remote_protection == "deny"
    assert policy.remote_protection_checks == ["deletion"]
    assert policy.allowed_url_schemes == ["https"]
    assert GitPolicyEditorValues.from_mapping(policy.model_dump()).to_policy() == policy


def test_swhv_006_guided_git_policy_exhaustively_covers_current_fields() -> None:
    assert GIT_POLICY_GUIDED_FIELDS == frozenset(GitPolicyConfig.model_fields)


def test_swhv_006_guided_git_policy_rejects_invalid_values() -> None:
    with pytest.raises(SetupGitPolicyFormError):
        custom_policy_from_guided_values(
            push="allow",
            default_branch_push="allow_fast_forward",
            force_push="deny",
            force_with_lease="deny",
            delete_remote_branch="require_approval",
            delete_remote_tag="deny",
            default_branch_commit="allow",
            amend_published="deny",
            pull_strategy="sideways",
            require_remote_protection="warn",
            remote_protection_checks=("force_push", "deletion"),
            allowed_url_schemes=("ssh", "https"),
        )
