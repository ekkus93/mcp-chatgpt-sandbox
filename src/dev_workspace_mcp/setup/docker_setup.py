"""Docker workspace setup choices and reviewed image build planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound
from pydantic import ValidationError

from dev_workspace_mcp.config import ContainerConfig, DockerConfig, WorkspaceConfig


class SetupDockerError(RuntimeError):
    """Safe Docker-setup validation failure."""


@dataclass(frozen=True, slots=True)
class DockerBuildPlan:
    """Exact non-secret Docker build action reviewed before mutation."""

    image: str
    project_root: Path
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DockerSetupChoices:
    default_image: str = "mcp-dev-ubuntu:24.04"
    workspace_path: str = "/workspace"
    container_user: str = "host"
    git_dir_mode: str = "read-write"
    idle_stop_after: str = "4h"

    def config_fragments(self) -> dict[str, object]:
        try:
            docker_config = DockerConfig.model_validate(
                {"default_image": self.default_image, "workspace_path": self.workspace_path}
            )
            container = ContainerConfig.model_validate(
                {"user": self.container_user, "git_dir_mode": self.git_dir_mode}
            )
            workspace = WorkspaceConfig.from_yaml_value({"idle_stop_after": self.idle_stop_after})
        except (ValidationError, ValueError) as exc:
            raise SetupDockerError(
                "Docker workspace choices are rejected by production models"
            ) from exc
        idle_value = "never" if workspace.idle_stop_after_seconds is None else self.idle_stop_after
        return {
            "docker": {
                "default_image": docker_config.default_image,
                "workspace_path": str(docker_config.workspace_path),
            },
            "container": {
                "user": container.user,
                "git_dir_mode": container.git_dir_mode,
            },
            "workspace": {"idle_stop_after": idle_value},
        }


def image_exists(client: docker.DockerClient, image: str) -> bool:
    try:
        client.images.get(image)
    except ImageNotFound:
        return False
    except DockerException as exc:
        raise SetupDockerError("Could not inspect Docker image availability") from exc
    return True


def default_image_build_command(
    image: str,
    *,
    project_root: Path | None = None,
) -> tuple[str, ...]:
    build_path = (
        "docker/default/"
        if project_root is None
        else str(project_root.expanduser().resolve(strict=False) / "docker" / "default")
    )
    return ("docker", "build", "-t", image, build_path)


def prepare_default_image_build_plan(*, image: str, project_root: Path) -> DockerBuildPlan:
    """Build the exact Docker build command without mutating Docker state."""

    try:
        docker_config = DockerConfig.model_validate({"default_image": image.strip()})
    except ValidationError as exc:
        raise SetupDockerError("Docker image name is rejected by production models") from exc
    resolved_root = project_root.expanduser().resolve(strict=False)
    return DockerBuildPlan(
        image=docker_config.default_image,
        project_root=resolved_root,
        argv=default_image_build_command(
            docker_config.default_image,
            project_root=resolved_root,
        ),
    )


def build_default_image(
    client: docker.DockerClient,
    *,
    image: str,
    project_root: Path,
    approved: bool,
) -> None:
    if not approved:
        raise SetupDockerError("Docker image build requires explicit approval")
    build_path = project_root / "docker" / "default"
    try:
        client.images.build(path=str(build_path), tag=image, rm=True)
    except (DockerException, OSError) as exc:
        raise SetupDockerError("Default workspace image build failed") from exc
