"""Additive operational-health summary for ``server_info`` (Post-MVP O10).

The summary deliberately reuses O2/O4's bounded read-only inventory and PID
observation semantics. It never performs reclamation, retention mutation, or
Docker stop/remove/prune work. Availability and pressure are separate states:
a genuine zero PID usage remains an available healthy observation, while an
unreadable optional source stays explicitly unavailable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated, Literal

import docker
from pydantic import BaseModel, ConfigDict, Field

from dev_workspace_mcp.operations.inventory import (
    InventoryLimits,
    ResourceInventoryResult,
    ResourceInventoryService,
)
from dev_workspace_mcp.operations.pressure import PidPressureStatus
from dev_workspace_mcp.operations.resources import ResourceKind
from dev_workspace_mcp.tools import operations as operations_tools

_STATE_FILESYSTEM_PRESSURE_FREE_PERCENT = 10.0
_PID_PRESSURE_UTILIZATION_PERCENT = 90.0

NonnegativeInt = Annotated[int, Field(ge=0)]
Percent = Annotated[float, Field(ge=0.0, le=100.0)]


class OperationalInventoryHealthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "degraded"]
    incomplete: bool
    diagnostic_count: NonnegativeInt


class StateFilesystemHealthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    pressure: Literal["normal", "pressured", "unknown"]
    capacity_bytes: NonnegativeInt | None
    free_bytes: NonnegativeInt | None
    free_percent: Percent | None


class PidOperationalHealthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    pressure: Literal["normal", "pressured", "unknown"]
    source: Literal["cgroup_v2"] | None
    current: NonnegativeInt | None
    limit: NonnegativeInt | None
    utilization_percent: Percent | None
    unlimited: bool | None
    reason: Annotated[str, Field(max_length=512)] | None


class OperationalHealthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "pressured", "degraded"]
    inventory: OperationalInventoryHealthOutput
    state_filesystem: StateFilesystemHealthOutput
    pid_pressure: PidOperationalHealthOutput


def _state_filesystem_health(result: ResourceInventoryResult) -> StateFilesystemHealthOutput:
    state_storage = next(
        (
            resource
            for resource in result.resources
            if resource.kind == ResourceKind.STATE_STORAGE
        ),
        None,
    )
    if state_storage is None:
        return StateFilesystemHealthOutput(
            status="unavailable",
            pressure="unknown",
            capacity_bytes=None,
            free_bytes=None,
            free_percent=None,
        )

    capacity_observation = state_storage.metrics.get("filesystem_capacity_bytes")
    free_observation = state_storage.metrics.get("filesystem_free_bytes")
    capacity = capacity_observation.value if capacity_observation is not None else None
    free = free_observation.value if free_observation is not None else None
    if capacity is None or free is None or capacity <= 0:
        return StateFilesystemHealthOutput(
            status="unavailable",
            pressure="unknown",
            capacity_bytes=capacity,
            free_bytes=free,
            free_percent=None,
        )

    free_percent = round((free * 100.0) / capacity, 2)
    pressure: Literal["normal", "pressured", "unknown"] = (
        "pressured"
        if free_percent <= _STATE_FILESYSTEM_PRESSURE_FREE_PERCENT
        else "normal"
    )
    return StateFilesystemHealthOutput(
        status="available",
        pressure=pressure,
        capacity_bytes=capacity,
        free_bytes=free,
        free_percent=free_percent,
    )


def _pid_health() -> PidOperationalHealthOutput:
    observation = operations_tools.observe_pid_pressure()
    if observation.status == PidPressureStatus.UNAVAILABLE:
        return PidOperationalHealthOutput(
            status="unavailable",
            pressure="unknown",
            source=observation.source,
            current=observation.current,
            limit=observation.limit,
            utilization_percent=None,
            unlimited=observation.unlimited,
            reason=observation.reason,
        )

    utilization_percent: float | None = None
    pressure: Literal["normal", "pressured", "unknown"]
    if observation.unlimited is True:
        pressure = "normal"
    elif (
        observation.current is not None
        and observation.limit is not None
        and observation.limit > 0
    ):
        utilization_percent = round((observation.current * 100.0) / observation.limit, 2)
        pressure = (
            "pressured"
            if utilization_percent >= _PID_PRESSURE_UTILIZATION_PERCENT
            else "normal"
        )
    else:
        pressure = "unknown"

    return PidOperationalHealthOutput(
        status="available",
        pressure=pressure,
        source=observation.source,
        current=observation.current,
        limit=observation.limit,
        utilization_percent=utilization_percent,
        unlimited=observation.unlimited,
        reason=observation.reason,
    )


def get_operational_health(
    *,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    state_db_path: Path,
) -> OperationalHealthOutput:
    """Return one bounded, mutation-free operational health snapshot."""
    inventory_docker = (
        operations_tools._DockerInventoryAdapter(docker_client)
        if docker_client is not None
        else operations_tools._UnavailableDocker()
    )
    result = ResourceInventoryService(
        connection=db,
        docker_client=inventory_docker,
        state_db_path=state_db_path,
        limits=InventoryLimits(),
    ).collect()
    state_filesystem = _state_filesystem_health(result)
    pid_pressure = _pid_health()

    probe_degraded = (
        result.incomplete
        or state_filesystem.status == "unavailable"
        or state_filesystem.pressure == "unknown"
        or pid_pressure.status == "unavailable"
        or pid_pressure.pressure == "unknown"
    )
    if probe_degraded:
        status: Literal["healthy", "pressured", "degraded"] = "degraded"
    elif state_filesystem.pressure == "pressured" or pid_pressure.pressure == "pressured":
        status = "pressured"
    else:
        status = "healthy"

    return OperationalHealthOutput(
        status=status,
        inventory=OperationalInventoryHealthOutput(
            status="degraded" if result.incomplete else "complete",
            incomplete=result.incomplete,
            diagnostic_count=len(result.diagnostics),
        ),
        state_filesystem=state_filesystem,
        pid_pressure=pid_pressure,
    )
