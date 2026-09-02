"""O4 acceptance for evidence-based ENOSPC and PID-pressure diagnostics."""

from __future__ import annotations

import errno
import importlib
from pathlib import Path
from typing import Any

from docker.errors import APIError, DockerException

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.errors import CommandFailedError
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import operations as operations_tools


def _pressure() -> Any:
    """Import inside tests so pre-O4 code reaches each RED assertion."""
    return importlib.import_module("dev_workspace_mcp.operations.pressure")


def test_o4_state_filesystem_enospc_requires_errno_evidence() -> None:
    pressure = _pressure()

    enospc = pressure.classify_state_filesystem_failure(
        OSError(errno.ENOSPC, "No space left on device")
    )
    assert enospc is not None
    assert enospc.domain.value == "state_filesystem"
    assert enospc.classification.value == "enospc"
    assert enospc.evidence == "errno_enospc"

    # A scary message must not override contradictory errno evidence.
    other_io = pressure.classify_state_filesystem_failure(
        OSError(errno.EIO, "disk full: No space left on device")
    )
    assert other_io is not None
    assert other_io.domain.value == "state_filesystem"
    assert other_io.classification.value == "non_space_io"
    assert other_io.evidence == f"errno_{errno.EIO}"


def test_o4_docker_storage_enospc_requires_docker_api_evidence() -> None:
    pressure = _pressure()

    api_error = APIError(
        "Docker API write failed",
        explanation="failed to register layer: no space left on device",
    )
    diagnostic = pressure.classify_docker_storage_failure(api_error)
    assert diagnostic is not None
    assert diagnostic.domain.value == "docker_storage"
    assert diagnostic.classification.value == "enospc"
    assert diagnostic.evidence == "docker_api_explanation_enospc"

    # Generic DockerException text is not authoritative daemon API evidence.
    assert (
        pressure.classify_docker_storage_failure(
            DockerException("application says disk full / no space left on device")
        )
        is None
    )


def test_o4_container_enospc_requires_failed_exec_signal() -> None:
    pressure = _pressure()

    diagnostic = pressure.classify_container_command_failure(
        exit_code=1,
        stderr="cp: cannot create regular file: No space left on device\n",
    )
    assert diagnostic is not None
    assert diagnostic.domain.value == "container_filesystem"
    assert diagnostic.classification.value == "enospc"
    assert diagnostic.evidence == "container_stderr_enospc"

    assert pressure.classify_container_command_failure(
        exit_code=1, stderr="application-specific error: disk full"
    ) is None
    assert pressure.classify_container_command_failure(
        exit_code=0, stderr="No space left on device"
    ) is None


def test_o4_pressure_context_preserves_primary_workspace_error() -> None:
    pressure = _pressure()
    primary = CommandFailedError(
        "setup command failed",
        details={"workspace_id": "ws-pressure"},
    )
    before = primary.to_dict()
    diagnostic = pressure.classify_container_command_failure(
        exit_code=1,
        stderr="write failed: No space left on device",
    )
    assert diagnostic is not None

    enriched = pressure.enrich_workspace_error(primary, diagnostic)
    assert enriched is primary
    after = enriched.to_dict()
    assert after["code"] == before["code"]
    assert after["message"] == before["message"]
    assert after["recoverable"] == before["recoverable"]
    assert after["details"]["workspace_id"] == "ws-pressure"
    assert after["details"]["pressure"] == {
        "domain": "container_filesystem",
        "classification": "enospc",
        "evidence": "container_stderr_enospc",
    }


def test_o4_pid_pressure_reads_cgroup_v2_usage_and_limit(tmp_path: Path) -> None:
    pressure = _pressure()
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    (proc_root / "self").mkdir(parents=True)
    (proc_root / "self" / "cgroup").write_text(
        "0::/user.slice/mcp-test.scope\n",
        encoding="utf-8",
    )
    pids_dir = cgroup_root / "user.slice" / "mcp-test.scope"
    pids_dir.mkdir(parents=True)
    (pids_dir / "pids.current").write_text("17\n", encoding="ascii")
    (pids_dir / "pids.max").write_text("64\n", encoding="ascii")

    observation = pressure.observe_pid_pressure(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )
    assert observation.status.value == "available"
    assert observation.source == "cgroup_v2"
    assert observation.current == 17
    assert observation.limit == 64
    assert observation.unlimited is False
    assert observation.reason is None


def test_o4_pid_pressure_unavailable_is_typed_and_never_zero(tmp_path: Path) -> None:
    pressure = _pressure()
    observation = pressure.observe_pid_pressure(
        proc_root=tmp_path / "missing-proc",
        cgroup_root=tmp_path / "missing-cgroup",
    )

    assert observation.status.value == "unavailable"
    assert observation.source is None
    assert observation.current is None
    assert observation.limit is None
    assert observation.unlimited is None
    assert observation.reason == "FileNotFoundError"


async def test_o4_resource_inventory_exposes_unavailable_pid_pressure_without_failing(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    pressure = _pressure()
    monkeypatch.setattr(
        operations_tools,
        "observe_pid_pressure",
        lambda: pressure.PidPressureObservation.unavailable("PermissionError"),
    )

    result = await create_server(minimal_config).call_tool("resource_inventory", {})
    payload = result.structured_content
    assert payload["pid_pressure"] == {
        "status": "unavailable",
        "source": None,
        "current": None,
        "limit": None,
        "unlimited": None,
        "reason": "PermissionError",
    }
    assert payload["status"] in {"complete", "degraded"}
