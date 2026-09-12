"""Versioned, typed, non-secret setup state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import ValidationError

from dev_workspace_mcp.config import RepositoryConfig
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    read_bounded_utf8,
)

SETUP_STATE_VERSION = 1
DEFAULT_SETUP_STATE_PATH = Path("~/.config/dev-workspace-mcp/setup-state.json")
_FORBIDDEN_STATE_KEY_FRAGMENTS = (
    "api_key",
    "token",
    "secret",
    "password",
    "private_key",
    "credential",
)


class SetupStateError(RuntimeError):
    """Secret-safe setup-state validation/persistence failure."""


class SetupStep(StrEnum):
    """Stable identifiers for the primary setup workflow."""

    PREREQUISITES = "prerequisites"
    STORAGE = "storage"
    DOCKER = "docker"
    REPOSITORIES = "repositories"
    CREDENTIALS = "credentials"
    GIT_POLICY = "git_policy"
    RUNTIME_LIMITS = "runtime_limits"
    CHATGPT = "chatgpt"
    REVIEW = "review"
    FINAL_VALIDATION = "final_validation"


class SetupStepStatus(StrEnum):
    """Persistable lifecycle state for one setup step."""

    NOT_STARTED = "not_started"
    ACTION_REQUIRED = "action_required"
    WAITING_FOR_USER = "waiting_for_user"
    VALIDATING = "validating"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED_OPTIONAL = "skipped_optional"


@dataclass(frozen=True, slots=True)
class SetupRepositoryState:
    """Non-secret repository registration choices safe to resume."""

    id: str
    path: str
    github: str
    ssh_key: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping):
            raise ValueError("setup repository must be a mapping")
        allowed = {"id", "path", "github", "ssh_key"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("setup repository contains unknown fields")
        raw_id = value.get("id")
        raw_path = value.get("path")
        raw_github = value.get("github")
        raw_ssh_key = value.get("ssh_key")
        if not isinstance(raw_id, str) or not isinstance(raw_path, str) or not isinstance(
            raw_github, str
        ):
            raise ValueError("setup repository id/path/github must be strings")
        if raw_ssh_key is not None and not isinstance(raw_ssh_key, str):
            raise ValueError("setup repository ssh_key must be a string or null")
        try:
            validated = RepositoryConfig.model_validate(
                {
                    "id": raw_id,
                    "path": raw_path,
                    "github": raw_github,
                    "ssh_key": raw_ssh_key,
                }
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
    {"version", "current_step", "statuses", "repositories", "advanced", "chatgpt_requested"}
)


def _reject_secret_state_keys(value: object, *, path: str = "state") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            folded = key.casefold()
            if any(fragment in folded for fragment in _FORBIDDEN_STATE_KEY_FRAGMENTS):
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
    github = [repository.github.casefold() for repository in repositories]
    if len(ids) != len(set(ids)):
        raise ValueError("setup state repositories contain duplicate ids")
    if len(paths) != len(set(paths)):
        raise ValueError("setup state repositories contain duplicate paths")
    if len(github) != len(set(github)):
        raise ValueError("setup state repositories contain duplicate GitHub identities")
    return repositories


@dataclass(slots=True)
class SetupState:
    """All resumable setup data; deliberately contains no secret values."""

    version: int = SETUP_STATE_VERSION
    current_step: SetupStep = SetupStep.PREREQUISITES
    statuses: dict[SetupStep, SetupStepStatus] = field(default_factory=dict)
    repositories: tuple[SetupRepositoryState, ...] = ()
    advanced: bool = False
    chatgpt_requested: bool = False

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "version": self.version,
            "current_step": self.current_step.value,
            "statuses": {step.value: status.value for step, status in self.statuses.items()},
            "repositories": [repository.to_dict() for repository in self.repositories],
            "advanced": self.advanced,
            "chatgpt_requested": self.chatgpt_requested,
        }
        # Status mapping keys are controlled SetupStep enum values.  Run the
        # generic secret-key guard over every other persisted structure.
        guard = dict(result)
        guard.pop("statuses")
        _reject_secret_state_keys(guard)
        return result

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping):
            raise ValueError("setup state must be a mapping")
        unknown = set(value) - _STATE_KEYS
        if unknown:
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
                step = SetupStep(raw_step)
                status = SetupStepStatus(raw_status)
            except ValueError as exc:
                raise ValueError("setup state contains an unknown step/status value") from exc
            statuses[step] = status

        advanced = value.get("advanced", False)
        chatgpt_requested = value.get("chatgpt_requested", False)
        if not isinstance(advanced, bool) or not isinstance(chatgpt_requested, bool):
            raise ValueError("setup state boolean choices must be booleans")

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
        )


def clone_setup_state(state: SetupState) -> SetupState:
    """Clone through serialization so every invariant is revalidated."""

    return SetupState.from_dict(state.to_dict())


def rewind_for_missing_process_local_material(
    state: SetupState,
    *,
    required_steps: set[SetupStep],
    available_steps: set[SetupStep],
) -> SetupState:
    """Rewind resume state before the earliest completed secret-bearing step.

    Callers decide which steps actually required process-local material for
    the chosen setup path; the state file itself never stores that material.
    """

    candidate = clone_setup_state(state)
    order = list(SetupStep)
    current_index = order.index(candidate.current_step)
    for step in order:
        if step not in required_steps or step in available_steps:
            continue
        step_index = order.index(step)
        if step_index <= current_index and candidate.statuses.get(step) is SetupStepStatus.COMPLETE:
            candidate.current_step = step
            candidate.statuses[step] = SetupStepStatus.ACTION_REQUIRED
            break
    return candidate


def save_setup_state(state: SetupState, path: Path = DEFAULT_SETUP_STATE_PATH) -> None:
    """Persist non-secret state atomically with owner-only permissions."""

    import json

    try:
        payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
        atomic_write_text(path, payload, mode=0o600, create_backup=False)
    except (OSError, ValueError, SetupFilesystemError) as exc:
        raise SetupStateError(f"Could not save setup state at {path.expanduser()}") from exc


def load_setup_state(path: Path = DEFAULT_SETUP_STATE_PATH) -> SetupState | None:
    """Load and validate persisted state; absent state returns ``None``."""

    import json

    expanded = path.expanduser()
    if not expanded.exists():
        return None
    try:
        raw = read_bounded_utf8(expanded)
        parsed = json.loads(raw)
        return SetupState.from_dict(parsed)
    except (json.JSONDecodeError, OSError, ValueError, SetupFilesystemError) as exc:
        raise SetupStateError(f"Could not load valid setup state at {expanded}") from exc
