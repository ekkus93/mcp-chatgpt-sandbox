"""Versioned, typed, non-secret setup state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import ValidationError

from mcp_chatgpt_sandbox.config import RepositoryConfig
from mcp_chatgpt_sandbox.setup.choices import SetupChoices
from mcp_chatgpt_sandbox.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    read_bounded_utf8,
)
from mcp_chatgpt_sandbox.setup.ownership import WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS

SETUP_STATE_VERSION = 1
DEFAULT_SETUP_STATE_PATH = Path("~/.config/mcp-chatgpt-sandbox/setup-state.json")
_FORBIDDEN_STATE_KEY_FRAGMENTS = (
    "api_key",
    "token",
    "secret",
    "password",
    "private_key",
    "credential",
    "runtime_key",
)


class SetupStateError(RuntimeError):
    """Secret-safe setup-state validation/persistence failure."""


class SetupStep(StrEnum):
    """Stable identifiers frozen by docs/SETUP_WIZARD_SPEC.md §8.1."""

    PREREQUISITES = "local_prerequisites"
    STORAGE = "server_storage"
    DOCKER = "docker_workspace"
    REPOSITORIES = "repository_access"
    CREDENTIALS = "ssh_and_github"
    GIT_POLICY = "git_safety_policy"
    RUNTIME_LIMITS = "runtime_and_resources"
    CHATGPT = "chatgpt_connection"
    REVIEW = "configuration_review"
    FINAL_VALIDATION = "final_validation"


class SetupStepStatus(StrEnum):
    NOT_STARTED = "not_started"
    ACTION_REQUIRED = "action_required"
    WAITING_FOR_USER = "waiting_for_user"
    VALIDATING = "validating"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED_OPTIONAL = "skipped_optional"


# Explicit rather than positional: changing one choice invalidates only evidence
# that semantically depends on it. The closure is computed transitively below.
_SETUP_STEP_DEPENDENCIES: dict[SetupStep, frozenset[SetupStep]] = {
    SetupStep.PREREQUISITES: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.STORAGE: frozenset(
        {SetupStep.CREDENTIALS, SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.DOCKER: frozenset({SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
    SetupStep.REPOSITORIES: frozenset(
        {SetupStep.CREDENTIALS, SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}
    ),
    SetupStep.CREDENTIALS: frozenset({SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
    SetupStep.GIT_POLICY: frozenset({SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
    SetupStep.RUNTIME_LIMITS: frozenset({SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}),
    SetupStep.CHATGPT: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.REVIEW: frozenset({SetupStep.FINAL_VALIDATION}),
    SetupStep.FINAL_VALIDATION: frozenset(),
}


@dataclass(frozen=True, slots=True)
class SetupRepositoryState:
    id: str
    path: str
    github: str
    ssh_key: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping):
            raise ValueError("setup repository must be a mapping")
        allowed = {"id", "path", "github", "ssh_key"}
        if set(value) - allowed:
            raise ValueError("setup repository contains unknown fields")
        raw_id = value.get("id")
        raw_path = value.get("path")
        raw_github = value.get("github")
        raw_ssh_key = value.get("ssh_key")
        if (
            not isinstance(raw_id, str)
            or not isinstance(raw_path, str)
            or not isinstance(raw_github, str)
        ):
            raise ValueError("setup repository id/path/github must be strings")
        if raw_ssh_key is not None and not isinstance(raw_ssh_key, str):
            raise ValueError("setup repository ssh_key must be a string or null")
        try:
            validated = RepositoryConfig.model_validate(
                {"id": raw_id, "path": raw_path, "github": raw_github, "ssh_key": raw_ssh_key}
            )
        except ValidationError as exc:
            raise ValueError("setup repository is rejected by production validation") from exc
        return cls(
            id=validated.id,
            path=str(validated.path),
            github=validated.github,
            ssh_key=validated.ssh_key,
        )

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "path": self.path, "github": self.github, "ssh_key": self.ssh_key}


_STATE_KEYS = frozenset(
    {
        "version",
        "current_step",
        "statuses",
        "repositories",
        "advanced",
        "chatgpt_requested",
        "config_path",
        "choices",
        "reviewed_fields",
    }
)


def _reject_secret_state_keys(value: object, *, path: str = "state") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            if any(fragment in key.casefold() for fragment in _FORBIDDEN_STATE_KEY_FRAGMENTS):
                raise ValueError(f"{path} contains a forbidden secret-bearing field name")
            _reject_secret_state_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _reject_secret_state_keys(child, path=f"{path}[{index}]")


def _validate_repositories(values: object) -> tuple[SetupRepositoryState, ...]:
    if not isinstance(values, list | tuple):
        raise ValueError("setup state repositories must be a list")
    repositories = tuple(SetupRepositoryState.from_dict(value) for value in values)
    ids = [repository.id for repository in repositories]
    paths = [str(Path(repository.path)) for repository in repositories]
    if len(ids) != len(set(ids)):
        raise ValueError("setup state repositories contain duplicate ids")
    if len(paths) != len(set(paths)):
        raise ValueError("setup state repositories contain duplicate paths")
    return repositories


def _validate_reviewed_fields(value: object) -> frozenset[str]:
    if not isinstance(value, list | tuple):
        raise ValueError("setup state reviewed_fields must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("setup state reviewed_fields must contain strings")
    fields = frozenset(value)
    if fields - WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS:
        raise ValueError("setup state reviewed_fields contain unsupported config fields")
    return fields


@dataclass(slots=True)
class SetupState:
    version: int = SETUP_STATE_VERSION
    current_step: SetupStep = SetupStep.PREREQUISITES
    statuses: dict[SetupStep, SetupStepStatus] = field(default_factory=dict)
    repositories: tuple[SetupRepositoryState, ...] = ()
    advanced: bool = False
    chatgpt_requested: bool = False
    config_path: str | None = None
    choices: SetupChoices = field(default_factory=SetupChoices)
    reviewed_fields: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "version": self.version,
            "current_step": self.current_step.value,
            "statuses": {step.value: status.value for step, status in self.statuses.items()},
            "repositories": [repository.to_dict() for repository in self.repositories],
            "advanced": self.advanced,
            "chatgpt_requested": self.chatgpt_requested,
            "config_path": self.config_path,
            "choices": self.choices.to_dict(),
            "reviewed_fields": sorted(self.reviewed_fields),
        }
        guard = dict(result)
        guard.pop("statuses")
        _reject_secret_state_keys(guard)
        return result

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping):
            raise ValueError("setup state must be a mapping")
        if set(value) - _STATE_KEYS:
            raise ValueError("setup state contains unknown fields")
        version = value.get("version")
        if version != SETUP_STATE_VERSION:
            raise ValueError(
                f"unsupported setup state version {version!r}; expected {SETUP_STATE_VERSION}"
            )
        current_raw = value.get("current_step", SetupStep.PREREQUISITES.value)
        if not isinstance(current_raw, str):
            raise ValueError("setup state current_step must be a string")
        try:
            current_step = SetupStep(current_raw)
        except ValueError as exc:
            raise ValueError("setup state contains an unknown current_step") from exc

        raw_statuses = value.get("statuses", {})
        if not isinstance(raw_statuses, Mapping):
            raise ValueError("setup state statuses must be a mapping")
        statuses: dict[SetupStep, SetupStepStatus] = {}
        for raw_step, raw_status in raw_statuses.items():
            if not isinstance(raw_step, str) or not isinstance(raw_status, str):
                raise ValueError("setup state statuses must map strings to strings")
            try:
                statuses[SetupStep(raw_step)] = SetupStepStatus(raw_status)
            except ValueError as exc:
                raise ValueError("setup state contains an unknown step/status value") from exc

        advanced = value.get("advanced", False)
        chatgpt_requested = value.get("chatgpt_requested", False)
        config_path = value.get("config_path")
        if not isinstance(advanced, bool) or not isinstance(chatgpt_requested, bool):
            raise ValueError("setup state boolean choices must be booleans")
        if config_path is not None and not isinstance(config_path, str):
            raise ValueError("setup state config_path must be a string or null")

        guard = dict(value)
        guard.pop("statuses", None)
        _reject_secret_state_keys(guard)
        return cls(
            version=SETUP_STATE_VERSION,
            current_step=current_step,
            statuses=statuses,
            repositories=_validate_repositories(value.get("repositories", [])),
            advanced=advanced,
            chatgpt_requested=chatgpt_requested,
            config_path=config_path,
            choices=SetupChoices.from_dict(value.get("choices", {})),
            reviewed_fields=_validate_reviewed_fields(value.get("reviewed_fields", [])),
        )


def clone_setup_state(state: SetupState) -> SetupState:
    return SetupState.from_dict(state.to_dict())


def dependent_steps(step: SetupStep) -> frozenset[SetupStep]:
    """Return the transitive setup evidence that depends on ``step``."""

    pending = list(_SETUP_STEP_DEPENDENCIES[step])
    dependents: set[SetupStep] = set()
    while pending:
        candidate = pending.pop()
        if candidate in dependents:
            continue
        dependents.add(candidate)
        pending.extend(_SETUP_STEP_DEPENDENCIES[candidate])
    return frozenset(dependents)


def invalidate_from_step(state: SetupState, changed_step: SetupStep) -> SetupState:
    """Invalidate only evidence that semantically depends on an edited step."""

    candidate = clone_setup_state(state)
    affected = dependent_steps(changed_step)
    candidate.statuses[changed_step] = SetupStepStatus.ACTION_REQUIRED
    for step in affected:
        candidate.statuses.pop(step, None)
    if candidate.current_step in affected:
        candidate.current_step = changed_step
    return candidate


def rewind_for_missing_process_local_material(
    state: SetupState,
    *,
    required_steps: set[SetupStep],
    available_steps: set[SetupStep],
) -> SetupState:
    candidate = clone_setup_state(state)
    order = list(SetupStep)
    current_index = order.index(candidate.current_step)
    for step in order:
        if step not in required_steps or step in available_steps:
            continue
        if (
            order.index(step) <= current_index
            and candidate.statuses.get(step) is SetupStepStatus.COMPLETE
        ):
            candidate = invalidate_from_step(candidate, step)
            candidate.current_step = step
            break
    return candidate


def save_setup_state(state: SetupState, path: Path = DEFAULT_SETUP_STATE_PATH) -> None:
    import json

    try:
        payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
        atomic_write_text(path, payload, mode=0o600, create_backup=False)
    except (OSError, ValueError, SetupFilesystemError) as exc:
        raise SetupStateError(f"Could not save setup state at {path.expanduser()}") from exc


def load_setup_state(path: Path = DEFAULT_SETUP_STATE_PATH) -> SetupState | None:
    import json

    expanded = path.expanduser()
    try:
        expanded.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupStateError(f"Could not inspect setup state at {expanded}") from exc
    try:
        raw = read_bounded_utf8(expanded)
        return SetupState.from_dict(json.loads(raw))
    except (json.JSONDecodeError, OSError, ValueError, SetupFilesystemError) as exc:
        raise SetupStateError(f"Could not load valid setup state at {expanded}") from exc
