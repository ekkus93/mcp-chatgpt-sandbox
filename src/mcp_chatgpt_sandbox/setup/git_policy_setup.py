"""Wizard-facing Git safety presets backed by ``GitPolicyConfig``."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ValidationError

from mcp_chatgpt_sandbox.config import GitPolicyConfig
from mcp_chatgpt_sandbox.setup.git_policy_form import GIT_POLICY_GUIDED_FIELDS

GIT_POLICY_UI_FIELDS = GIT_POLICY_GUIDED_FIELDS


class GitPolicyPreset(StrEnum):
    RECOMMENDED = "recommended"
    CONSERVATIVE = "conservative"
    CUSTOM = "custom"


def recommended_policy() -> GitPolicyConfig:
    return GitPolicyConfig()


def conservative_policy() -> GitPolicyConfig:
    return GitPolicyConfig(
        push="deny",
        default_branch_push="deny",
        force_push="deny",
        force_with_lease="deny",
        delete_remote_branch="deny",
        delete_remote_tag="deny",
        default_branch_commit="deny",
        amend_published="deny",
        pull_strategy="ff_only",
        require_remote_protection="deny",
    )


def materialize_policy(
    preset: GitPolicyPreset,
    *,
    custom: dict[str, object] | None = None,
) -> GitPolicyConfig:
    if preset is GitPolicyPreset.RECOMMENDED:
        return recommended_policy()
    if preset is GitPolicyPreset.CONSERVATIVE:
        return conservative_policy()
    if custom is None:
        raise ValueError("Custom Git policy requires reviewed field values")
    try:
        return GitPolicyConfig.model_validate(custom)
    except ValidationError as exc:
        raise ValueError("Custom Git policy is rejected by production validation") from exc


def policy_to_yaml_mapping(policy: GitPolicyConfig) -> dict[str, object]:
    return policy.model_dump(mode="json")
