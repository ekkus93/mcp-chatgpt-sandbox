"""O10 acceptance: server_info summarizes operational pressure and degradation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.operations.pressure import PidPressureObservation, PidPressureStatus
from dev_workspace_mcp.server import create_server


class _EmptyCollection:
    def list(self, *_args: object, **_kwargs: object) -> list[Any]:
        return []


class _HealthyDocker:
    containers = _EmptyCollection()
    volumes = _EmptyCollection()

    def ping(self) -> bool:
        return True

    def version(self) -> dict[str, str]:
        return {"Version": "o10-test"}


def _statvfs(*, capacity: int, free: int) -> SimpleNamespace:
    return SimpleNamespace(f_frsize=1, f_blocks=capacity, f_bavail=free)


def _available_pid(*, current: int, limit: int) -> PidPressureObservation:
    return PidPressureObservation(
        status=PidPressureStatus.AVAILABLE,
        source="cgroup_v2",
        current=current,
        limit=limit,
        unlimited=False,
        reason=None,
    )


async def test_o10_server_info_reports_healthy_operational_state_and_preserves_health_sections(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: _HealthyDocker())
    monkeypatch.setattr(
        "dev_workspace_mcp.operations.inventory.os.statvfs",
        lambda _path: _statvfs(capacity=1_000, free=500),
    )
    monkeypatch.setattr(
        "dev_workspace_mcp.tools.operations.observe_pid_pressure",
        lambda: _available_pid(current=0, limit=100),
    )

    payload = (await create_server(minimal_config).call_tool("server_info", {})).structured_content

    assert payload["operations"] == {
        "status": "healthy",
        "inventory": {
            "status": "complete",
            "incomplete": False,
            "diagnostic_count": 0,
        },
        "state_filesystem": {
            "status": "available",
            "pressure": "normal",
            "capacity_bytes": 1_000,
            "free_bytes": 500,
            "free_percent": 50.0,
        },
        "pid_pressure": {
            "status": "available",
            "pressure": "normal",
            "source": "cgroup_v2",
            "current": 0,
            "limit": 100,
            "utilization_percent": 0.0,
            "unlimited": False,
            "reason": None,
        },
    }
    # O10 is additive: GAP3's existing health surfaces remain intact.
    assert "audit" in payload
    assert "maintenance" in payload


async def test_o10_server_info_reports_pressure_without_calling_destructive_tooling(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: _HealthyDocker())
    monkeypatch.setattr(
        "dev_workspace_mcp.operations.inventory.os.statvfs",
        lambda _path: _statvfs(capacity=1_000, free=50),
    )
    monkeypatch.setattr(
        "dev_workspace_mcp.tools.operations.observe_pid_pressure",
        lambda: _available_pid(current=95, limit=100),
    )

    payload = (await create_server(minimal_config).call_tool("server_info", {})).structured_content
    operations = payload["operations"]

    assert operations["status"] == "pressured"
    assert operations["inventory"]["status"] == "complete"
    assert operations["state_filesystem"] == {
        "status": "available",
        "pressure": "pressured",
        "capacity_bytes": 1_000,
        "free_bytes": 50,
        "free_percent": 5.0,
    }
    assert operations["pid_pressure"] == {
        "status": "available",
        "pressure": "pressured",
        "source": "cgroup_v2",
        "current": 95,
        "limit": 100,
        "utilization_percent": 95.0,
        "unlimited": False,
        "reason": None,
    }


async def test_o10_optional_probe_degrades_server_info_and_recovers_without_blocking_startup(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: _HealthyDocker())
    monkeypatch.setattr(
        "dev_workspace_mcp.tools.operations.observe_pid_pressure",
        lambda: PidPressureObservation.unavailable("PermissionError"),
    )

    def unavailable_statvfs(_path: object) -> object:
        raise PermissionError("sensitive host path must not escape")

    monkeypatch.setattr("dev_workspace_mcp.operations.inventory.os.statvfs", unavailable_statvfs)

    # Optional pressure probes are not startup prerequisites.
    server = create_server(minimal_config)
    degraded = (await server.call_tool("server_info", {})).structured_content["operations"]

    assert degraded["status"] == "degraded"
    assert degraded["inventory"]["status"] == "degraded"
    assert degraded["inventory"]["incomplete"] is True
    assert degraded["inventory"]["diagnostic_count"] >= 1
    assert degraded["state_filesystem"] == {
        "status": "unavailable",
        "pressure": "unknown",
        "capacity_bytes": None,
        "free_bytes": None,
        "free_percent": None,
    }
    assert degraded["pid_pressure"] == {
        "status": "unavailable",
        "pressure": "unknown",
        "source": None,
        "current": None,
        "limit": None,
        "utilization_percent": None,
        "unlimited": None,
        "reason": "PermissionError",
    }
    assert "sensitive host path" not in str(degraded)

    monkeypatch.setattr(
        "dev_workspace_mcp.operations.inventory.os.statvfs",
        lambda _path: _statvfs(capacity=1_000, free=500),
    )
    monkeypatch.setattr(
        "dev_workspace_mcp.tools.operations.observe_pid_pressure",
        lambda: _available_pid(current=10, limit=100),
    )

    recovered = (await server.call_tool("server_info", {})).structured_content["operations"]
    assert recovered["status"] == "healthy"
    assert recovered["inventory"]["status"] == "complete"
    assert recovered["state_filesystem"]["pressure"] == "normal"
    assert recovered["pid_pressure"]["pressure"] == "normal"
