"""Project workspace configuration — `.mcp-workspace.yml` (§9, §10, §16.2)
— S30.

Optional, per-repository, committed to the repository itself (unlike
`docs/SPEC.md`'s daemon-side `config.yml`): lets a project declare its own
image, one-time setup steps, environment variables, and dependency
caches, rather than the operator hand-configuring each repository's
`docker.default_image`/`environment` at the daemon level.

`.devcontainer/devcontainer.json` support (§9's precedence item 2, §30)
is deliberately **not** implemented here — §30 itself says the server
"MAY initially support a subset," and this task's own scope is already
substantial without a second config format. `.mcp-workspace.yml`, when
present, is therefore the only source of image/setup/environment/cache
configuration this daemon currently reads from the repository itself;
`docker.default_image` remains the fallback (§9's precedence item 4)
whenever no `.mcp-workspace.yml` is present or it omits `image`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from dev_workspace_mcp.errors import ContainerCreateFailedError

#: §9's list — checked in this order; the first one present wins. Both
#: extensions are accepted since real YAML tooling accepts either.
_CANDIDATE_FILENAMES = (".mcp-workspace.yml", ".mcp-workspace.yaml")


class ProjectCacheEntry(BaseModel):
    """One `caches[]` entry (§11). `path` is authored `$HOME`-relative
    (`$HOME/.cargo` or `~/.cargo`, per §10's own example) — normalized by
    `workspace/caches.py`, not here, since this model only validates
    shape, not daemon-specific mount semantics."""

    model_config = {"extra": "forbid"}

    path: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")


class ProjectGithubConfig(BaseModel):
    model_config = {"extra": "forbid"}

    repository: str


class ProjectValidationConfig(BaseModel):
    """Parsed for forward-compatibility and unknown-field rejection —
    §10's example includes it, but no tool in this MVP runs these
    commands yet; there is no `validate` tool (§34.1 doesn't list one)."""

    model_config = {"extra": "forbid"}

    format: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)


class ProjectWorkspaceConfig(BaseModel):
    """The full `.mcp-workspace.yml` schema (§10). `extra: "forbid"`
    is what makes "unknown fields SHOULD cause a validation error" real
    rather than aspirational — matching every other config model in this
    codebase (`config/models.py`)."""

    model_config = {"extra": "forbid"}

    version: int
    image: str | None = None
    workspace: str | None = None
    """Parsed and validated, not yet wired to anything — `docker.
    workspace_path` (`config/models.py`) has the same "accepted, not yet
    acted on" status; both need `tools/workspace.py`'s hardcoded
    `DEFAULT_WORKSPACE_PATH` replaced with a real override path, a
    separate, pre-existing piece of work this task does not do."""
    setup: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    caches: list[ProjectCacheEntry] = Field(default_factory=list)
    github: ProjectGithubConfig | None = None
    validation: ProjectValidationConfig | None = None

    @field_validator("environment")
    @classmethod
    def _environment_cannot_shadow_daemon_variables(cls, v: dict[str, str]) -> dict[str, str]:
        """HR-006 (`docs/HARDENING_SPEC.md` §6): `environment:` is
        repository-supplied and, via `exec` plus a container-writable
        `.git/`, effectively client-authorable. `MCP_*` and
        `SSH_AUTH_SOCK` are the identity/credential variables
        `build_workspace_run_kwargs` computes for the container (§5.3) —
        a real regression let `MCP_UID: "0"` here run the container as
        root on a daemon with no user-namespace remapping, routing
        straight around the `container.user: root` refusal, which only
        ever inspects `container_config.user`. This is one of two
        independent controls (`container_config.py` also merges the
        daemon's own variables last); this one is deliberately the
        allowlist-reject side, so a bypass of one does not silently
        become a bypass of both."""
        shadowed = sorted(k for k in v if k == "SSH_AUTH_SOCK" or k.startswith("MCP_"))
        if shadowed:
            raise ValueError(
                f"environment may not set daemon-controlled variable(s) {shadowed} (§5.3/HR-006)"
            )
        return v


def _candidate_path(repo_path: Path) -> Path | None:
    for filename in _CANDIDATE_FILENAMES:
        candidate = repo_path / filename
        if candidate.is_file():
            return candidate
    return None


def load_project_config(repo_path: Path) -> ProjectWorkspaceConfig | None:
    """`None` when neither `.mcp-workspace.yml` nor `.mcp-workspace.yaml`
    exists — an ordinary, unconfigured repository, not an error. Raises
    `ContainerCreateFailedError` for a present-but-invalid file: workspace
    creation genuinely cannot proceed correctly against a project
    configuration the daemon cannot make sense of, so this fails closed
    the same way an invalid `container.user: root` configuration already
    does (§10: "Unknown mandatory fields SHOULD cause a validation
    error"), rather than silently falling back to defaults."""
    path = _candidate_path(repo_path)
    if path is None:
        return None

    try:
        raw: Any = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContainerCreateFailedError(
            f"{path.name} is not readable or not valid YAML: {exc}",
            details={"path": str(path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ContainerCreateFailedError(
            f"{path.name}: top level must be a mapping, got {type(raw).__name__}",
            details={"path": str(path)},
        )

    try:
        return ProjectWorkspaceConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, deliberately caught broadly
        raise ContainerCreateFailedError(
            f"{path.name}: invalid project configuration: {exc}",
            details={"path": str(path)},
        ) from exc
