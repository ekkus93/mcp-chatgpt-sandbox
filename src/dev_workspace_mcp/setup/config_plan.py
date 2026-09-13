"""Fail-closed reviewed configuration planning for interactive setup."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from dev_workspace_mcp.config import ConfigError, ServerConfig, validate_config_mapping
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    read_text_bounded,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.git_policy import policy_for_preset
from dev_workspace_mcp.setup.model import SetupState, SetupStep, SetupStepStatus

_MAX_CONFIG_BYTES = 1024 * 1024


class ConfigPlanError(RuntimeError):
    """Secret-safe failure while inspecting or preparing operator config."""


@dataclass(frozen=True, slots=True)
class ConfigInspection:
    exists: bool
    valid: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigUpdatePlan:
    """Exact reviewed candidate bound to source bytes and setup choices."""

    candidate_text: str = field(repr=False)
    source_exists: bool
    source_digest: str | None
    state_digest: str
    repaired_invalid_source: bool
    summary: tuple[str, ...]


def _reject_duplicate_keys(node: Node) -> None:
    if isinstance(node, MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            key = (key_node.tag, getattr(key_node, "value", ""))
            if key in seen:
                raise ValueError("configuration contains duplicate mapping keys")
            seen.add(key)
            _reject_duplicate_keys(key_node)
            _reject_duplicate_keys(value_node)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            _reject_duplicate_keys(child)


def _parse_mapping(raw: str) -> dict[str, Any]:
    try:
        node = yaml.compose(raw)
        if node is None:
            raise ValueError("configuration root must be a mapping")
        _reject_duplicate_keys(node)
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("configuration is not valid YAML") from exc
    if not isinstance(loaded, dict):
        raise ValueError("configuration root must be a mapping")
    if any(not isinstance(key, str) for key in loaded):
        raise ValueError("configuration root keys must be strings")
    return loaded


def _digest_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def state_digest(state: SetupState) -> str:
    encoded = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_existing(path: Path) -> tuple[bool, str]:
    expanded = path.expanduser()
    try:
        reject_unsafe_sensitive_target(expanded)
    except SetupFilesystemError as exc:
        raise ConfigPlanError(f"Refusing unsafe configuration target at {expanded}") from exc
    if not expanded.exists():
        return False, ""
    try:
        return True, read_text_bounded(expanded, max_bytes=_MAX_CONFIG_BYTES)
    except SetupFilesystemError as exc:
        raise ConfigPlanError(f"Could not safely read configuration at {expanded}") from exc


def _validate_mapping(mapping: dict[str, Any]) -> ServerConfig:
    try:
        return validate_config_mapping(mapping)
    except ConfigError as exc:
        raise ConfigPlanError(f"Configuration is rejected by production validation: {exc}") from exc


def inspect_config(path: Path) -> ConfigInspection:
    exists, raw = _read_existing(path)
    if not exists:
        return ConfigInspection(exists=False, valid=False)
    try:
        mapping = _parse_mapping(raw)
        _validate_mapping(mapping)
    except (ValueError, ConfigPlanError) as exc:
        return ConfigInspection(exists=True, valid=False, error=str(exc))
    return ConfigInspection(exists=True, valid=True)


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        result: dict[str, Any] = {}
        parent[key] = result
        return result
    if not isinstance(value, dict):
        raise ConfigPlanError(f"Existing configuration field {key!r} is not a mapping")
    return value


def _require_new_config_inputs(state: SetupState) -> None:
    missing = [
        label
        for label, value in (
            ("state_db_path", state.state_db_path),
            ("docker.default_image", state.docker_image),
            ("ssh.keys_dir", state.ssh_keys_dir),
            ("ssh.known_hosts", state.known_hosts_path),
            ("audit.path", state.audit_path),
        )
        if value is None
    ]
    if missing:
        raise ConfigPlanError("New configuration is missing required setup values: " + ", ".join(missing))


def _apply_state(candidate: dict[str, Any], state: SetupState, *, source_exists: bool) -> None:
    candidate["version"] = 1
    if not source_exists:
        _require_new_config_inputs(state)

    if state.state_db_path is not None:
        candidate["state_db_path"] = state.state_db_path
    if state.docker_image is not None:
        _mapping(candidate, "docker")["default_image"] = state.docker_image
    if state.ssh_keys_dir is not None:
        _mapping(candidate, "ssh")["keys_dir"] = state.ssh_keys_dir
    if state.known_hosts_path is not None:
        _mapping(candidate, "ssh")["known_hosts"] = state.known_hosts_path
    if state.audit_path is not None:
        _mapping(candidate, "audit")["path"] = state.audit_path

    if state.statuses.get(SetupStep.REPOSITORY_ACCESS) is SetupStepStatus.COMPLETE or not source_exists:
        candidate["repository_roots"] = list(state.repository_roots)
        candidate["repositories"] = [repository.to_dict() for repository in state.repositories]

    ssh = _mapping(candidate, "ssh")
    if state.ssh_mode is not None:
        ssh["mode"] = state.ssh_mode
    if state.ssh_scope is not None:
        ssh["scope"] = state.ssh_scope
    if state.shared_key is not None:
        ssh["shared_key"] = state.shared_key

    github = _mapping(candidate, "github")
    if state.github_api_auth is not None:
        github["api_auth"] = state.github_api_auth
    if state.github_api_token_path is not None:
        github["api_token_path"] = state.github_api_token_path

    if state.statuses.get(SetupStep.GIT_SAFETY_POLICY) is SetupStepStatus.COMPLETE:
        policy = policy_for_preset(state.git_policy_preset)
        candidate["git_policy"] = policy.model_dump(mode="json")


def _flatten(value: Any, *, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    result: dict[str, Any] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            result.update(_flatten(child, prefix=path))
        else:
            result[path] = child
    return result


def _display(value: Any) -> str:
    if value is None:
        return "<unset>"
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return str(value)


def _summary(before: dict[str, Any] | None, after: dict[str, Any], *, repaired: bool) -> tuple[str, ...]:
    lines = [
        "Existing configuration: invalid; explicit repair approved"
        if repaired
        else "Existing configuration: none; new file will be created"
        if before is None
        else "Existing configuration: valid; unmodified settings preserved"
    ]
    old = _flatten(before or {})
    new = _flatten(after)
    for path in sorted(set(old) | set(new)):
        if old.get(path) != new.get(path):
            lines.append(f"{path}: {_display(old.get(path))} -> {_display(new.get(path))}")
    return tuple(lines)


def prepare_config_update(
    path: Path,
    state: SetupState,
    *,
    repair_invalid: bool = False,
) -> ConfigUpdatePlan:
    """Prepare a production-valid candidate without mutating the host."""

    validated_state = SetupState.from_dict(state.to_dict())
    exists, raw = _read_existing(path)
    before: dict[str, Any] | None = None
    repaired = False
    if exists:
        try:
            parsed = _parse_mapping(raw)
            _validate_mapping(parsed)
        except (ValueError, ConfigPlanError) as exc:
            if not repair_invalid:
                raise ConfigPlanError(
                    "Existing configuration is invalid; explicitly approve repair before replacement"
                ) from exc
            candidate: dict[str, Any] = {}
            repaired = True
        else:
            before = deepcopy(parsed)
            candidate = deepcopy(parsed)
    else:
        candidate = {}

    _apply_state(candidate, validated_state, source_exists=exists and not repaired)
    _validate_mapping(candidate)
    try:
        candidate_text = yaml.safe_dump(candidate, sort_keys=False, default_flow_style=False)
        serialized = _parse_mapping(candidate_text)
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigPlanError("Could not safely serialize configuration candidate") from exc
    _validate_mapping(serialized)

    return ConfigUpdatePlan(
        candidate_text=candidate_text,
        source_exists=exists,
        source_digest=_digest_text(raw) if exists else None,
        state_digest=state_digest(validated_state),
        repaired_invalid_source=repaired,
        summary=_summary(before, candidate, repaired=repaired),
    )


__all__ = [
    "ConfigInspection",
    "ConfigPlanError",
    "ConfigUpdatePlan",
    "inspect_config",
    "prepare_config_update",
    "state_digest",
]
