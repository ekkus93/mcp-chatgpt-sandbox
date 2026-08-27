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

    # H30 (`docs/HARDENING_TODO.md`, HR-010) considered, and deliberately
    # did NOT add, a cross-field check rejecting `user: root` with
    # `sudo: true` (redundant — the container's own uid is already 0 — but
    # not dangerous, since `sudo` under an already-root process is simply
    # a no-op, not a privilege change): `sudo` defaults to `true`, and
    # multiple existing, real tests
    # (`tests/workspace/test_container_config.py`,
    # `tests/workspace/test_default_image.py`'s `test_at_016_...`)
    # construct `ContainerConfig(user="root", sudo=True)` or
    # `ContainerConfig(user="root")` (picking up the default) to exercise
    # the unrelated "root refused without user-namespace remapping"
    # behavior. Rejecting the combination at construction time would
    # break those before they ever reach the thing they're actually
    # testing, for a case that is annoying, not unsafe.


# --- §19 ssh -----------------------------------------------------------------


class SSHConfig(BaseModel):
    """`ssh:` block (§19)."""

    model_config = {"extra": "forbid"}

    mode: Literal["agent", "file"] = "agent"
    scope: Literal["repository", "shared"] = "repository"
    keys_dir: Path
    known_hosts: Path
    shared_key: str | None = None
    """Filename relative to `keys_dir`, used only under `scope: shared`
    (§19.1) — one key installed on every configured repository. Ignored
    under `scope: repository`, where each `repositories[].ssh_key` names
    its own key instead."""

    @field_validator("keys_dir", "known_hosts")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"ssh paths must be absolute: {v}")
        return v

    # H30 (`docs/HARDENING_TODO.md`, HR-010) considered, and deliberately
    # did NOT add, a cross-field check rejecting `scope: shared` with no
    # `shared_key` set: `resolve_ssh_key_path`'s own docstring documents
    # this as intentional ("`None` when nothing is configured for it ...
    # `scope: shared` with no `ssh.shared_key` set. Callers treat `None`
    # as 'nothing to provision,' not an error: not every repository need
    # be push-capable"), and
    # `test_resolve_ssh_key_path_shared_scope_none_when_unset`
    # (`tests/workspace/test_ssh_agent.py`) exercises exactly this shape
    # as a real, supported case. A hard `ConfigError` here would reject a
    # legitimate, already-tested configuration, not close a real gap.


# --- §19.5 / §19.6 github ------------------------------------------------------


class GithubConfig(BaseModel):
    """`github:` block (§19.5, §19.6).

    `repositories` is the remote-identity allowlist used by §20.2 validation,
    independent of (and compared against) each `repositories[].github`.
    """

    model_config = {"extra": "forbid"}

    api_auth: Literal["none", "token", "app"] = "none"
    api_token_path: Path | None = None
    api_base_url: str = "https://api.github.com"
    """§19.5/§20.4's remote-protection queries go here. Overridable for
    GitHub Enterprise Server deployments — and, incidentally, for pointing
    at a real local test server instead of the genuine GitHub API (S28's
    own tests do exactly that, rather than mocking the HTTP layer)."""
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

    # H30 (`docs/HARDENING_TODO.md`, HR-010) considered, and deliberately
    # did NOT add, a cross-field check rejecting `api_auth: token` with no
    # `api_token_path`: `github_api.py`'s own remote-protection query
    # already handles exactly this combination gracefully — falling back
    # to "unverified" (`_unverified(default_branch, "api_auth: token but
    # no api_token_path configured")`) rather than failing — which is the
    # same documented, intentional "nothing configured → degrade, don't
    # error" pattern `ssh.scope: shared` with no `shared_key` uses above.
    # A hard `ConfigError` here would reject a shape the runtime code was
    # deliberately written to tolerate.


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
    #: HD3/HR-029 (`docs/HARDENING_SPEC.md` §9, §15.5 amendment): `exec_output`'s
    #: own *response* default, distinct from `max_output_bytes`/`tail_keep_bytes`
    #: (what stays *captured on disk*, §15.6). The old default —
    #: `max_output_bytes + tail_keep_bytes`, 10.25 MiB — defeated §15.5's
    #: whole stated purpose of preventing multi-megabyte output from
    #: consuming MCP context. The full on-disk capture stays reachable, just
    #: only through an explicit `max_bytes`/ranged request, not by default.
    default_output_response_bytes: int = Field(default=64 * 1024, gt=0)


# --- §12.2 filesystem ------------------------------------------------------------


class FilesystemConfig(BaseModel):
    """`filesystem:` block (§12.2) — configurable limits for direct reads,
    direct writes, patch payloads, and directory/search listings."""

    model_config = {"extra": "forbid"}

    max_read_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_write_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_patch_bytes: int = Field(default=1 * 1024 * 1024, gt=0)
    max_listing_entries: int = Field(default=1000, gt=0)
    max_search_results: int = Field(default=500, gt=0)


# --- §18.9 git_policy ------------------------------------------------------------


def _default_remote_protection_checks() -> list[Literal["force_push", "deletion"]]:
    return ["force_push", "deletion"]


def _default_allowed_url_schemes() -> list[Literal["ssh", "https"]]:
    return ["ssh", "https"]


class GitPolicyConfig(BaseModel):
    """`git_policy:` block — structured tools only (§18.9). This is the
    authoritative field set; §52's example config may omit any of these.

    H30 (`docs/HARDENING_TODO.md`, HR-010): `validate_remote` was removed
    — declared, but never read anywhere. Remote-identity validation
    (`git/remote.py`) is unconditional, foundational security behavior
    (AT-009, HR-014) this daemon does not offer an opt-out from, so the
    fix is deletion, not wiring up a toggle that would let an operator
    turn off a core protection."""

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

    allowed_url_schemes: list[Literal["ssh", "https"]] = Field(
        default_factory=_default_allowed_url_schemes
    )


# --- §26 resource controls -----------------------------------------------------------


class ResourcesConfig(BaseModel):
    """`resources:` block (§26). Every field `None` (the default) means
    "no limit" — matching Docker's own default of unbounded, not an
    opinionated daemon-side ceiling; an operator opts in per §26's own
    example, distinguishing persistent workspaces (generous limits) from
    ephemeral validation ones (stricter) via separate config, not by this
    model imposing one for both."""

    model_config = {"extra": "forbid"}

    cpus: float | None = Field(default=None, gt=0)
    memory: str | None = None
    """Docker's own size-string syntax (`"16g"`, `"512m"`) — passed
    through to `docker.containers.run(mem_limit=...)` verbatim rather
    than parsed here; the Docker SDK/daemon already validates it, and
    duplicating that parsing would only be a second place for it to
    disagree with Docker's own."""
    pids_limit: int | None = Field(default=None, gt=0)


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
    state_db_path: Path
    """§68: the SQLite state database — workspace/job/audit records. No
    default, matching `audit.path`/`ssh.keys_dir`: where operational state
    is written is an operator decision, not a convention to assume."""
    repository_roots: list[Path] = Field(default_factory=list)
    repositories: list[RepositoryConfig] = Field(default_factory=list)
    docker: DockerConfig
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    container: ContainerConfig = Field(default_factory=ContainerConfig)
    ssh: SSHConfig
    github: GithubConfig = Field(default_factory=GithubConfig)
    filesystem: FilesystemConfig = Field(default_factory=FilesystemConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    git_policy: GitPolicyConfig = Field(default_factory=GitPolicyConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    audit: AuditConfig

    @field_validator("state_db_path")
    @classmethod
    def _state_db_path_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"state_db_path must be absolute: {v}")
        return v

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
