"""Server configuration models — one pydantic model per §52 top-level block.

§18.9 is the authoritative definition of `git_policy`'s field set; §52's
example config is illustrative and may omit fields that have sensible
defaults. Fields here follow §18.9, not the trimmed example.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# --- shared small types -----------------------------------------------

#: "owner/repo" as used for repositories[].github and github.repositories (§7.3).
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[\w.-]+$")

#: force_push, force_with_lease, delete_remote_branch, delete_remote_tag,
#: amend_published all share this triad (§18.9).
RemoteWritePolicy = Literal["allow", "deny", "require_approval"]


def _validate_owner_repo(value: str) -> str:
    if not _OWNER_REPO_RE.match(value):
        raise ValueError(f"not a valid 'owner/repo' identifier: {value!r}")
    return value


class Duration:
    """A §8.1-style duration: `<n><s|m|h|d>` in seconds, or the literal
    `"never"` represented as `None`. Not a pydantic type on its own — parsed
    by `WorkspaceConfig.idle_stop_after`'s validator into `int | None` so the
    model stays a plain, serializable value.
    """

    _UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    @classmethod
    def parse(cls, value: str) -> int | None:
        if value == "never":
            return None
        m = re.fullmatch(r"(\d+)([smhd])", value)
        if not m:
            raise ValueError(f"invalid duration {value!r}: expected '<n>[smhd]' or 'never'")
        n, unit = m.groups()
        return int(n) * cls._UNIT_SECONDS[unit]


# --- §7 repositories -----------------------------------------------------


class RepositoryConfig(BaseModel):
    """One entry of `repositories:` (§7)."""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    path: Path
    github: str
    ssh_key: str | None = None
    """Filename relative to `ssh.keys_dir`; omitted under `ssh.scope: shared` (§19.1)."""

    @field_validator("path")
    @classmethod
    def _path_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"repositories[].path must be absolute: {v}")
        return v

    @field_validator("github")
    @classmethod
    def _github_owner_repo(cls, v: str) -> str:
        return _validate_owner_repo(v)


# --- §9 / §52 docker -------------------------------------------------------


class DockerConfig(BaseModel):
    """`docker:` block (§9, §52)."""

    model_config = {"extra": "forbid"}

    default_image: str = Field(min_length=1)
    workspace_path: Path = Path("/workspace")

    @field_validator("workspace_path")
    @classmethod
    def _workspace_path_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"docker.workspace_path must be absolute: {v}")
        return v


# --- §8.1 workspace ---------------------------------------------------------


class WorkspaceConfig(BaseModel):
    """`workspace:` block (§8.1)."""

    model_config = {"extra": "forbid"}

    idle_stop_after_seconds: int | None = Field(default=4 * 3600)
    """`None` means `idle_stop_after: never` — idle stop disabled."""

    @classmethod
    def from_yaml_value(cls, raw: dict[str, object]) -> WorkspaceConfig:
        """`idle_stop_after` is authored as a duration string (`"4h"`,
        `"never"`) but stored parsed, so the rest of the daemon works with
        seconds rather than re-parsing a string everywhere."""
        data = dict(raw)
        if "idle_stop_after" in data:
            value = data.pop("idle_stop_after")
            if not isinstance(value, str):
                raise ValueError("workspace.idle_stop_after must be a string")
            data["idle_stop_after_seconds"] = Duration.parse(value)
        return cls.model_validate(data)


# --- §5.3 / §18.2 container --------------------------------------------------


class ContainerConfig(BaseModel):
    """`container:` block (§5.3, §18.2)."""

    model_config = {"extra": "forbid"}

    user: Literal["host", "root"] = "host"
    sudo: bool = True
    git_dir_mode: Literal["read-write", "read-only", "none"] = "read-write"


# --- §19 ssh -----------------------------------------------------------------


class SSHConfig(BaseModel):
    """`ssh:` block (§19)."""

    model_config = {"extra": "forbid"}

    mode: Literal["agent", "file"] = "agent"
    scope: Literal["repository", "shared"] = "repository"
    keys_dir: Path
    known_hosts: Path

    @field_validator("keys_dir", "known_hosts")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"ssh paths must be absolute: {v}")
        return v


# --- §19.5 / §19.6 github ------------------------------------------------------


class GithubConfig(BaseModel):
    """`github:` block (§19.5, §19.6).

    `repositories` is the remote-identity allowlist used by §20.2 validation,
    independent of (and compared against) each `repositories[].github`.
    """

    model_config = {"extra": "forbid"}

    api_auth: Literal["none", "token", "app"] = "none"
    api_token_path: Path | None = None
    repositories: list[str] = Field(default_factory=list)

    @field_validator("api_token_path")
    @classmethod
    def _token_path_absolute(cls, v: Path | None) -> Path | None:
        if v is not None and not v.is_absolute():
            raise ValueError(f"github.api_token_path must be absolute: {v}")
        return v

    @field_validator("repositories")
    @classmethod
    def _repositories_owner_repo(cls, v: list[str]) -> list[str]:
        return [_validate_owner_repo(r) for r in v]


# --- §15.6 execution -----------------------------------------------------------


class ExecutionConfig(BaseModel):
    """`execution:` block (§15.5, §15.6)."""

    model_config = {"extra": "forbid"}

    sync_timeout_seconds: int = Field(default=30, gt=0)
    default_job_timeout_seconds: int = Field(default=3600, gt=0)
    max_output_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    tail_keep_bytes: int = Field(default=256 * 1024, gt=0)
    response_tail_lines: int = Field(default=200, gt=0)
    job_retention_days: int = Field(default=7, gt=0)
    job_log_max_total_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)


# --- §18.9 git_policy ------------------------------------------------------------


def _default_remote_protection_checks() -> list[Literal["force_push", "deletion"]]:
    return ["force_push", "deletion"]


def _default_allowed_url_schemes() -> list[Literal["ssh", "https"]]:
    return ["ssh", "https"]


class GitPolicyConfig(BaseModel):
    """`git_policy:` block — structured tools only (§18.9). This is the
    authoritative field set; §52's example config may omit any of these."""

    model_config = {"extra": "forbid"}

    push: Literal["allow", "deny"] = "allow"
    default_branch_push: Literal["allow", "allow_fast_forward", "require_approval", "deny"] = (
        "allow_fast_forward"
    )
    force_push: RemoteWritePolicy = "deny"
    force_with_lease: RemoteWritePolicy = "deny"
    delete_remote_branch: RemoteWritePolicy = "require_approval"
    delete_remote_tag: RemoteWritePolicy = "deny"

    default_branch_commit: Literal["allow", "deny"] = "allow"
    amend_published: RemoteWritePolicy = "deny"

    pull_strategy: Literal["ff_only", "merge", "rebase"] = "ff_only"

    require_remote_protection: Literal["off", "warn", "deny"] = "warn"
    remote_protection_checks: list[Literal["force_push", "deletion"]] = Field(
        default_factory=_default_remote_protection_checks
    )

    validate_remote: bool = True
    allowed_url_schemes: list[Literal["ssh", "https"]] = Field(
        default_factory=_default_allowed_url_schemes
    )


