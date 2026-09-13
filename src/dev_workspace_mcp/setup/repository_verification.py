"""Read-only repository verification for the setup wizard."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.git.remote import normalize_to_owner_repo
from dev_workspace_mcp.setup.model import SetupRepository

_GIT_TIMEOUT_SECONDS = 5.0
_MAX_GIT_OUTPUT_CHARS = 16 * 1024


@dataclass(frozen=True, slots=True)
class RepositoryVerification:
    valid: bool
    message: str
    remote_url: str | None = None
    normalized_github: str | None = None


def _run_git(path: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    stdout = result.stdout[: _MAX_GIT_OUTPUT_CHARS + 1]
    if len(stdout) > _MAX_GIT_OUTPUT_CHARS:
        return 1, ""
    return result.returncode, stdout.strip()


def verify_repository(repository: SetupRepository) -> RepositoryVerification:
    """Verify local Git shape and configured GitHub identity without mutation/network I/O."""

    path = Path(repository.path)
    if not path.is_dir():
        return RepositoryVerification(False, "Repository path does not exist or is not a directory")

    code, inside = _run_git(path, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside != "true":
        return RepositoryVerification(False, "Path is not a Git working tree")

    # --local deliberately reads the repository's own configured URL without
    # network access or user/global insteadOf rewriting.
    code, remote_url = _run_git(path, "config", "--local", "--get", "remote.origin.url")
    if code != 0 or not remote_url:
        return RepositoryVerification(False, "Repository has no local origin URL")
    normalized = normalize_to_owner_repo(remote_url)
    if normalized is None:
        return RepositoryVerification(
            False,
            "Origin is not a recognized GitHub SSH/HTTPS repository URL",
            remote_url=remote_url,
        )
    if normalized.casefold() != repository.github.casefold():
        return RepositoryVerification(
            False,
            f"Origin resolves to {normalized!r}, not configured {repository.github!r}",
            remote_url=remote_url,
            normalized_github=normalized,
        )
    return RepositoryVerification(
        True,
        "Local Git repository and origin identity verified",
        remote_url=remote_url,
        normalized_github=normalized,
    )


__all__ = ["RepositoryVerification", "verify_repository"]
