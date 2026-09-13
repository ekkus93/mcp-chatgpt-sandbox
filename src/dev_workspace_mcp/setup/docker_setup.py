"""Explicit, bounded Docker validation and default-image build actions."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound

_BUILD_TIMEOUT_SECONDS = 15 * 60.0


class DockerSetupStatus(StrEnum):
    AVAILABLE = "available"
    IMAGE_MISSING = "image_missing"
    DAEMON_UNAVAILABLE = "daemon_unavailable"


@dataclass(frozen=True, slots=True)
class DockerSetupResult:
    status: DockerSetupStatus
    message: str


@dataclass(frozen=True, slots=True)
class DockerBuildPlan:
    image: str
    context: Path
    argv: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return shlex.join(self.argv)


class DockerSetupError(RuntimeError):
    """Secret-safe Docker setup failure."""


def inspect_docker_image(image: str) -> DockerSetupResult:
    """Check daemon reachability and local image presence without mutation."""

    try:
        client = docker.from_env()
        try:
            client.ping()
            try:
                client.images.get(image)
            except ImageNotFound:
                return DockerSetupResult(
                    DockerSetupStatus.IMAGE_MISSING,
                    f"Docker image is not present locally: {image}",
                )
        finally:
            client.close()
    except (DockerException, OSError):
        return DockerSetupResult(
            DockerSetupStatus.DAEMON_UNAVAILABLE,
            "Docker daemon is not reachable from the current user",
        )
    return DockerSetupResult(DockerSetupStatus.AVAILABLE, f"Docker image is available: {image}")


def default_image_build_plan(image: str, *, source_root: Path | None = None) -> DockerBuildPlan:
    """Return the exact source-tree build action for ``docker/default``."""

    root = source_root or Path(__file__).resolve().parents[3]
    context = root / "docker" / "default"
    dockerfile = context / "Dockerfile"
    if not dockerfile.is_file():
        raise DockerSetupError(
            "The project default-image build context is unavailable; run setup from a source "
            "checkout or build/pull the selected image separately"
        )
    argv = ("docker", "build", "-t", image, str(context))
    return DockerBuildPlan(image=image, context=context, argv=argv)


def execute_docker_build(plan: DockerBuildPlan) -> None:
    """Run only an already-reviewed argv; never invoke a shell."""

    try:
        result = subprocess.run(
            list(plan.argv),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_BUILD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerSetupError("Docker default-image build failed or timed out") from exc
    if result.returncode != 0:
        raise DockerSetupError("Docker default-image build returned a failure")


__all__ = [
    "DockerBuildPlan",
    "DockerSetupError",
    "DockerSetupResult",
    "DockerSetupStatus",
    "default_image_build_plan",
    "execute_docker_build",
    "inspect_docker_image",
]
