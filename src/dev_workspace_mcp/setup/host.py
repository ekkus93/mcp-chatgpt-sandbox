"""Host prerequisite and server-storage validation for interactive setup."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dev_workspace_mcp.config import AuditConfig, DockerConfig, SSHConfig, ServerConfig, docker_available


class SetupHostError(RuntimeError):
    """Safe host/setup validation failure."""


@dataclass(frozen=True, slots=True)
class PrerequisiteReport:
    python_supported: bool
    uv_path: str | None
    docker_reachable: bool

    @property
    def fatal(self) -> bool:
        return not self.python_supported


@dataclass(frozen=True, slots=True)
class StorageChoices:
    state_db_path: Path
    audit_path: Path
    keys_dir: Path
    known_hosts: Path

    def validate(self) -> None:
        """Validate absolute-path semantics through production models."""
        try:
            ServerConfig(
                version=1,
                state_db_path=self.state_db_path,
                docker=DockerConfig(default_image="setup-validation-image"),
                ssh=SSHConfig(keys_dir=self.keys_dir, known_hosts=self.known_hosts),
                audit=AuditConfig(path=self.audit_path),
            )
        except ValidationError as exc:
            raise SetupHostError("Storage choices are rejected by production configuration") from exc

    def config_fragment(self) -> dict[str, object]:
        self.validate()
        return {
            "state_db_path": str(self.state_db_path),
            "ssh": {"keys_dir": str(self.keys_dir), "known_hosts": str(self.known_hosts)},
            "audit": {"path": str(self.audit_path)},
        }


def check_prerequisites() -> PrerequisiteReport:
    return PrerequisiteReport(
        python_supported=sys.version_info >= (3, 12),
        uv_path=shutil.which("uv"),
        docker_reachable=docker_available(),
    )


def nearest_existing_parent(path: Path) -> Path:
    candidate = path.expanduser().parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def parent_is_writable(path: Path) -> bool:
    parent = nearest_existing_parent(path)
    return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)


def _create_missing_parent(path: Path) -> None:
    parent = path.expanduser().parent
    if parent.exists():
        if not parent.is_dir():
            raise SetupHostError(f"Expected directory parent for {path.expanduser()}")
        return
    try:
        parent.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise SetupHostError(f"Could not create parent directory for {path.expanduser()}") from exc


def prepare_storage_directories(choices: StorageChoices, *, approved: bool) -> None:
    """Create only reviewed missing storage directories; never mutate implicitly."""
    choices.validate()
    if not approved:
        raise SetupHostError("Storage directory creation requires explicit approval")
    for target in (choices.state_db_path, choices.audit_path, choices.known_hosts):
        _create_missing_parent(target)
    if not choices.keys_dir.exists():
        try:
            choices.keys_dir.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise SetupHostError(f"Could not create SSH keys directory {choices.keys_dir}") from exc
    elif not choices.keys_dir.is_dir():
        raise SetupHostError(f"SSH keys path is not a directory: {choices.keys_dir}")
