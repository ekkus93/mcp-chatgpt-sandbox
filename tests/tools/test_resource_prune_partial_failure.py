"""Post-MVP Operations O9 / PMOAT-010 durable partial-failure acceptance."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docker.errors import NotFound

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.operations.resources import LABEL_RESOURCE_KIND, RESOURCE_KIND_CACHE_VOLUME
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED, LABEL_REPOSITORY_ID
from dev_workspace_mcp.workspace.ssh_agent import agent_socket_path


class _EmptyCollection:
    def list(self, *_args: object, **_kwargs: object) -> list[object]:
        return []

    def get(self, _resource_id: str) -> object:
        raise NotFound("resource absent")


class _Volume:
    def __init__(self) -> None:
        self.name = "cache-o9-owned"
        self.removed = False
        self.attrs: dict[str, Any] = {
            "Name": self.name,
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
                LABEL_REPOSITORY_ID: "repo-o9-orphan",
            },
            "UsageData": {"Size": 4096},
        }

    def reload(self) -> None:
        if self.removed:
            raise NotFound("volume absent")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        if self.removed:
            raise NotFound("volume absent")
        self.removed = True


class _VolumeCollection:
    def __init__(self, volume: _Volume) -> None:
        self._volume = volume

    def list(self, *_args: object, **kwargs: object) -> list[_Volume]:
        if self._volume.removed:
            return []
        raw_labels = (kwargs.get("filters") or {}).get("label", [])
        requirements = [raw_labels] if isinstance(raw_labels, str) else list(raw_labels)
        labels = self._volume.attrs["Labels"]
        for requirement in requirements:
            key, _, value = str(requirement).partition("=")
            if labels.get(key) != value:
                return []
        return [self._volume]

    def get(self, resource_id: str) -> _Volume:
        if self._volume.removed or resource_id != self._volume.name:
            raise NotFound("volume absent")
        return self._volume

    def prune(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("O9 executor must never use volume collection prune")


class _Docker:
    def __init__(self, volume: _Volume) -> None:
        self.containers = _EmptyCollection()
        self.volumes = _VolumeCollection(volume)

    def ping(self) -> bool:
        return True

    def version(self) -> dict[str, str]:
        return {"Version": "test"}


@dataclass(slots=True)
class _PartialFailureFixture:
    server: Any
    volume: _Volume
    workspace_id: str
    socket_path: Path
    fail_unlink: dict[str, bool]


def _seed_orphan_ssh_agent(path: Path, *, workspace_id: str, socket_path: Path) -> None:
    db = open_db(path)
    try:
        with db:
            db.execute(
                """
                INSERT INTO ssh_agent
                    (workspace_id, pid, socket_path, created_at, boot_id, process_start_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    424242,
                    str(socket_path),
                    "2026-09-05T00:00:00Z",
                    "boot-o9",
                    17,
                ),
            )
    finally:
        db.close()


async def _force_partial_failure(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> _PartialFailureFixture:
    volume = _Volume()
    docker_client = _Docker(volume)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    monkeypatch.setattr("dev_workspace_mcp.operations.prune_execution._pid_exists", lambda _pid: False)

    workspace_id = "ws-o9-cleanup"
    runtime_dir = minimal_config.state_db_path.parent / "ssh-agents"
    socket_path = agent_socket_path(runtime_dir, workspace_id)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_bytes(b"o9-pending-socket-bytes")

    server = create_server(minimal_config)
    _seed_orphan_ssh_agent(
        minimal_config.state_db_path,
        workspace_id=workspace_id,
        socket_path=socket_path,
    )

    original_unlink = Path.unlink
    fail_unlink = {"enabled": True}

    def controlled_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == socket_path and fail_unlink["enabled"]:
            raise PermissionError("forced O9 secondary socket cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", controlled_unlink)

    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["cache_volume", "ssh_agent"]},
        )
    ).structured_content
    assert plan["candidate_count"] == 2
    assert {item["kind"] for item in plan["candidates"]} == {"cache_volume", "ssh_agent"}

    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert result["reclaimed_count"] == 1
    assert result["failed_count"] == 1
    by_kind = {item["kind"]: item for item in result["outcomes"]}
    assert by_kind["cache_volume"]["status"] == "reclaimed"
    assert by_kind["ssh_agent"]["status"] == "failed"
    assert by_kind["ssh_agent"]["reason"] == "secondary_cleanup_failed"
    assert volume.removed is True

    return _PartialFailureFixture(
        server=server,
        volume=volume,
        workspace_id=workspace_id,
        socket_path=socket_path,
        fail_unlink=fail_unlink,
    )


