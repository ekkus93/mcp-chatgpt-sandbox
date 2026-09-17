"""Evidence-based disk and PID-pressure diagnostics (Post-MVP Operations O4).

Disk-pressure classification is deliberately domain-specific.  Callers must
choose the classifier whose evidence source they actually possess; this module
never upgrades a vague "disk full" message into ENOSPC when errno/API/exit
status evidence says otherwise.

PID pressure intentionally supports one Linux source only: cgroup v2.  The
current process' unified cgroup path comes from ``/proc/self/cgroup``'s
``0::<path>`` record; usage and limit come from that cgroup's ``pids.current``
and ``pids.max`` under ``/sys/fs/cgroup``.  Missing procfs/cgroup files,
permissions, malformed content, or a non-v2 layout are reported as typed
``unavailable`` observations.  There is no cgroup-v1 or host ``pid_max``
fallback because those values do not describe the same enforcement boundary.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from docker.errors import APIError, DockerException

if TYPE_CHECKING:
    from mcp_chatgpt_sandbox.errors import WorkspaceError


class PressureDomain(StrEnum):
    STATE_FILESYSTEM = "state_filesystem"
    DOCKER_STORAGE = "docker_storage"
    CONTAINER_FILESYSTEM = "container_filesystem"


class PressureClassification(StrEnum):
    ENOSPC = "enospc"
    NON_SPACE_IO = "non_space_io"


@dataclass(frozen=True, slots=True)
class PressureDiagnostic:
    domain: PressureDomain
    classification: PressureClassification
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain.value,
            "classification": self.classification.value,
            "evidence": self.evidence,
        }


class PidPressureStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PidPressureObservation:
    status: PidPressureStatus
    source: Literal["cgroup_v2"] | None
    current: int | None
    limit: int | None
    unlimited: bool | None
    reason: str | None

    @classmethod
    def unavailable(cls, reason: str) -> PidPressureObservation:
        return cls(
            status=PidPressureStatus.UNAVAILABLE,
            source=None,
            current=None,
            limit=None,
            unlimited=None,
            reason=reason,
        )


def classify_state_filesystem_failure(exc: OSError) -> PressureDiagnostic | None:
    """Classify an error already known to come from daemon state storage.

    ``errno`` is authoritative.  A contradictory message cannot turn EIO,
    EDQUOT, EACCES, etc. into ENOSPC.  An ``OSError`` with no errno carries
    too little evidence and is left unclassified.
    """
    if exc.errno is None:
        return None
    if exc.errno == errno.ENOSPC:
        return PressureDiagnostic(
            domain=PressureDomain.STATE_FILESYSTEM,
            classification=PressureClassification.ENOSPC,
            evidence="errno_enospc",
        )
    return PressureDiagnostic(
        domain=PressureDomain.STATE_FILESYSTEM,
        classification=PressureClassification.NON_SPACE_IO,
        evidence=f"errno_{exc.errno}",
    )


def classify_docker_storage_failure(exc: DockerException) -> PressureDiagnostic | None:
    """Recognize Docker-storage ENOSPC only from daemon API explanation.

    A generic ``DockerException`` message is not sufficient because command,
    application, transport, and user-provided strings can all flow through
    generic exception text.  ``APIError.explanation`` is the daemon response
    channel this classifier trusts.
    """
    if not isinstance(exc, APIError):
        return None
    explanation = exc.explanation
    if not isinstance(explanation, str):
        return None
    if "no space left on device" not in explanation.casefold():
        return None
    return PressureDiagnostic(
        domain=PressureDomain.DOCKER_STORAGE,
        classification=PressureClassification.ENOSPC,
        evidence="docker_api_explanation_enospc",
    )


def classify_container_command_failure(*, exit_code: int, stderr: str) -> PressureDiagnostic | None:
    """Recognize container-filesystem ENOSPC from a failed command only."""
    if exit_code == 0:
        return None
    if "no space left on device" not in stderr.casefold():
        return None
    return PressureDiagnostic(
        domain=PressureDomain.CONTAINER_FILESYSTEM,
        classification=PressureClassification.ENOSPC,
        evidence="container_stderr_enospc",
    )


def enrich_workspace_error(
    exc: WorkspaceError,
    diagnostic: PressureDiagnostic,
) -> WorkspaceError:
    """Attach pressure evidence without replacing the primary error object."""
    exc.details = {**exc.details, "pressure": diagnostic.to_dict()}
    return exc


def _cgroup_v2_relative_path(text: str) -> PurePosixPath:
    matches = [line[3:] for line in text.splitlines() if line.startswith("0::")]
    if len(matches) != 1:
        raise ValueError("cgroup v2 membership unavailable")
    raw = matches[0]
    path = PurePosixPath(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid cgroup v2 membership path")
    return PurePosixPath(*path.parts[1:])


def _parse_nonnegative_int(text: str) -> int:
    value = int(text.strip())
    if value < 0:
        raise ValueError("negative cgroup PID value")
    return value


def observe_pid_pressure(
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> PidPressureObservation:
    """Read the current daemon process' cgroup-v2 PID usage and limit.

    Failures are intentionally converted only to a sanitized type name.  They
    do not escape this optional observation path and cannot block ordinary MCP
    workspace operations.
    """
    try:
        membership = (proc_root / "self" / "cgroup").read_text(encoding="utf-8")
        relative = _cgroup_v2_relative_path(membership)
        pids_dir = cgroup_root.joinpath(*relative.parts)
        current = _parse_nonnegative_int((pids_dir / "pids.current").read_text(encoding="ascii"))
        raw_limit = (pids_dir / "pids.max").read_text(encoding="ascii").strip()
        if raw_limit == "max":
            limit = None
            unlimited = True
        else:
            limit = _parse_nonnegative_int(raw_limit)
            unlimited = False
        return PidPressureObservation(
            status=PidPressureStatus.AVAILABLE,
            source="cgroup_v2",
            current=current,
            limit=limit,
            unlimited=unlimited,
            reason=None,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        # Expose only the exception type, never its path/message.
        return PidPressureObservation.unavailable(type(exc).__name__)
