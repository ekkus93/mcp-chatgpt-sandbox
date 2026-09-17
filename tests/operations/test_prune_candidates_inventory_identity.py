"""O5 hardening: internal inventory must preserve Y2 SSH-agent identity."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mcp_chatgpt_sandbox.db.schema import apply_migrations
from mcp_chatgpt_sandbox.operations.inventory import ResourceInventoryService
from mcp_chatgpt_sandbox.operations.resources import ResourceKind


class _EmptyCollection:
    def list(self, *_args: object, **_kwargs: object) -> list[Any]:
        return []


class _EmptyDocker:
    def __init__(self) -> None:
        self.containers = _EmptyCollection()
        self.volumes = _EmptyCollection()


def test_o5_inventory_preserves_full_y2_ssh_agent_identity_internally(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    apply_migrations(connection)
    connection.execute(
        """
        INSERT INTO ssh_agent
            (workspace_id, pid, socket_path, created_at, boot_id, process_start_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "workspace-orphan-agent",
            4242,
            "/host/private/agent.sock",
            "2026-09-03T00:00:00Z",
            "boot-opaque",
            123456,
        ),
    )
    state_db_path = tmp_path / "state.db"
    state_db_path.write_bytes(b"state")

    result = ResourceInventoryService(
        connection=connection,
        docker_client=_EmptyDocker(),
        state_db_path=state_db_path,
    ).collect()

    agent = next(
        resource for resource in result.resources if resource.kind is ResourceKind.SSH_AGENT
    )
    assert agent.details["pid"] == 4242
    assert agent.details["boot_id"] == "boot-opaque"
    assert agent.details["process_start_time"] == 123456
    assert "socket_path" not in agent.details
