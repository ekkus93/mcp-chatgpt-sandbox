"""Hardened setup step actions that replace JSON-first Git-policy editing."""

from __future__ import annotations

import json
from typing import Protocol, TypeVar

from textual import on
from textual.widgets import Button, Checkbox, Select, Static

from dev_workspace_mcp.setup.docker_setup import SetupDockerError, default_image_build_command
from dev_workspace_mcp.setup.git_policy_form import (
    GitPolicyEditorValues,
    SetupGitPolicyFormError,
)
from dev_workspace_mcp.setup.git_policy_setup import GitPolicyPreset, materialize_policy
from dev_workspace_mcp.setup.model import SetupStep, clone_setup_state
from dev_workspace_mcp.setup.step_actions import SetupStepActionsMixin as _BaseSetupStepActionsMixin

_T = TypeVar("_T")


class _GuidedActionHost(Protocol):
    def query_one(self, selector: str, expect_type: type[_T]) -> _T: ...


def _selected(host: _GuidedActionHost, selector: str, *, label: str) -> str:
    value = host.query_one(selector, Select).value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be selected")
    return value


def _checked(host: _GuidedActionHost, selector: str) -> bool:
    return bool(host.query_one(selector, Checkbox).value)


def _remote_protection_checks(host: _GuidedActionHost) -> tuple[str, ...]:
    values: list[str] = []
    if _checked(host, "#git-policy-check-force-push"):
        values.append("force_push")
    if _checked(host, "#git-policy-check-deletion"):
        values.append("deletion")
    return tuple(values)


def _allowed_url_schemes(host: _GuidedActionHost) -> tuple[str, ...]:
    values: list[str] = []
    if _checked(host, "#git-policy-url-ssh"):
        values.append("ssh")
    if _checked(host, "#git-policy-url-https"):
        values.append("https")
    return tuple(values)


class SetupStepActionsMixin(_BaseSetupStepActionsMixin):
    """Hardened setup handlers layered over the ordinary step actions."""

    @on(Button.Pressed, "#validate-docker")
    def validate_docker(self) -> None:
        host = self._action_host()
        try:
            choices = self._docker_from_widgets()
            default_image_build_command(choices.default_image)
        except ValueError:
            super().validate_docker()
            return
        except SetupDockerError as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        try:
            super().validate_docker()
        except SetupDockerError as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))

    @on(Button.Pressed, "#build-docker-image")
    def build_docker_image(self) -> None:
        host = self._action_host()
        try:
            plan = host._pending_docker_build_plan
            if plan is not None and not plan.build_context.is_dir():
                raise SetupDockerError(
                    "Default Docker build context is unavailable; build or pull the image manually."
                )
            super().build_docker_image()
        except SetupDockerError as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))

    def _custom_git_policy_from_guided_widgets(self) -> dict[str, object]:
        host = self._action_host()
        values = GitPolicyEditorValues(
            push="allow" if _checked(host, "#git-policy-push-allow") else "deny",
            default_branch_push=_selected(
                host,
                "#git-policy-default-branch-push",
                label="default branch push policy",
            ),
            force_push=_selected(host, "#git-policy-force-push", label="force push policy"),
            force_with_lease=_selected(
                host,
                "#git-policy-force-with-lease",
                label="force-with-lease policy",
            ),
            delete_remote_branch=_selected(
                host,
                "#git-policy-delete-remote-branch",
                label="remote branch deletion policy",
            ),
            delete_remote_tag=_selected(
                host,
                "#git-policy-delete-remote-tag",
                label="remote tag deletion policy",
            ),
            default_branch_commit=(
                "allow" if _checked(host, "#git-policy-default-branch-commit-allow") else "deny"
            ),
            amend_published=_selected(
                host,
                "#git-policy-amend-published",
                label="published amend policy",
            ),
            pull_strategy=_selected(host, "#git-policy-pull-strategy", label="pull strategy"),
            require_remote_protection=_selected(
                host,
                "#git-policy-remote-protection",
                label="remote-protection requirement",
            ),
            remote_protection_checks=_remote_protection_checks(host),
            allowed_url_schemes=_allowed_url_schemes(host),
        )
        return values.to_policy().model_dump(mode="json")

    @on(Button.Pressed, "#validate-git-policy")
    def validate_git_policy(self) -> None:
        host = self._action_host()
        try:
            preset = GitPolicyPreset(
                _selected(host, "#git-policy-preset", label="Git safety preset")
            )
            custom = (
                self._custom_git_policy_from_guided_widgets()
                if preset is GitPolicyPreset.CUSTOM
                else None
            )
            policy = materialize_policy(preset, custom=custom)
        except (ValueError, SetupGitPolicyFormError) as exc:
            host.set_step_validation(SetupStep.GIT_POLICY, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(host.state)
        candidate.choices.git_policy_preset = preset.value
        candidate.choices.git_policy_custom = (
            policy.model_dump(mode="json") if preset is GitPolicyPreset.CUSTOM else None
        )
        candidate.reviewed_fields |= frozenset({"git_policy"})
        host.query_one("#git-policy-message", Static).update(
            "Validated production Git policy: " + json.dumps(policy.model_dump(mode="json"))
        )
        host._save_validated_state(
            candidate,
            step=SetupStep.GIT_POLICY,
            message="Git safety policy is production-valid.",
        )