# --- §5.2 / §5.4 security ----------------------------------------------------------


class SecurityConfig(BaseModel):
    """`security:` block (§5.2, §5.4, §41)."""

    model_config = {"extra": "forbid"}

    allow_docker_socket: bool = False
    allow_privileged_containers: bool = False
    allow_additional_host_mounts: bool = False


# --- §24 audit -----------------------------------------------------------------------


class AuditConfig(BaseModel):
    """`audit:` block (§24)."""

    model_config = {"extra": "forbid"}

    enabled: bool = True
    path: Path

    @field_validator("path")
    @classmethod
    def _path_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"audit.path must be absolute: {v}")
        return v


# --- top level ------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """The full server configuration (§52)."""

    model_config = {"extra": "forbid"}

    version: Literal[1] = 1
    repository_roots: list[Path] = Field(default_factory=list)
    repositories: list[RepositoryConfig] = Field(default_factory=list)
    docker: DockerConfig
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    container: ContainerConfig = Field(default_factory=ContainerConfig)
    ssh: SSHConfig
    github: GithubConfig = Field(default_factory=GithubConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    git_policy: GitPolicyConfig = Field(default_factory=GitPolicyConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    audit: AuditConfig

    @field_validator("repository_roots")
    @classmethod
    def _roots_absolute(cls, v: list[Path]) -> list[Path]:
        for root in v:
            if not root.is_absolute():
                raise ValueError(f"repository_roots entries must be absolute: {root}")
        return v

    @field_validator("repositories")
    @classmethod
    def _repository_ids_unique(cls, v: list[RepositoryConfig]) -> list[RepositoryConfig]:
        seen = set()
        for repo in v:
            if repo.id in seen:
                raise ValueError(f"duplicate repositories[].id: {repo.id!r}")
            seen.add(repo.id)
        return v
