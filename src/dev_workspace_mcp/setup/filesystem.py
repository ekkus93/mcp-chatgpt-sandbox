"""Fail-closed filesystem primitives used by interactive setup."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_MAX_SETUP_FILE_BYTES = 1024 * 1024


class SetupFilesystemError(RuntimeError):
    """Secret-safe filesystem failure suitable for operator presentation."""


@dataclass(frozen=True, slots=True)
class AtomicWriteResult:
    """Non-secret result of one atomic setup-file replacement."""

    path: Path
    backup_path: Path | None


def _describe_os_error(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupFilesystemError(
            f"Could not inspect setup path {path}: {_describe_os_error(exc)}"
        ) from exc


def reject_unsafe_sensitive_target(path: Path) -> None:
    """Reject symlinks and non-regular existing sensitive-file targets."""

    expanded = path.expanduser()
    existing = _lstat(expanded)
    if existing is None:
        return
    if stat.S_ISLNK(existing.st_mode):
        raise SetupFilesystemError(f"Refusing symlink setup target: {expanded}")
    if not stat.S_ISREG(existing.st_mode):
        raise SetupFilesystemError(f"Setup target is not a regular file: {expanded}")


def ensure_private_directory(path: Path) -> Path:
    """Create or validate a setup-owned directory and force mode 0700."""

    expanded = path.expanduser()
    existing = _lstat(expanded)
    if existing is not None:
        if stat.S_ISLNK(existing.st_mode):
            raise SetupFilesystemError(f"Refusing symlink setup directory: {expanded}")
        if not stat.S_ISDIR(existing.st_mode):
            raise SetupFilesystemError(f"Expected setup directory at {expanded}")
    try:
        expanded.mkdir(parents=True, mode=0o700, exist_ok=True)
        expanded.chmod(0o700)
    except OSError as exc:
        raise SetupFilesystemError(
            f"Could not prepare setup directory {expanded}: {_describe_os_error(exc)}"
        ) from exc
    return expanded


def read_bounded_utf8(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_SETUP_FILE_BYTES,
) -> str:
    """Read one regular UTF-8 setup file without accepting an oversized value."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    expanded = path.expanduser()
    reject_unsafe_sensitive_target(expanded)
    try:
        with expanded.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise SetupFilesystemError(
            f"Could not read setup file {expanded}: {_describe_os_error(exc)}"
        ) from exc
    if len(raw) > max_bytes:
        raise SetupFilesystemError(f"Setup file exceeds the {max_bytes}-byte safety limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupFilesystemError("Setup file is not valid UTF-8") from exc


def _backup_name(path: Path, *, now: datetime) -> Path:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    base = path.with_name(f"{path.name}.bak.{stamp}")
    candidate = base
    counter = 1
    while _lstat(candidate) is not None:
        candidate = path.with_name(f"{base.name}.{counter}")
        counter += 1
    return candidate


def backup_regular_file(path: Path, *, now: datetime | None = None) -> Path | None:
    """Copy an existing regular file to an owner-only timestamped backup."""

    expanded = path.expanduser()
    reject_unsafe_sensitive_target(expanded)
    if _lstat(expanded) is None:
        return None
    backup_path = _backup_name(expanded, now=now or datetime.now(UTC))
    try:
        shutil.copyfile(expanded, backup_path)
        backup_path.chmod(0o600)
    except OSError as exc:
        with suppress(OSError):
            backup_path.unlink(missing_ok=True)
        raise SetupFilesystemError(
            f"Could not back up setup file {expanded}: {_describe_os_error(exc)}"
        ) from exc
    return backup_path


def atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    create_backup: bool = True,
) -> AtomicWriteResult:
    """Atomically write UTF-8 text in the target directory.

    A backup, when requested, is completed before the replacement.  It is
    intentionally retained if any later validation fails so repair/recovery
    never depends on the failed candidate.
    """

    expanded = path.expanduser()
    parent = ensure_private_directory(expanded.parent)
    reject_unsafe_sensitive_target(expanded)
    backup_path = backup_regular_file(expanded) if create_backup else None

    fd = -1
    temporary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix=f".{expanded.name}.", suffix=".tmp", dir=parent)
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, expanded)
        temporary = None
        expanded.chmod(mode)
    except OSError as exc:
        raise SetupFilesystemError(
            f"Could not update setup file {expanded}: {_describe_os_error(exc)}"
        ) from exc
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    return AtomicWriteResult(path=expanded, backup_path=backup_path)


def is_owner_only_file(path: Path) -> bool:
    """Return true for a regular non-symlink file with no group/other bits."""

    existing = _lstat(path.expanduser())
    if existing is None or stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
        return False
    return stat.S_IMODE(existing.st_mode) & 0o077 == 0


def is_owner_only_directory(path: Path) -> bool:
    """Return true for a non-symlink directory with no group/other bits."""

    existing = _lstat(path.expanduser())
    if existing is None or stat.S_ISLNK(existing.st_mode) or not stat.S_ISDIR(existing.st_mode):
        return False
    return stat.S_IMODE(existing.st_mode) & 0o077 == 0
