"""SSH and optional GitHub API setup validation without secret disclosure."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from dev_workspace_mcp.config import GithubConfig, SSHConfig
from dev_workspace_mcp.setup.model import SetupRepositoryState

REPOSITORY_SCOPE_HELP = (
    "Recommended: a dedicated key per repository limits credential reach "
    "if one workspace is compromised."
)
SHARED_SCOPE_WARNING = (
    "Shared SSH scope gives every configured repository the same credential and weakens isolation."
)
AGENT_MODE_HELP = "Recommended: agent mode keeps private key material outside workspace containers."
FILE_MODE_WARNING = (
    "File mode makes the selected private key readable by processes in the container."
)


class SetupCredentialError(RuntimeError):
    """Safe SSH/GitHub-setup failure."""


@dataclass(frozen=True, slots=True)
class CredentialChoices:
    mode: Literal["agent", "file"] = "agent"
    scope: Literal["repository", "shared"] = "repository"
    keys_dir: Path = Path("/")
    known_hosts: Path = Path("/")
    shared_key: str | None = None
    github_api_auth: Literal["none", "token", "app"] = "none"
    github_api_token_path: Path | None = None
    github_api_base_url: str = "https://api.github.com"

    def config_fragments(self) -> dict[str, object]:
        try:
            ssh = SSHConfig(
                mode=self.mode,
                scope=self.scope,
                keys_dir=self.keys_dir,
                known_hosts=self.known_hosts,
                shared_key=self.shared_key,
            )
            github = GithubConfig(
                api_auth=self.github_api_auth,
                api_token_path=self.github_api_token_path,
                api_base_url=self.github_api_base_url,
            )
        except ValidationError as exc:
            raise SetupCredentialError(
                "SSH/GitHub choices are rejected by production models"
            ) from exc
        return {
            "ssh": {
                "mode": ssh.mode,
                "scope": ssh.scope,
                "keys_dir": str(ssh.keys_dir),
                "known_hosts": str(ssh.known_hosts),
                "shared_key": ssh.shared_key,
            },
            "github": {
                "api_auth": github.api_auth,
                "api_token_path": (
                    str(github.api_token_path) if github.api_token_path is not None else None
                ),
                "api_base_url": github.api_base_url,
            },
        }


@dataclass(frozen=True, slots=True)
class CredentialCheck:
    valid: bool
    status: str
    message: str


def _regular_non_symlink(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def verify_credential_files(
    choices: CredentialChoices,
    repositories: tuple[SetupRepositoryState, ...],
) -> tuple[CredentialCheck, ...]:
    choices.config_fragments()
    checks: list[CredentialCheck] = []
    known_ok = _regular_non_symlink(choices.known_hosts)
    if known_ok:
        try:
            known_ok = choices.known_hosts.stat().st_size > 0
        except OSError:
            known_ok = False
    checks.append(
        CredentialCheck(
            known_ok,
            "pass" if known_ok else "action_required",
            "known_hosts file is present and non-empty"
            if known_ok
            else "known_hosts is missing or empty",
        )
    )

    if choices.scope == "shared":
        if choices.shared_key is None:
            checks.append(
                CredentialCheck(True, "pass", "shared SSH key intentionally not configured")
            )
        else:
            path = choices.keys_dir / choices.shared_key
            present = _regular_non_symlink(path)
            checks.append(
                CredentialCheck(
                    present,
                    "pass" if present else "action_required",
                    "shared SSH key file is present"
                    if present
                    else "shared SSH key file is missing",
                )
            )
    else:
        for repository in repositories:
            if repository.ssh_key is None:
                checks.append(
                    CredentialCheck(
                        True,
                        "pass",
                        (
                            f"{repository.id}: no SSH key configured; repository remains "
                            "non-push-capable"
                        ),
                    )
                )
                continue
            present = _regular_non_symlink(choices.keys_dir / repository.ssh_key)
            checks.append(
                CredentialCheck(
                    present,
                    "pass" if present else "action_required",
                    f"{repository.id}: SSH key file is present"
                    if present
                    else f"{repository.id}: referenced SSH key file is missing",
                )
            )

    if choices.github_api_auth == "app":
        checks.append(
            CredentialCheck(
                False,
                "unverified",
                (
                    "GitHub App API auth is accepted by config but not implemented by "
                    "runtime verification"
                ),
            )
        )
    elif choices.github_api_auth == "token":
        token_path = choices.github_api_token_path
        present = token_path is not None and _regular_non_symlink(token_path)
        checks.append(
            CredentialCheck(
                present,
                "pass" if present else "action_required",
                "GitHub API token file is present"
                if present
                else "GitHub API token file path is missing or unreadable",
            )
        )
    else:
        checks.append(CredentialCheck(True, "unverified", "GitHub API verification is disabled"))
    return tuple(checks)
