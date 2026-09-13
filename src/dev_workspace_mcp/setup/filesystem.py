"""Fail-closed filesystem primitives used by interactive setup.

Managed setup files are small security-sensitive text files.  These helpers
centralize target inspection, bounded reads, restrictive atomic replacement,
and timestamped backups so Textual widgets never implement file mutation.
"""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_MAX_TEXT_BYTES = 16 * 1024 * 1024


class SetupFilesystemError(RuntimeError):
    """Secret-safe failure while inspecting or mutating setup files."""


@dataclass(frozen=True, slots=True)
class AtomicWriteResult:
    path: Path
    backup_path: Path | None


def _existing_components(path: Path) -> list[Path]:
    absolute = path.expanduser().absolute()
    components: list[Path] = []
    current = absolute
    while True:
        components.append(current)
        if current.parent == current:
            break
        current = current.parent
    components.reverse()
    return components


def reject_unsafe_sensitive_target(path: Path) -> Path:
    """Reject symlinked parents/targets and non-regular existing targets."""

    expanded = path.expanduser().absolute()
    for component in _existing_components(expanded):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SetupFilesystemError(f"Could not inspect setup path {expanded}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SetupFilesystemError(f"Refusing setup path that traverses a symlink: {expanded}")
    try:
        target_info = expanded.lstat()
    except FileNotFoundError:
        return expanded
    except OSError as exc:
        raise SetupFilesystemError(f"Could not inspect setup target {expanded}") from exc
    if not stat.S_ISREG(target_info.st_mode):
        raise SetupFilesystemError(f"Refusing non-regular setup target: {expanded}")
    return expanded


def read_bytes_bounded(path: Path, *, max_bytes: int = _DEFAULT_MAX_TEXT_BYTES) -> bytes:
    """Read at most ``max_bytes`` plus one probe byte from a safe regular file."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    expanded = reject_unsafe_sensitive_target(path)
    try:
        with expanded.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except OSError as exc:
        raise SetupFilesystemError(f"Could not read setup file {expanded}") from exc
    if len(payload) > max_bytes:
        raise SetupFilesystemError(f"Setup file exceeds the {max_bytes}-byte size limit")
    return payload


def read_text_bounded(path: Path, *, max_bytes: int = _DEFAULT_MAX_TEXT_BYTES) -> str:
    raw = read_bytes_bounded(path, max_bytes=max_bytes)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupFilesystemError("Setup file is not valid UTF-8") from exc


def _ensure_parent(path: Path) -> None:
    parent = path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupFilesystemError(f"Could not create setup directory {parent}") from exc
    # Reinspect after mkdir so a concurrently substituted symlink is rejected.
    reject_unsafe_sensitive_target(path)


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.bak.{timestamp}")


def _write_new_file(path: Path, payload: bytes, *, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, mode)
    except OSError as exc:
        raise SetupFilesystemError(f"Could not create setup file {path}") from exc
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode, follow_symlinks=False)
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SetupFilesystemError(f"Could not securely write setup file {path}") from exc


def atomic_write_text(
    path: Path,
    text: str,
    *,
    mode: int = 0o600,
    create_backup: bool = False,
) -> AtomicWriteResult:
    """Atomically replace one reviewed UTF-8 text file with an optional backup."""

    if mode & 0o077:
        raise ValueError("setup managed files must not grant group/other permissions")
    expanded = reject_unsafe_sensitive_target(path)
    _ensure_parent(expanded)
    existed = expanded.exists()
    backup: Path | None = None
    if create_backup and existed:
        original = read_bytes_bounded(expanded)
        backup = _backup_path(expanded)
        _write_new_file(backup, original, mode=mode)

    payload = text.encode("utf-8")
    temporary = expanded.with_name(f".{expanded.name}.tmp.{secrets.token_hex(12)}")
    try:
        _write_new_file(temporary, payload, mode=mode)
        # Re-check the destination immediately before replacement.  Source
        # digest CAS is enforced by config_writer; this protects path shape.
        reject_unsafe_sensitive_target(expanded)
        os.replace(temporary, expanded)
        os.chmod(expanded, mode, follow_symlinks=False)
        directory_fd = os.open(expanded.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SetupFilesystemError:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SetupFilesystemError(f"Could not atomically update setup file {expanded}") from exc
    return AtomicWriteResult(path=expanded, backup_path=backup)


__all__ = [
    "AtomicWriteResult",
    "SetupFilesystemError",
    "atomic_write_text",
    "read_bytes_bounded",
    "read_text_bounded",
    "reject_unsafe_sensitive_target",
]
