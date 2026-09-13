"""Typed non-secret state and dependency semantics for interactive setup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self

from dev_workspace_mcp.config.models import RepositoryConfig


class SetupStep(StrEnum):
    """Stable persisted identifiers for the logical setup flow."""

    LOCAL_PREREQUISITES = "local_prerequisites"
    SERVER_STORAGE = "server_storage"
    DOCKER_WORKSPACE = "docker_workspace"
    REPOSITORY_ACCESS = "repository_access"
    SSH_AND_GITHUB = "ssh_and_github"
    GIT_SAFETY_POLICY = "git_safety_policy"
    RUNTIME_AND_RESOURCES = "runtime_and_resources"
    CHATGPT_CONNECTION = "chatgpt_connection"
    CONFIGURATION_REVIEW = "configuration_review"
    FINAL_VALIDATION = "final_validation"


class SetupStepStatus(StrEnum):
    NOT_STARTED = "not_started"
    ACTION_REQUIRED = "action_required"
    WAITING_FOR_USER = "waiting_for_user"
    VALIDATING = "validating"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED_OPTIONAL = "skipped_optional"


class GitPolicyPreset(StrEnum):
    RECOMMENDED = "recommended"
    CONSERVATIVE = "conservative"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class SetupRepository:
    """One fully validated, non-secret repository choice."""

    id: str
    path: str
    github: str
    ssh_key: str | None = None

    def __post_init__(self) -> None:
        validated = RepositoryConfig.model_validate(
            {"id": self.id, "path": self.path, "github": self.github, "ssh_key": self.ssh_key}
        )
        object.__setattr__(self, "id", validated.id)
        object.__setattr__(self, "path", str(validated.path))
        object.__setattr__(self, "github", validated.github)
        object.__setattr__(self, "ssh_key", validated.ssh_key)

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "path": self.path, "github": self.github, "ssh_key": self.ssh_key}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ValueError("setup repository must be a mapping")
        allowed = {"id", "path", "github", "ssh_key"}
        _reject_unknown_keys(value, allowed, context="repository")
        return cls(
            id=_required_str(value, "id", context="repository"),
            path=_required_str(value, "path", context="repository"),
            github=_required_str(value, "github", context="repository"),
            ssh_key=_optional_str(value, "ssh_key", context="repository"),
        )


_STATE_VERSION = 1
_SECRET_FRAGMENTS = ("token", "secret", "password", "private_key", "api_key", "credential")
_ALLOWED_STATE_KEYS = {
    "version",
    "current_step",
    "statuses",
    "config_path",
    "state_db_path",
    "audit_path",
    "ssh_keys_dir",
    "known_hosts_path",
    "docker_image",
    "repository_roots",
    "repositories",
    "ssh_mode",
    "ssh_scope",
    "shared_key",
    "github_api_auth",
    "github_api_token_path",
    "git_policy_preset",
    "tunnel_id",
    "chatgpt_enabled",
    "advanced",
}


def _contains_secret_fragment(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold())
    return any(fragment in normalized for fragment in _SECRET_FRAGMENTS)


def _reject_unknown_keys(value: dict[object, object], allowed: set[str], *, context: str) -> None:
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{context} keys must be strings")
        if key in allowed:
            continue
        if _contains_secret_fragment(key):
            raise ValueError(f"{context} contains forbidden secret-bearing field {key!r}")
        raise ValueError(f"{context} contains unknown field {key!r}")


def _required_str(value: dict[object, object], key: str, *, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return item


def _optional_str(value: dict[object, object], key: str, *, context: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{context}.{key} must be null or a non-empty string")
    return item


def _string_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"setup state {field_name} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"setup state {field_name} must contain non-empty strings")
        result.append(item)
    return tuple(result)


_STEP_DEPENDENTS: dict[SetupStep, frozenset[SetupStep]] = {
    SetupStep.LOCAL_PREREQUISITES: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.SERVER_STORAGE: frozenset(
        {SetupStep.CONFIGURATION_REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.DOCKER_WORKSPACE: frozenset(
        {SetupStep.CONFIGURATION_REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.REPOSITORY_ACCESS: frozenset(
        {
            SetupStep.SSH_AND_GITHUB,
            SetupStep.CONFIGURATION_REVIEW,
            SetupStep.FINAL_VALIDATION,
        }
    ),
    SetupStep.SSH_AND_GITHUB: frozenset(
        {SetupStep.CONFIGURATION_REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.GIT_SAFETY_POLICY: frozenset(
        {SetupStep.CONFIGURATION_REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.RUNTIME_AND_RESOURCES: frozenset(
        {SetupStep.CONFIGURATION_REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.CHATGPT_CONNECTION: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.CONFIGURATION_REVIEW: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.FINAL_VALIDATION: frozenset(),
}


@dataclass(slots=True)
class SetupState:
    """Versioned resumable setup state containing non-secret choices only."""

    version: int = _STATE_VERSION
    current_step: SetupStep = SetupStep.LOCAL_PREREQUISITES
    statuses: dict[SetupStep, SetupStepStatus] = field(default_factory=dict)
    config_path: str | None = None
    state_db_path: str | None = None
    audit_path: str | None = None
    ssh_keys_dir: str | None = None
    known_hosts_path: str | None = None
    docker_image: str | None = None
    repository_roots: tuple[str, ...] = ()
    repositories: tuple[SetupRepository, ...] = ()
    ssh_mode: str | None = None
    ssh_scope: str | None = None
    shared_key: str | None = None
    github_api_auth: str | None = None
    github_api_token_path: str | None = None
    git_policy_preset: GitPolicyPreset = GitPolicyPreset.RECOMMENDED
    tunnel_id: str | None = None
    chatgpt_enabled: bool = False
    advanced: bool = False

    def clone(self) -> SetupState:
        return SetupState.from_dict(self.to_dict())

    def invalidate_from(self, step: SetupStep) -> None:
        """Mark every dependent step action-required after an upstream edit."""

        pending = list(_STEP_DEPENDENTS[step])
        visited: set[SetupStep] = set()
        while pending:
            dependent = pending.pop()
            if dependent in visited:
                continue
            visited.add(dependent)
            if self.statuses.get(dependent) in {
                SetupStepStatus.COMPLETE,
                SetupStepStatus.SKIPPED_OPTIONAL,
                SetupStepStatus.VALIDATING,
                SetupStepStatus.FAILED,
            }:
                self.statuses[dependent] = SetupStepStatus.ACTION_REQUIRED
            pending.extend(_STEP_DEPENDENTS[dependent])

    def mark_changed(self, step: SetupStep) -> None:
        self.statuses[step] = SetupStepStatus.ACTION_REQUIRED
        self.invalidate_from(step)

    def rewind_for_missing_process_local_secret(self) -> bool:
        """Reacquire optional tunnel runtime material after process restart.

        Raw runtime credentials are intentionally not persisted. If ChatGPT
        connection setup had progressed past its step, resuming cannot truthfully
        claim that process-local material still exists.
        """

        if not self.chatgpt_enabled:
            return False
        status = self.statuses.get(SetupStep.CHATGPT_CONNECTION)
        order = list(SetupStep)
        if status is not SetupStepStatus.COMPLETE:
            return False
        if order.index(self.current_step) < order.index(SetupStep.CHATGPT_CONNECTION):
            return False
        self.current_step = SetupStep.CHATGPT_CONNECTION
        self.statuses[SetupStep.CHATGPT_CONNECTION] = SetupStepStatus.ACTION_REQUIRED
        self.invalidate_from(SetupStep.CHATGPT_CONNECTION)
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "current_step": self.current_step.value,
            "statuses": {step.value: status.value for step, status in self.statuses.items()},
            "config_path": self.config_path,
            "state_db_path": self.state_db_path,
            "audit_path": self.audit_path,
            "ssh_keys_dir": self.ssh_keys_dir,
            "known_hosts_path": self.known_hosts_path,
            "docker_image": self.docker_image,
            "repository_roots": list(self.repository_roots),
            "repositories": [repository.to_dict() for repository in self.repositories],
            "ssh_mode": self.ssh_mode,
            "ssh_scope": self.ssh_scope,
            "shared_key": self.shared_key,
            "github_api_auth": self.github_api_auth,
            "github_api_token_path": self.github_api_token_path,
            "git_policy_preset": self.git_policy_preset.value,
            "tunnel_id": self.tunnel_id,
            "chatgpt_enabled": self.chatgpt_enabled,
            "advanced": self.advanced,
        }

    @classmethod
    def from_dict(cls, value: object) -> SetupState:
        if not isinstance(value, dict):
            raise ValueError("setup state root must be a mapping")
        _reject_unknown_keys(value, _ALLOWED_STATE_KEYS, context="setup state")
        version = value.get("version")
        if version != _STATE_VERSION:
            raise ValueError(f"unsupported setup state version: {version!r}")
        raw_current_step = value.get("current_step")
        if not isinstance(raw_current_step, str):
            raise ValueError("setup state current_step is invalid")
        try:
            current_step = SetupStep(raw_current_step)
        except ValueError as exc:
            raise ValueError("setup state current_step is invalid") from exc

        raw_statuses = value.get("statuses", {})
        if not isinstance(raw_statuses, dict):
            raise ValueError("setup state statuses must be a mapping")
        statuses: dict[SetupStep, SetupStepStatus] = {}
        for raw_step, raw_status in raw_statuses.items():
            try:
                step = SetupStep(raw_step)
                status = SetupStepStatus(raw_status)
            except (TypeError, ValueError) as exc:
                raise ValueError("setup state contains invalid step/status") from exc
            statuses[step] = status

        raw_repositories = value.get("repositories", [])
        if not isinstance(raw_repositories, list):
            raise ValueError("setup state repositories must be a list")
        repositories = tuple(SetupRepository.from_dict(item) for item in raw_repositories)
        ids = [repository.id for repository in repositories]
        if len(ids) != len(set(ids)):
            raise ValueError("setup state repositories contain duplicate ids")
        paths = [Path(repository.path) for repository in repositories]
        if len(paths) != len(set(paths)):
            raise ValueError("setup state repositories contain duplicate paths")

        def optional(name: str) -> str | None:
            return _optional_str(value, name, context="setup state")

        chatgpt_enabled = value.get("chatgpt_enabled", False)
        advanced = value.get("advanced", False)
        if not isinstance(chatgpt_enabled, bool) or not isinstance(advanced, bool):
            raise ValueError("setup state boolean fields must be booleans")
        try:
            preset = GitPolicyPreset(value.get("git_policy_preset", GitPolicyPreset.RECOMMENDED))
        except (TypeError, ValueError) as exc:
            raise ValueError("setup state git_policy_preset is invalid") from exc

        return cls(
            version=_STATE_VERSION,
            current_step=current_step,
            statuses=statuses,
            config_path=optional("config_path"),
            state_db_path=optional("state_db_path"),
            audit_path=optional("audit_path"),
            ssh_keys_dir=optional("ssh_keys_dir"),
            known_hosts_path=optional("known_hosts_path"),
            docker_image=optional("docker_image"),
            repository_roots=_string_tuple(
                value.get("repository_roots", []), field_name="repository_roots"
            ),
            repositories=repositories,
            ssh_mode=optional("ssh_mode"),
            ssh_scope=optional("ssh_scope"),
            shared_key=optional("shared_key"),
            github_api_auth=optional("github_api_auth"),
            github_api_token_path=optional("github_api_token_path"),
            git_policy_preset=preset,
            tunnel_id=optional("tunnel_id"),
            chatgpt_enabled=chatgpt_enabled,
            advanced=advanced,
        )


def with_step_status(state: SetupState, step: SetupStep, status: SetupStepStatus) -> SetupState:
    """Return a revalidated clone carrying one status update."""

    candidate = state.clone()
    candidate.statuses[step] = status
    return SetupState.from_dict(candidate.to_dict())


__all__ = [
    "GitPolicyPreset",
    "SetupRepository",
    "SetupState",
    "SetupStep",
    "SetupStepStatus",
    "with_step_status",
]
