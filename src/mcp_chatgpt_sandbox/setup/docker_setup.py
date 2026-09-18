"""Docker workspace setup choices and reviewed image build planning."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound
from pydantic import ValidationError

from mcp_chatgpt_sandbox.config import ContainerConfig, DockerConfig, WorkspaceConfig


class SetupDockerError(RuntimeError):
    """Safe Docker-setup validation failure."""


@dataclass(frozen=True, slots=True)
class DockerBuildPlan:
    """Exact non-secret Docker build action reviewed before mutation."""

    image: str
    build_context: Path
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DockerSetupChoices:
    default_image: str = "mcp-chatgpt-sandbox-ubuntu:24.04"
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


def validate_default_image_build_context(build_context: Path) -> Path:
    """Return one normalized usable build context or raise a safe setup error."""

    resolved = build_context.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise SetupDockerError(
            "Docker build context is unavailable; build or pull the image manually."
        )
    return resolved


#: The distribution name a checkout's `pyproject.toml` must declare for its
#: `docker/default/` to be trusted as this project's build context (PSCR2-001).
_PROJECT_NAME = "mcp-chatgpt-sandbox"

_NAME_SEPARATOR_RUN = re.compile(r"[-_.]+")


def _normalized_project_name(name: object) -> str | None:
    """PEP 503-normalise a `[project] name`, or None if it is not a string.

    PSCR3-009 (`docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`). Packaging treats
    `mcp-chatgpt-sandbox`, `mcp_chatgpt_sandbox`, and `MCP-ChatGPT-Sandbox` as one
    project name, and `importlib.metadata` resolves any of them. The checkout
    search compared the raw string, so restyling the name in `pyproject.toml`
    would have made it stop finding *its own* checkout and report that the build
    context "ships only in a source checkout" from inside one — a symptom
    pointing away from its cause. Normalising accepts every spelling of this
    project and still no other project.
    """

    if not isinstance(name, str):
        return None
    return _NAME_SEPARATOR_RUN.sub("-", name).lower()


def _declared_project(pyproject: Path) -> dict[str, Any] | None:
    """Return a `pyproject.toml`'s `[project]` table, or None if unusable.

    An unreadable or malformed file disqualifies that one candidate rather than
    aborting context resolution: it is not evidence that the search has found
    this project, and an unrelated parse error surfacing from "where is the
    build context?" would be both confusing and unactionable.
    """

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    return project if isinstance(project, dict) else None


def _source_checkout_root() -> Path | None:
    """Locate the source checkout that ships the default Docker build context.

    PSCR-001 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`) records the
    policy this implements: the bundled context is a **source-checkout asset,
    not a packaged one**. `docker/default/` lives outside the `src/` module root
    that `uv_build` packages, so no wheel carries it — verified by building one
    and inspecting it (120 entries, no `Dockerfile`).

    This replaces `Path(__file__).resolve().parents[3]`, which was correct only
    for this repository's `src/` layout and silently resolved to
    `<venv>/lib/pythonX.Y/docker/default` for an installed distribution. That
    was not merely wrong, it was undetectable: the development install is
    editable, so `__file__` always points into `src/` and both CI and the local
    suite always took the checkout branch. Searching upward for a directory that
    actually contains both markers is self-validating — it cannot report a root
    that does not hold the context — so the installed case fails loudly at the
    one place that resolves it instead of being discovered by an operator.

    PSCR2-001 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`) made the search
    project-specific. The two layout markers alone prove a root *holds a build
    context*; they do not prove it is *this project's*. Any ancestor with some
    `pyproject.toml` and some `docker/default/` satisfied the original check, so
    an installed distribution sitting beneath an unrelated project with that
    layout would have built an image from the wrong context. A candidate is now
    accepted only when its `[project] name` is this distribution's (compared
    PEP 503-normalised since PSCR3-009), and a
    right-layout, wrong-name candidate is skipped so the search keeps climbing.

    A non-editable install into an in-repo virtualenv (`<repo>/.venv/...`) still
    finds `<repo>`, because the checkout genuinely is present. That does *not*
    guarantee the context matches the code that is running — an older wheel in
    a newer checkout would build from a Dockerfile it was never written against.
    `bundled_default_image_build_context()` closes that gap (PSCR2-002).
    """

    for candidate in Path(__file__).resolve().parents:
        pyproject = candidate / "pyproject.toml"
        if not (pyproject.is_file() and (candidate / "docker" / "default").is_dir()):
            continue
        project = _declared_project(pyproject)
        if project is not None and _normalized_project_name(project.get("name")) == _PROJECT_NAME:
            return candidate
    return None


def _installed_version() -> str | None:
    """Version of the installed `mcp-chatgpt-sandbox` distribution, if any."""

    try:
        return importlib.metadata.version(_PROJECT_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def _running_from(root: Path) -> bool:
    """Whether this module is the source tree under `root`, not an installed copy."""

    return Path(__file__).resolve().is_relative_to((root / "src").resolve())


def _check_context_matches_installed_version(root: Path) -> None:
    """Fail closed when an installed package would build from another version's context.

    PSCR2-002 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`), Option B.

    When this module *is* the checkout's own `src/` tree — the editable install
    used for development — code and context come from one tree by construction,
    so there is nothing to compare and the check is skipped. The spec originally
    proposed comparing `mcp_chatgpt_sandbox.__version__` instead, on the belief
    that an editable install matches by construction. It does not:
    `__version__` is a hard-coded literal maintained separately from
    `[project] version`, so a half-done version bump would have failed the
    Docker step of an editable install over bookkeeping unrelated to Docker.
    Asking "is the running code in the same tree?" is the property that
    actually holds by construction.

    Otherwise the installed distribution's own metadata — the wheel actually
    running — is compared with the checkout's `pyproject.toml`. A mismatch, or a
    version that cannot be determined, fails closed. A same-version checkout
    with uncommitted `Dockerfile` edits passes deliberately: that is ordinary
    Dockerfile development, not skew.
    """

    if _running_from(root):
        return
    project = _declared_project(root / "pyproject.toml")
    checkout_version = None if project is None else project.get("version")
    installed = _installed_version()
    if installed is None or checkout_version is None or installed != checkout_version:
        raise SetupDockerError(
            "The installed mcp-chatgpt-sandbox package "
            f"({installed or 'unknown version'}) does not match the source checkout "
            f"it would build the workspace image from ({checkout_version or 'unknown version'}). "
            "Reinstall the package from this checkout, or build the image manually "
            "and select it."
        )


def bundled_default_image_build_context() -> Path:
    """Resolve the source-checkout default Docker build context.

    The setup wizard may be launched from any current working directory, so this
    never consults `Path.cwd()`. Per PSCR-001 the context is checkout-only; an
    installed distribution raises an actionable error rather than returning a
    path that does not exist.
    """

    root = _source_checkout_root()
    if root is None:
        raise SetupDockerError(
            "The bundled Docker build context ships only in a source checkout. "
            "Build the workspace image manually with "
            "'docker build -t <image> docker/default/' from a checkout, or select "
            "an image that already exists on this host."
        )
    _check_context_matches_installed_version(root)
    return validate_default_image_build_context(root / "docker" / "default")


def default_image_build_command(
    image: str, *, build_context: Path | None = None
) -> tuple[str, ...]:
    context = (
        bundled_default_image_build_context()
        if build_context is None
        else validate_default_image_build_context(build_context)
    )
    return ("docker", "build", "-t", image, str(context))


def prepare_default_image_build_plan(
    *, image: str, build_context: Path | None = None
) -> DockerBuildPlan:
    """Build the exact Docker build command without mutating Docker state."""

    try:
        docker_config = DockerConfig.model_validate({"default_image": image.strip()})
    except ValidationError as exc:
        raise SetupDockerError("Docker image name is rejected by production models") from exc
    context = (
        bundled_default_image_build_context()
        if build_context is None
        else validate_default_image_build_context(build_context)
    )
    return DockerBuildPlan(
        image=docker_config.default_image,
        build_context=context,
        argv=("docker", "build", "-t", docker_config.default_image, str(context)),
    )


def build_default_image(
    client: docker.DockerClient,
    *,
    image: str,
    approved: bool,
    build_context: Path | None = None,
) -> None:
    """Build the bundled default image after explicit approval."""

    if not approved:
        raise SetupDockerError("Docker image build requires explicit approval")
    build_path = (
        bundled_default_image_build_context()
        if build_context is None
        else validate_default_image_build_context(build_context)
    )
    try:
        client.images.build(path=str(build_path), tag=image, rm=True)
    except (DockerException, OSError) as exc:
        raise SetupDockerError("Default workspace image build failed") from exc
