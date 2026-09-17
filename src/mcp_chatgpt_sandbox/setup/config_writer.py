"""Reviewed, CAS-bound configuration planning and atomic commit support."""

from __future__ import annotations

import errno
import hashlib
import importlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from mcp_chatgpt_sandbox.config import ConfigError, ServerConfig, load_config, parse_config_mapping
from mcp_chatgpt_sandbox.setup.filesystem import (
    SetupFilesystemError,
    atomic_write_text,
    ensure_private_directory,
    read_bounded_utf8,
    reject_unsafe_sensitive_target,
)
from mcp_chatgpt_sandbox.setup.ownership import managed_fields_for_session


class SetupConfigError(RuntimeError):
    """Secret-safe failure while planning or committing setup configuration."""


@dataclass(frozen=True, slots=True)
class ConfigInspection:
    """Non-secret state of the current target file."""

    exists: bool
    valid: bool


@dataclass(frozen=True, slots=True)
class ConfigUpdatePlan:
    """Exact reviewed candidate bound to the source bytes that produced it."""

    candidate_text: str = field(repr=False)
    source_exists: bool
    source_digest: str | None
    repaired_invalid_source: bool
    managed_fields: frozenset[str]
    summary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfigCommitResult:
    """Successful production-reloaded setup configuration update."""

    path: Path
    backup_path: Path | None
    config: ServerConfig


def _reject_duplicate_mapping_keys(node: Node) -> None:
    if isinstance(node, MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode):
                key = (key_node.tag, key_node.value)
                if key in seen:
                    raise ValueError("configuration contains duplicate mapping keys")
                seen.add(key)
            _reject_duplicate_mapping_keys(key_node)
            _reject_duplicate_mapping_keys(value_node)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            _reject_duplicate_mapping_keys(child)


