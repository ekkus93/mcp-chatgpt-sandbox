"""Guided setup-form values for ``GitPolicyConfig`` custom mode."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from dev_workspace_mcp.config import GitPolicyConfig

ALLOW_DENY_VALUES = ("allow", "deny")
DEFAULT_BRANCH_PUSH_VALUES = ("allow", "allow_fast_forward", "require_approval", "deny")
REMOTE_WRITE_POLICY_VALUES = ("allow", "require_approval", "deny")
PULL_STRATEGY_VALUES = ("ff_only", "merge", "rebase")
REMOTE_PROTECTION_VALUES = ("off", "warn", "deny")
REMOTE_PROTECTION_CHECK_VALUES = ("force_push", "deletion")
ALLOWED_URL_SCHEME_VALUES = ("ssh", "https")

# SWPH-004: this is intentionally independent of GitPolicyConfig.model_fields.
# Adding or removing a production field must therefore require an explicit guided
# editor support decision instead of silently changing the setup coverage claim.
GIT_POLICY_GUIDED_CONTROLS: dict[str, tuple[str, ...]] = {
    "push": ("git-policy-push-allow",),
    "default_branch_push": ("git-policy-default-branch-push",),
    "force_push": ("git-policy-force-push",),
    "force_with_lease": ("git-policy-force-with-lease",),
    "delete_remote_branch": ("git-policy-delete-remote-branch",),
    "delete_remote_tag": ("git-policy-delete-remote-tag",),
    "default_branch_commit": ("git-policy-default-branch-commit-allow",),
    "amend_published": ("git-policy-amend-published",),
    "pull_strategy": ("git-policy-pull-strategy",),
    "require_remote_protection": ("git-policy-remote-protection",),
    "remote_protection_checks": (
        "git-policy-check-force-push",
        "git-policy-check-deletion",
    ),
    "allowed_url_schemes": (
        "git-policy-url-ssh",
        "git-policy-url-https",
    ),
}
GIT_POLICY_GUIDED_FIELDS = frozenset(GIT_POLICY_GUIDED_CONTROLS)


class SetupGitPolicyFormError(ValueError):
    """Safe custom Git-policy editor validation failure."""


@dataclass(frozen=True, slots=True)
class GitPolicyEditorValues:
    """One complete guided custom Git-policy form snapshot."""

    push: str
    default_branch_push: str
    force_push: str
    force_with_lease: str
    delete_remote_branch: str
    delete_remote_tag: str
    default_branch_commit: str
    amend_published: str
    pull_strategy: str
    require_remote_protection: str
    remote_protection_checks: tuple[str, ...]
    allowed_url_schemes: tuple[str, ...]

    @classmethod
    def from_policy(cls, policy: GitPolicyConfig) -> GitPolicyEditorValues:
        return cls(
            push=policy.push,
            default_branch_push=policy.default_branch_push,
            force_push=policy.force_push,
            force_with_lease=policy.force_with_lease,
            delete_remote_branch=policy.delete_remote_branch,
            delete_remote_tag=policy.delete_remote_tag,
            default_branch_commit=policy.default_branch_commit,
            amend_published=policy.amend_published,
            pull_strategy=policy.pull_strategy,
            require_remote_protection=policy.require_remote_protection,
            remote_protection_checks=tuple(policy.remote_protection_checks),
            allowed_url_schemes=tuple(policy.allowed_url_schemes),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> GitPolicyEditorValues:
        if value is None:
            return cls.from_policy(GitPolicyConfig())
        try:
            return cls.from_policy(GitPolicyConfig.model_validate(dict(value)))
        except ValidationError as exc:
            raise SetupGitPolicyFormError(
                "Persisted custom Git policy is rejected by production validation"
            ) from exc

    def to_mapping(self) -> dict[str, object]:
        return {
            "push": self.push,
            "default_branch_push": self.default_branch_push,
            "force_push": self.force_push,
            "force_with_lease": self.force_with_lease,
            "delete_remote_branch": self.delete_remote_branch,
            "delete_remote_tag": self.delete_remote_tag,
            "default_branch_commit": self.default_branch_commit,
            "amend_published": self.amend_published,
            "pull_strategy": self.pull_strategy,
            "require_remote_protection": self.require_remote_protection,
            "remote_protection_checks": list(self.remote_protection_checks),
            "allowed_url_schemes": list(self.allowed_url_schemes),
        }

    def to_policy(self) -> GitPolicyConfig:
        try:
            return GitPolicyConfig.model_validate(self.to_mapping())
        except ValidationError as exc:
            raise SetupGitPolicyFormError(
                "Guided custom Git policy is rejected by production validation"
            ) from exc


def custom_policy_from_guided_values(
    *,
    push: str,
    default_branch_push: str,
    force_push: str,
    force_with_lease: str,
    delete_remote_branch: str,
    delete_remote_tag: str,
    default_branch_commit: str,
    amend_published: str,
    pull_strategy: str,
    require_remote_protection: str,
    remote_protection_checks: tuple[str, ...],
    allowed_url_schemes: tuple[str, ...],
) -> GitPolicyConfig:
    """Validate guided UI values through the production Git policy model."""

    return GitPolicyEditorValues(
        push=push,
        default_branch_push=default_branch_push,
        force_push=force_push,
        force_with_lease=force_with_lease,
        delete_remote_branch=delete_remote_branch,
        delete_remote_tag=delete_remote_tag,
        default_branch_commit=default_branch_commit,
        amend_published=amend_published,
        pull_strategy=pull_strategy,
        require_remote_protection=require_remote_protection,
        remote_protection_checks=remote_protection_checks,
        allowed_url_schemes=allowed_url_schemes,
    ).to_policy()
