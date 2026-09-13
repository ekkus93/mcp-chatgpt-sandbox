"""Explicit configuration ownership decisions for the setup wizard.

Every production configuration field must have a setup disposition.  Top-level
sections may be reviewable while individual nested keys remain preserved; the
nested manifests make that distinction explicit so a future production field
cannot silently become defaulted, omitted, or overwritten by setup.

``ServerConfig`` and its nested production models remain authoritative for
field types, defaults, cross-field safety rules, and production validity.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from dev_workspace_mcp.config import ServerConfig
from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    DockerConfig,
    ExecutionConfig,
    FilesystemConfig,
    GithubConfig,
    GitPolicyConfig,
    LoggingConfig,
    RepositoryConfig,
    ResourcesConfig,
    SSHConfig,
    WorkspaceConfig,
)


class SetupFieldDisposition(StrEnum):
    """How setup treats one production configuration field."""

    MANAGED = "managed"
    PRESERVED = "preserved"
    DERIVED = "derived"
    UNSUPPORTED = "unsupported"


WIZARD_GUIDED_TOP_LEVEL_FIELDS = frozenset(
    {
        "version",
        "state_db_path",
        "repository_roots",
        "repositories",
        "docker",
        "workspace",
        "container",
        "ssh",
        "github",
        "git_policy",
        "resources",
        "audit",
        "logging",
    }
)

WIZARD_ADVANCED_TOP_LEVEL_FIELDS = frozenset({"filesystem", "execution"})

WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS = (
    WIZARD_GUIDED_TOP_LEVEL_FIELDS | WIZARD_ADVANCED_TOP_LEVEL_FIELDS
)

TOP_LEVEL_FIELD_DISPOSITIONS: Final[dict[str, SetupFieldDisposition]] = {
    "version": SetupFieldDisposition.DERIVED,
    "state_db_path": SetupFieldDisposition.MANAGED,
    "repository_roots": SetupFieldDisposition.MANAGED,
    "repositories": SetupFieldDisposition.MANAGED,
    "docker": SetupFieldDisposition.MANAGED,
    "workspace": SetupFieldDisposition.MANAGED,
    "container": SetupFieldDisposition.MANAGED,
    "ssh": SetupFieldDisposition.MANAGED,
    "github": SetupFieldDisposition.MANAGED,
    "filesystem": SetupFieldDisposition.MANAGED,
    "execution": SetupFieldDisposition.MANAGED,
    "git_policy": SetupFieldDisposition.MANAGED,
    "resources": SetupFieldDisposition.MANAGED,
    "audit": SetupFieldDisposition.MANAGED,
    "logging": SetupFieldDisposition.MANAGED,
}

# These manifests describe what happens *inside* a reviewed top-level section.
# For example, the guided Docker step reviews container.user and
# container.git_dir_mode but deliberately preserves an existing container.sudo.
NESTED_FIELD_DISPOSITIONS: Final[dict[type[object], dict[str, SetupFieldDisposition]]] = {
    RepositoryConfig: {
        "id": SetupFieldDisposition.MANAGED,
        "path": SetupFieldDisposition.MANAGED,
        "github": SetupFieldDisposition.MANAGED,
        "ssh_key": SetupFieldDisposition.MANAGED,
    },
    DockerConfig: {
        "default_image": SetupFieldDisposition.MANAGED,
        "workspace_path": SetupFieldDisposition.MANAGED,
    },
    WorkspaceConfig: {
        "idle_stop_after_seconds": SetupFieldDisposition.MANAGED,
    },
    ContainerConfig: {
        "user": SetupFieldDisposition.MANAGED,
        "sudo": SetupFieldDisposition.PRESERVED,
        "git_dir_mode": SetupFieldDisposition.MANAGED,
    },
    SSHConfig: {
        "mode": SetupFieldDisposition.MANAGED,
        "scope": SetupFieldDisposition.MANAGED,
        "keys_dir": SetupFieldDisposition.MANAGED,
        "known_hosts": SetupFieldDisposition.MANAGED,
        "shared_key": SetupFieldDisposition.MANAGED,
    },
    GithubConfig: {
        "api_auth": SetupFieldDisposition.MANAGED,
        "api_token_path": SetupFieldDisposition.MANAGED,
        "api_base_url": SetupFieldDisposition.MANAGED,
        "repositories": SetupFieldDisposition.PRESERVED,
    },
    FilesystemConfig: {
        "max_read_bytes": SetupFieldDisposition.MANAGED,
        "max_write_bytes": SetupFieldDisposition.MANAGED,
        "max_patch_bytes": SetupFieldDisposition.MANAGED,
        "max_listing_entries": SetupFieldDisposition.MANAGED,
        "max_search_results": SetupFieldDisposition.MANAGED,
    },
    ExecutionConfig: {
        "sync_timeout_seconds": SetupFieldDisposition.MANAGED,
        "default_job_timeout_seconds": SetupFieldDisposition.MANAGED,
        "max_output_bytes": SetupFieldDisposition.MANAGED,
        "tail_keep_bytes": SetupFieldDisposition.MANAGED,
        "response_tail_lines": SetupFieldDisposition.MANAGED,
        "job_retention_days": SetupFieldDisposition.MANAGED,
        "job_log_max_total_bytes": SetupFieldDisposition.MANAGED,
        "default_output_response_bytes": SetupFieldDisposition.MANAGED,
    },
    GitPolicyConfig: {
        "push": SetupFieldDisposition.MANAGED,
        "default_branch_push": SetupFieldDisposition.MANAGED,
        "force_push": SetupFieldDisposition.MANAGED,
        "force_with_lease": SetupFieldDisposition.MANAGED,
        "delete_remote_branch": SetupFieldDisposition.MANAGED,
        "delete_remote_tag": SetupFieldDisposition.MANAGED,
        "default_branch_commit": SetupFieldDisposition.MANAGED,
        "amend_published": SetupFieldDisposition.MANAGED,
        "pull_strategy": SetupFieldDisposition.MANAGED,
        "require_remote_protection": SetupFieldDisposition.MANAGED,
        "remote_protection_checks": SetupFieldDisposition.MANAGED,
        "allowed_url_schemes": SetupFieldDisposition.MANAGED,
    },
    ResourcesConfig: {
        "cpus": SetupFieldDisposition.MANAGED,
        "memory": SetupFieldDisposition.MANAGED,
        "pids_limit": SetupFieldDisposition.MANAGED,
    },
    AuditConfig: {
        "enabled": SetupFieldDisposition.PRESERVED,
        "path": SetupFieldDisposition.MANAGED,
    },
    LoggingConfig: {
        "level": SetupFieldDisposition.MANAGED,
    },
}


def _model_field_names(model: type[object]) -> frozenset[str]:
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, dict):
        raise TypeError(f"{model!r} is not a supported Pydantic config model")
    return frozenset(fields)


def unclassified_server_config_fields() -> frozenset[str]:
    """Return newly-added production fields lacking an explicit setup decision."""

    return frozenset(ServerConfig.model_fields) - WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS


def ownership_drift() -> tuple[str, ...]:
    """Return deterministic ownership-manifest drift diagnostics."""

    problems: list[str] = []
    top_actual = _model_field_names(ServerConfig)
    top_declared = frozenset(TOP_LEVEL_FIELD_DISPOSITIONS)
    if top_actual != top_declared:
        problems.append(
            "ServerConfig: "
            f"missing={sorted(top_actual - top_declared)}, "
            f"stale={sorted(top_declared - top_actual)}"
        )
    for model, dispositions in NESTED_FIELD_DISPOSITIONS.items():
        actual = _model_field_names(model)
        declared = frozenset(dispositions)
        if actual != declared:
            problems.append(
                f"{model.__name__}: missing={sorted(actual - declared)}, "
                f"stale={sorted(declared - actual)}"
            )
    return tuple(problems)


def validate_ownership_manifests() -> None:
    """Fail when production config changed without an explicit setup decision."""

    problems = ownership_drift()
    if problems:
        raise RuntimeError("setup configuration ownership drift: " + "; ".join(problems))


def managed_fields_for_session(*, touched_fields: Iterable[str]) -> frozenset[str]:
    """Return reviewed top-level fields the config writer may modify in this session.

    A field being wizard-owned does not mean it is overwritten merely by
    opening the wizard. Existing production-valid values are preserved until
    the operator actually visits/changes the corresponding section and the
    review step includes that field.
    """

    touched = frozenset(touched_fields)
    unknown = touched - WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"setup session contains unclassified config fields: {names}")
    return touched
