"""Read-only repository verification and typed repository CRUD helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from mcp_chatgpt_sandbox.config import RepositoryConfig
from mcp_chatgpt_sandbox.git.remote import normalize_to_owner_repo
from mcp_chatgpt_sandbox.setup.model import SetupRepositoryState


class SetupRepositoryError(RuntimeError):
    """Safe repository-setup failure."""


@dataclass(frozen=True, slots=True)
class RepositoryVerification:
    repository: SetupRepositoryState
    is_git_repository: bool
    remote_url: str | None
    remote_identity: str | None
    matches_configured_identity: bool
    message: str

    @property
    def valid(self) -> bool:
        return self.is_git_repository and self.matches_configured_identity


def validate_repository_choice(repository: SetupRepositoryState) -> RepositoryConfig:
    try:
        return RepositoryConfig.model_validate(repository.to_dict())
    except ValidationError as exc:
        raise SetupRepositoryError(
            "Repository choices are rejected by production validation"
        ) from exc


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupRepositoryError("Could not inspect local Git repository") from exc


def verify_repository(repository: SetupRepositoryState) -> RepositoryVerification:
    validated = validate_repository_choice(repository)
    path = validated.path
    if not path.is_dir():
        return RepositoryVerification(
            repository, False, None, None, False, "Local repository path is not a directory."
        )
    inside = _git(path, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return RepositoryVerification(
            repository, False, None, None, False, "Local path is not a Git working tree."
        )
    remote = _git(path, "remote", "get-url", "origin")
    if remote.returncode != 0:
        return RepositoryVerification(
            repository, True, None, None, False, "Git repository has no readable origin remote."
        )
    remote_url = remote.stdout.strip()
    identity = normalize_to_owner_repo(remote_url)
    matches = identity == validated.github
    return RepositoryVerification(
        repository,
        True,
        remote_url,
        identity,
        matches,
        "Repository remote matches configured GitHub identity."
        if matches
        else "Repository origin does not match the configured GitHub owner/repo identity.",
    )


def _identity_path(repository: SetupRepositoryState) -> Path:
    return Path(repository.path).expanduser().resolve(strict=False)


def add_repository(
    repositories: tuple[SetupRepositoryState, ...], candidate: SetupRepositoryState
) -> tuple[SetupRepositoryState, ...]:
    validate_repository_choice(candidate)
    if any(existing.id == candidate.id for existing in repositories):
        raise SetupRepositoryError("Repository id is already configured")
    candidate_path = _identity_path(candidate)
    if any(_identity_path(existing) == candidate_path for existing in repositories):
        raise SetupRepositoryError("Repository path is already configured under another id")
    return (*repositories, candidate)


def edit_repository(
    repositories: tuple[SetupRepositoryState, ...],
    *,
    repository_id: str,
    replacement: SetupRepositoryState,
) -> tuple[SetupRepositoryState, ...]:
    remaining = tuple(repository for repository in repositories if repository.id != repository_id)
    if len(remaining) == len(repositories):
        raise SetupRepositoryError("Repository id to edit was not found")
    return add_repository(remaining, replacement)


def remove_repository(
    repositories: tuple[SetupRepositoryState, ...], *, repository_id: str, confirmed: bool
) -> tuple[SetupRepositoryState, ...]:
    if not confirmed:
        raise SetupRepositoryError("Repository removal requires explicit confirmation")
    remaining = tuple(repository for repository in repositories if repository.id != repository_id)
    if len(remaining) == len(repositories):
        raise SetupRepositoryError("Repository id to remove was not found")
    return remaining


def validate_repository_roots(roots: list[Path]) -> tuple[Path, ...]:
    for root in roots:
        if not root.is_absolute():
            raise SetupRepositoryError("Repository roots must be absolute paths")
        if not root.is_dir():
            raise SetupRepositoryError("Repository root does not exist or is not a directory")
    return tuple(roots)
