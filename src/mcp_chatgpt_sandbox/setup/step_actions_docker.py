"""Hardened reviewed Docker actions for the Textual setup wizard."""

from __future__ import annotations

import shlex

import docker
from docker.errors import DockerException
from textual import on
from textual.widgets import Button, Checkbox, Static

from mcp_chatgpt_sandbox.setup.docker_setup import (
    SetupDockerError,
    build_default_image,
    image_exists,
    prepare_default_image_build_plan,
    validate_default_image_build_context,
)
from mcp_chatgpt_sandbox.setup.model import SetupStep, clone_setup_state
from mcp_chatgpt_sandbox.setup.step_actions import (
    SetupStepActionsMixin as _BaseSetupStepActionsMixin,
)


def _command_preview(argv: tuple[str, ...]) -> str:
    return shlex.join(argv)


class DockerStepActionsMixin(_BaseSetupStepActionsMixin):
    """Keep Docker planning, preview, and apply failures inside the wizard."""

    @on(Button.Pressed, "#validate-docker")
    def validate_docker(self) -> None:
        host = self._action_host()
        try:
            choices = self._docker_from_widgets()
            choices.config_fragments()
            defer_image = host.query_one("#docker-defer-image", Checkbox).value
        except (ValueError, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return

        # PSCR-003 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`): a build
        # context is required only by the build path, so resolving one must not
        # gate validation. It used to: plan construction sat in the block above,
        # and `docker/default/` ships only in a source checkout (PSCR-001), so an
        # installed deployment could not validate this step even with the
        # configured image already present on the host — and the deferral below
        # was unreachable, because `#docker-defer-image` was only read after plan
        # construction had already succeeded.
        #
        # Catching `SetupDockerError` narrowly here is safe rather than sloppy:
        # `config_fragments()` above has already validated the image name through
        # the same `DockerConfig` the plan builder uses, so a rejected image has
        # failed before this point and the only remaining error is an unavailable
        # context. The preview is therefore best-effort, and its absence is
        # reported as such instead of being reported as invalid Docker values.
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

        try:
            plan = prepare_default_image_build_plan(image=choices.default_image)
        except SetupDockerError as exc:
            build_preview = f"Build command unavailable: {exc}"
        else:
            build_preview = "Build command: " + _command_preview(plan.argv)

        host.query_one("#docker-build-command", Static).update(build_preview)
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