async def test_o9_execute_schema_exposes_aggregate_partial_failure_state(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tool = next(tool for tool in await server.list_tools() if tool.name == "resource_prune_execute")

    status_schema = tool.output_schema["properties"]["status"]
    serialized = str(status_schema)
    assert "not_confirmed" in serialized
    assert "success" in serialized
    assert "mixed" in serialized
    assert "degraded" in serialized


async def test_pmoat_010_partial_failure_is_mixed_and_durable_in_inventory(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    fixture = await _force_partial_failure(minimal_config, monkeypatch)

    # The primary ssh-agent process/state identity is already gone. O9 must
    # leave only bounded cleanup debt, not resurrect the ssh_agent row.
    db = open_db(minimal_config.state_db_path)
    try:
        assert db.execute(
            "SELECT 1 FROM ssh_agent WHERE workspace_id = ?", (fixture.workspace_id,)
        ).fetchone() is None
    finally:
        db.close()

    inventory = (
        await fixture.server.call_tool(
            "resource_inventory",
            {"workspace_id": fixture.workspace_id},
        )
    ).structured_content
    pending = [
        item
        for item in inventory["resources"]
        if item["kind"] == "ssh_agent" and item["lifecycle_state"] == "cleanup_pending"
    ]
    assert len(pending) == 1
    assert pending[0]["bytes"] == {
        "value": len(b"o9-pending-socket-bytes"),
        "provenance": "accounted",
        "reason": None,
    }
    assert pending[0]["metadata"]["last_error_recorded"] is True
    assert inventory["cleanup_debt"]["count"] == 1
    assert inventory["cleanup_debt"]["bytes"]["value"] == len(b"o9-pending-socket-bytes")


async def test_pmoat_010_fresh_plan_retries_debt_and_repeated_execute_is_idempotent(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    fixture = await _force_partial_failure(minimal_config, monkeypatch)
    fixture.fail_unlink["enabled"] = False

    # A new server has an empty O6/O7 in-memory plan store. The durable debt
    # must therefore be sufficient for a fresh plan after restart.
    restarted = create_server(minimal_config)
    retry_plan = (
        await restarted.call_tool(
            "resource_prune_plan",
            {
                "workspace_id": fixture.workspace_id,
                "resource_kinds": ["ssh_agent"],
            },
        )
    ).structured_content
    assert retry_plan["candidate_count"] == 1
    assert retry_plan["candidates"][0]["kind"] == "ssh_agent"
    assert retry_plan["candidates"][0]["required_lifecycle_state"] == "cleanup_pending"
    assert retry_plan["candidates"][0]["action"] == "retry_ssh_agent_cleanup"

    retried = (
        await restarted.call_tool(
            "resource_prune_execute",
            {"plan_token": retry_plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert retried["status"] == "success"
    assert retried["reclaimed_count"] == 1
    assert retried["failed_count"] == 0
    assert fixture.socket_path.exists() is False

    inventory = (
        await restarted.call_tool(
            "resource_inventory",
            {"workspace_id": fixture.workspace_id},
        )
    ).structured_content
    assert inventory["cleanup_debt"]["count"] == 0
    assert not any(
        item["kind"] == "ssh_agent" and item["lifecycle_state"] == "cleanup_pending"
        for item in inventory["resources"]
    )

    repeated = (
        await restarted.call_tool(
            "resource_prune_execute",
            {"plan_token": retry_plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert repeated["status"] == "success"
    assert repeated["reclaimed_count"] == 0
    assert repeated["already_absent_count"] == 1
    assert repeated["failed_count"] == 0
