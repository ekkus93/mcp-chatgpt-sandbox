"""Hardened setup step actions for guided policy and reviewed host mutations."""

from __future__ import annotations

import json
import shlex
from typing import Protocol, TypeVar

import docker
from docker.errors import DockerException
from textual import on
from textual.widgets import Button, Checkbox, Select, Static

from dev_workspace_mcp.setup.docker_setup import (
    SetupDockerError,
    build_default_image,
    image_exists,
    prepare_default_image_build_plan,
    validate_default_image_build_context,
)
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


def _command_preview(argv: tuple[str, ...]) -> str:
    return shlex.join(argv)


class SetupStepActionsMixin(_BaseSetupStepActionsMixin):
    """Step handlers with hardened guided editing and reviewed Docker actions."""

    @on(Button.Pressed, "#validate-docker")
    def validate_docker(self) -> None:
        host = self._action_host()
        try:
            choices = self._docker_from_widgets()
            choices.config_fragments()
            plan = prepare_default_image_build_plan(image=choices.default_image)
            defer_image = host.query_one("#docker-defer-image", Checkbox).value
        except (ValueError, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return

        try:
            client = docker.from_env()
            try:
                present = image_exists(client, choices.default_image)
            finally:
                client.close()
        except (DockerException, SetupDockerError):
            present = False
            message = (
                "Docker is unavailable; workspace configuration remains action-required until "
                "Docker-dependent checks can run."
            )
        else:
            if not present and not defer_image:
                host.set_step_validation(
                    SetupStep.DOCKER,
                    valid=False,
                    message=(
                        "Selected image is missing; build it or explicitly defer to final "
                        "diagnostics"
                    ),
                )
                return
            message = (
                "Docker workspace values are valid and the selected image is present."
                if present
                else "Docker workspace values are valid; missing image explicitly deferred."
            )

        host.query_one("#docker-build-command", Static).update(
            "Build command: " + _command_preview(plan.argv)
        )
        candidate = clone_setup_state(host.state)
        candidate.choices.docker_image = choices.default_image
        candidate.choices.workspace_path = choices.workspace_path
        candidate.choices.container_user = choices.container_user
        candidate.choices.git_dir_mode = choices.git_dir_mode
        candidate.choices.idle_stop_after = choices.idle_stop_after
        candidate.reviewed_fields |= frozenset({"docker", "workspace", "container"})
        host._save_validated_state(candidate, step=SetupStep.DOCKER, message=message)

    @on(Button.Pressed, "#prepare-docker-build")
    def prepare_docker_build(self) -> None:
        host = self._action_host()
        host._pending_docker_build_plan = None
        host.query_one("#build-docker-image", Button).disabled = True
        try:
            choices = self._docker_from_widgets()
            plan = prepare_default_image_build_plan(image=choices.default_image)
        except (ValueError, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        host._pending_docker_build_plan = plan
        host.query_one("#docker-build-command", Static).update(
            "Reviewed build plan: " + _command_preview(plan.argv)
        )
        host.query_one("#build-docker-image", Button).disabled = False
        host.set_step_validation(
            SetupStep.DOCKER,
            valid=False,
            message="Review the Docker build command, then apply the stored plan separately.",
        )

    @on(Button.Pressed, "#build-docker-image")
    def build_docker_image(self) -> None:
        host = self._action_host()
        plan = host._pending_docker_build_plan
        if plan is None:
            host.set_step_validation(
                SetupStep.DOCKER,
                valid=False,
                message="Prepare and review a Docker build plan before applying it.",
            )
            return
        try:
            validate_default_image_build_context(plan.build_context)
        except SetupDockerError as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return

        try:
            client = docker.from_env()
            try:
                build_default_image(
                    client,
                    image=plan.image,
                    build_context=plan.build_context,
                    approved=True,
                )
            finally:
                client.close()
        except (DockerException, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        host._pending_docker_build_plan = None
        host.query_one("#build-docker-image", Button).disabled = True
        host.set_step_validation(
            SetupStep.DOCKER,
            valid=False,
            message="Docker build completed from the reviewed plan; validate Docker again.",
        )

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