def _parse_mapping(raw: str) -> dict[str, Any]:
    try:
        node = yaml.compose(raw)
        if node is None:
            raise ValueError("configuration root must be a mapping")
        _reject_duplicate_mapping_keys(node)
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("configuration is not valid YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("configuration root must be a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError("configuration root keys must be strings")
    return data


def _read_existing(path: Path) -> tuple[bool, str]:
    expanded = path.expanduser()
    try:
        reject_unsafe_sensitive_target(expanded)
        expanded.lstat()
    except FileNotFoundError:
        return False, ""
    except SetupFilesystemError as exc:
        raise SetupConfigError(f"Refusing unsafe setup config target at {expanded}") from exc
    except OSError as exc:
        raise SetupConfigError(f"Could not inspect setup config at {expanded}") from exc
    try:
        return True, read_bounded_utf8(expanded)
    except SetupFilesystemError as exc:
        raise SetupConfigError(f"Could not read setup config at {expanded}") from exc


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_mapping(data: dict[str, Any]) -> ServerConfig:
    try:
        return parse_config_mapping(data)
    except ConfigError as exc:
        raise SetupConfigError("Configuration is rejected by production validation") from exc


def inspect_config(path: Path) -> ConfigInspection:
    """Safely distinguish absent, valid, and invalid existing configuration."""

    exists, raw = _read_existing(path)
    if not exists:
        return ConfigInspection(exists=False, valid=False)
    try:
        data = _parse_mapping(raw)
        _validate_mapping(data)
    except (ValueError, SetupConfigError):
        return ConfigInspection(exists=True, valid=False)
    return ConfigInspection(exists=True, valid=True)


def _display(value: object) -> str:
    if value is None:
        return "<unset>"
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    if isinstance(value, list):
        return f"[{len(value)} item(s)]"
    if isinstance(value, dict):
        return "<configured>"
    return str(value)


def _repository_index(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def _summary(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    managed_fields: frozenset[str],
    repaired: bool,
) -> tuple[str, ...]:
    lines: list[str] = []
    if before is None:
        lines.append(
            "Existing config: invalid; explicit repair approved and original will be backed up"
            if repaired
            else "Existing config: none; a new configuration will be created"
        )
    else:
        lines.append("Existing config: valid; untouched settings will be preserved")

    for field_name in sorted(managed_fields):
        old = before.get(field_name) if before is not None else None
        new = after.get(field_name)
        if old != new:
            lines.append(f"{field_name}: {_display(old)} -> {_display(new)}")

    if "repositories" in managed_fields:
        old_repos = _repository_index(before.get("repositories") if before is not None else None)
        new_repos = _repository_index(after.get("repositories"))
        for repository_id in sorted(new_repos.keys() - old_repos.keys()):
            lines.append(f"repository + {repository_id}")
        for repository_id in sorted(old_repos.keys() - new_repos.keys()):
            lines.append(f"repository - {repository_id}")
        for repository_id in sorted(old_repos.keys() & new_repos.keys()):
            if old_repos[repository_id] != new_repos[repository_id]:
                lines.append(f"repository ~ {repository_id}")

    for section in ("ssh", "git_policy"):
        if section not in managed_fields:
            continue
        old_section = before.get(section) if before is not None else None
        new_section = after.get(section)
        old_mapping = old_section if isinstance(old_section, dict) else {}
        new_mapping = new_section if isinstance(new_section, dict) else {}
        for key in sorted(set(old_mapping) | set(new_mapping)):
            if old_mapping.get(key) != new_mapping.get(key):
                lines.append(
                    f"{section}.{key}: {_display(old_mapping.get(key))} -> "
                    f"{_display(new_mapping.get(key))}"
                )
    return tuple(lines)


def _merge_reviewed_value(existing: object, reviewed: object) -> object:
    """Recursively preserve mapping keys the wizard did not review.

    Lists and scalars are replaced as units. Mapping fragments are merged so a
    guided edit of (for example) ``github.api_auth`` does not erase a valid
    existing ``github.repositories`` allowlist. Explicit ``None`` remains a
    real reviewed value and therefore clears the corresponding key.
    """

    if isinstance(existing, dict) and isinstance(reviewed, dict):
        merged = deepcopy(existing)
        for key, value in reviewed.items():
            merged[key] = _merge_reviewed_value(merged.get(key), value)
        return merged
    return deepcopy(reviewed)


def prepare_config_update(
    path: Path,
    *,
    wizard_values: dict[str, object],
    managed_fields: set[str] | frozenset[str],
    repair_invalid: bool = False,
) -> ConfigUpdatePlan:
    """Build and production-validate the exact candidate an operator will review."""

    managed = managed_fields_for_session(touched_fields=managed_fields)
    missing_values = managed - wizard_values.keys()
    if missing_values:
        raise SetupConfigError("Managed setup fields are missing reviewed values")

    exists, raw = _read_existing(path)
    before: dict[str, Any] | None = None
    repaired = False
    if exists:
        try:
            parsed = _parse_mapping(raw)
            _validate_mapping(parsed)
        except (ValueError, SetupConfigError) as exc:
            if not repair_invalid:
                raise SetupConfigError(
                    "Existing configuration is invalid; explicit repair approval is required"
                ) from exc
            candidate: dict[str, Any] = {}
            repaired = True
        else:
            before = deepcopy(parsed)
            candidate = deepcopy(parsed)
    else:
        candidate = {}

    for field_name in managed:
        candidate[field_name] = _merge_reviewed_value(
            candidate.get(field_name), wizard_values[field_name]
        )

    _validate_mapping(candidate)
    try:
        candidate_text = yaml.safe_dump(candidate, sort_keys=False, default_flow_style=False)
        round_tripped = _parse_mapping(candidate_text)
    except (yaml.YAMLError, ValueError) as exc:
        raise SetupConfigError("Could not safely serialize reviewed configuration") from exc
    _validate_mapping(round_tripped)

    return ConfigUpdatePlan(
        candidate_text=candidate_text,
        source_exists=exists,
        source_digest=_digest(raw) if exists else None,
        repaired_invalid_source=repaired,
        managed_fields=managed,
        summary=_summary(before, candidate, managed_fields=managed, repaired=repaired),
    )


def _commit_lock_path(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded.with_name(f".{expanded.name}.setup.lock")


def _fcntl_module() -> Any:
    try:
        return importlib.import_module("fcntl")
    except ImportError as exc:
        raise SetupConfigError(
            "Safe serialized configuration replacement is unavailable on this platform"
        ) from exc


@contextmanager
def _config_commit_lock(path: Path) -> Iterator[Path]:
    """Serialize setup config commits with a non-blocking kernel advisory lock.

    Lock identity is the persistent empty sibling ``.<config>.setup.lock``.
    The file is never unlinked: unlink-on-release can create two independently
    lockable inodes during contention. Acquisition is one non-blocking attempt,
    so contention is bounded without retry/sleep behavior. Kernel lock ownership
    is tied to the descriptor and is released automatically on process exit, so
    crashes do not leave stale lock ownership. Existing config bytes are
    revalidated only after this lock is held; backup, replacement, byte
    verification, and production reload remain inside the same critical section.
    """

    expanded = path.expanduser()
    try:
        ensure_private_directory(expanded.parent)
    except SetupFilesystemError as exc:
        raise SetupConfigError("Could not prepare the configuration commit lock directory") from exc

    lock_path = _commit_lock_path(expanded)
    try:
        reject_unsafe_sensitive_target(lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
    except (OSError, SetupFilesystemError) as exc:
        raise SetupConfigError("Could not open the configuration commit lock") from exc

    fcntl = _fcntl_module()
    acquired = False
    try:
        try:
            lock_stat = os.fstat(fd)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise SetupConfigError("Configuration commit lock is not a regular file")
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise SetupConfigError("Could not validate the configuration commit lock") from exc

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise SetupConfigError(
                    "Configuration is locked by another setup writer; retry after it finishes"
                ) from exc
            raise SetupConfigError("Could not acquire the configuration commit lock") from exc

        yield lock_path
    finally:
        if acquired:
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def commit_config_update(path: Path, plan: ConfigUpdatePlan) -> ConfigCommitResult:
    """Persist exactly one reviewed plan under serialized final verification."""

    with _config_commit_lock(path):
        exists, current = _read_existing(path)
        current_digest = _digest(current) if exists else None
        if exists != plan.source_exists or current_digest != plan.source_digest:
            raise SetupConfigError(
                "Configuration changed after review; review the current file again"
            )

        try:
            result = atomic_write_text(
                path,
                plan.candidate_text,
                mode=0o600,
                create_backup=exists,
            )
        except SetupFilesystemError as exc:
            raise SetupConfigError("Could not atomically write reviewed configuration") from exc

        try:
            persisted = read_bounded_utf8(result.path)
        except SetupFilesystemError as exc:
            raise SetupConfigError("Could not verify newly written configuration") from exc
        if persisted != plan.candidate_text:
            raise SetupConfigError("Written configuration did not match the reviewed candidate")

        try:
            config = load_config(result.path)
        except ConfigError as exc:
            raise SetupConfigError(
                "Written configuration failed production reload; backup was preserved"
            ) from exc
        return ConfigCommitResult(path=result.path, backup_path=result.backup_path, config=config)
