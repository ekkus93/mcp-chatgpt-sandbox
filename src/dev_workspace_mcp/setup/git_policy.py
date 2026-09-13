"""Wizard presentation presets projected onto production GitPolicyConfig."""

from __future__ import annotations

from dev_workspace_mcp.config.models import GitPolicyConfig
from dev_workspace_mcp.setup.model import GitPolicyPreset


def recommended_policy() -> GitPolicyConfig:
    """Return the production defaults used by the Recommended preset."""

    return GitPolicyConfig()


def conservative_policy() -> GitPolicyConfig:
    """Return a policy no less restrictive than Recommended for mutations."""

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
        remote_protection_checks=["force_push", "deletion"],
        allowed_url_schemes=["ssh"],
    )


def policy_for_preset(preset: GitPolicyPreset) -> GitPolicyConfig:
    if preset is GitPolicyPreset.RECOMMENDED:
        return recommended_policy()
    if preset is GitPolicyPreset.CONSERVATIVE:
        return conservative_policy()
    raise ValueError("Custom Git policy requires explicit field values")


__all__ = ["conservative_policy", "policy_for_preset", "recommended_policy"]
