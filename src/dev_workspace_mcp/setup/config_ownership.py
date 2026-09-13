"""Explicit ownership boundary between the setup wizard and production config.

This module is intentionally presentation-free.  It exists so new production
configuration fields cannot silently become omitted, defaulted, or reimplemented
by the setup wizard.  Every field receives one explicit setup disposition, and
contract tests compare these manifests with Pydantic's production ``model_fields``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    DockerConfig,
    ExecutionConfig,
    FilesystemConfig,
    GithubConfig,
    GitPolicyConfig,
    LoggingConfig,
    ResourcesConfig,
    ServerConfig,
    SSHConfig,
    WorkspaceConfig,
)


class ConfigDisposition(StrEnum):
    """How the setup wizard treats one production configuration field."""

    MANAGED = "managed"
    PRESERVED = "preserved"
    DERIVED = "derived"
    UNSUPPORTED = "unsupported"


CONFIG_FIELD_OWNERSHIP: Final[dict[str, ConfigDisposition]] = {
    "version": ConfigDisposition.DERIVED,
    "state_db_path": ConfigDisposition.MANAGED,
    "repository_roots": ConfigDisposition.MANAGED,
    "repositories": ConfigDisposition.MANAGED,
    "docker": ConfigDisposition.MANAGED,
    "workspace": ConfigDisposition.MANAGED,
    "container": ConfigDisposition.MANAGED,
    "ssh": ConfigDisposition.MANAGED,
    "github": ConfigDisposition.MANAGED,
    "filesystem": ConfigDisposition.MANAGED,
    "execution": ConfigDisposition.MANAGED,
    "git_policy": ConfigDisposition.MANAGED,
    "resources": ConfigDisposition.MANAGED,
    "audit": ConfigDisposition.MANAGED,
    "logging": ConfigDisposition.MANAGED,
}

# Safety-sensitive nested models are also exhaustive. A future production
# field must receive an explicit wizard disposition before this contract passes.
NESTED_CONFIG_FIELD_OWNERSHIP: Final[dict[type[object], dict[str, ConfigDisposition]]] = {
    DockerConfig: {
        "default_image": ConfigDisposition.MANAGED,
        "workspace_path": ConfigDisposition.MANAGED,
    },
    WorkspaceConfig: {
        "idle_stop_after_seconds": ConfigDisposition.MANAGED,
    },
    ContainerConfig: {
        "user": ConfigDisposition.MANAGED,
        "sudo": ConfigDisposition.MANAGED,
        "git_dir_mode": ConfigDisposition.MANAGED,
    },
    SSHConfig: {
        "mode": ConfigDisposition.MANAGED,
        "scope": ConfigDisposition.MANAGED,
        "keys_dir": ConfigDisposition.MANAGED,
        "known_hosts": ConfigDisposition.MANAGED,
        "shared_key": ConfigDisposition.MANAGED,
    },
    GithubConfig: {
        "api_auth": ConfigDisposition.MANAGED,
        "api_token_path": ConfigDisposition.MANAGED,
        "api_base_url": ConfigDisposition.MANAGED,
        "repositories": ConfigDisposition.MANAGED,
    },
    FilesystemConfig: {
        "max_read_bytes": ConfigDisposition.MANAGED,
        "max_write_bytes": ConfigDisposition.MANAGED,
        "max_patch_bytes": ConfigDisposition.MANAGED,
        "max_listing_entries": ConfigDisposition.MANAGED,
        "max_search_results": ConfigDisposition.MANAGED,
    },
    ExecutionConfig: {
        "sync_timeout_seconds": ConfigDisposition.MANAGED,
        "default_job_timeout_seconds": ConfigDisposition.MANAGED,
        "max_output_bytes": ConfigDisposition.MANAGED,
        "tail_keep_bytes": ConfigDisposition.MANAGED,
        "response_tail_lines": ConfigDisposition.MANAGED,
        "job_retention_days": ConfigDisposition.MANAGED,
        "job_log_max_total_bytes": ConfigDisposition.MANAGED,
        "default_output_response_bytes": ConfigDisposition.MANAGED,
    },
    GitPolicyConfig: {
        "push": ConfigDisposition.MANAGED,
        "default_branch_push": ConfigDisposition.MANAGED,
        "force_push": ConfigDisposition.MANAGED,
        "force_with_lease": ConfigDisposition.MANAGED,
        "delete_remote_branch": ConfigDisposition.MANAGED,
        "delete_remote_tag": ConfigDisposition.MANAGED,
        "default_branch_commit": ConfigDisposition.MANAGED,
        "amend_published": ConfigDisposition.MANAGED,
        "pull_strategy": ConfigDisposition.MANAGED,
        "require_remote_protection": ConfigDisposition.MANAGED,
        "remote_protection_checks": ConfigDisposition.MANAGED,
        "allowed_url_schemes": ConfigDisposition.MANAGED,
    },
    ResourcesConfig: {
        "cpus": ConfigDisposition.MANAGED,
        "memory": ConfigDisposition.MANAGED,
        "pids_limit": ConfigDisposition.MANAGED,
    },
    AuditConfig: {
        "enabled": ConfigDisposition.MANAGED,
        "path": ConfigDisposition.MANAGED,
    },
    LoggingConfig: {
        "level": ConfigDisposition.MANAGED,
    },
}


def _field_names(model: type[object]) -> set[str]:
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, dict):
        raise TypeError(f"{model!r} is not a supported Pydantic configuration model")
    return set(fields)


def _validate_manifest(model: type[object], manifest: dict[str, ConfigDisposition]) -> None:
    actual = _field_names(model)
    declared = set(manifest)
    if actual == declared:
        return
    missing = sorted(actual - declared)
    stale = sorted(declared - actual)
    raise RuntimeError(
        f"setup configuration ownership drift for {model.__name__}: "
        f"missing={missing}, stale={stale}"
    )


def validate_config_ownership() -> None:
    """Fail if production config changed without an explicit setup decision."""

    _validate_manifest(ServerConfig, CONFIG_FIELD_OWNERSHIP)
    for model, manifest in NESTED_CONFIG_FIELD_OWNERSHIP.items():
        _validate_manifest(model, manifest)
