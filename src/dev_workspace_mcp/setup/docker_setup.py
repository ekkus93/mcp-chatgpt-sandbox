"""Docker workspace setup choices and read-only image inspection."""

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


def bundled_default_image_build_context() -> Path:
    """Resolve the repository-bundled default Docker build context.

    The setup wizard may be launched from any current working directory. The
    bundled Docker context is therefore resolved from this module's installed
    source tree, not from ``Path.cwd()``.
    """

    context = Path(__file__).resolve().parents[3] / "docker" / "default"
    if not context.is_dir():
        raise SetupDockerError(
            "Default Docker build context is unavailable; build or pull the image manually."
        )
    return context


def default_image_build_command(
    image: str, *, build_context: Path | None = None
) -> tuple[str, ...]:
    context = bundled_default_image_build_context() if build_context is None else build_context
    return ("docker", "build", "-t", image, str(context))


def build_default_image(
    client: docker.DockerClient,
    *,
    image: str,
    approved: bool,
    build_context: Path | None = None,
) -> None:
    if not approved:
        raise SetupDockerError("Docker image build requires explicit approval")
    build_path = bundled_default_image_build_context() if build_context is None else build_context
    try:
        client.images.build(path=str(build_path), tag=image, rm=True)
    except (DockerException, OSError) as exc:
        raise SetupDockerError("Default workspace image build failed") from exc
