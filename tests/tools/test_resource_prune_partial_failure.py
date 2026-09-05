"""Post-MVP Operations O9 / PMOAT-010 durable partial-reclamation acceptance."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.server import create_server


async def test_pmoat_010_partial_secondary_cleanup_is_durable_accounted_and_retryable(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = minimal_config.state_db_path.parent
    workspace_id = "ws-o9-secondary-cleanup"
    job_id = "job-o9-independent-success"
    socket_path = state_dir / "ssh-agents" / workspace_id / "agent.sock"
    socket_payload = b"o9-pending-socket-artifact-bytes"
    job_dir = state_dir / "jobs" / job_id

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_bytes(socket_payload)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "stdout.log").write_bytes(b"job-log")

    db = open_db(minimal_config.state_db_path)
    db.execute(
        """
        INSERT INTO ssh_agent
            (workspace_id, pid, socket_path, created_at, boot_id, process_start_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            2_147_483_647,
            str(socket_path),
            "2026-09-05T00:00:00+00:00",
            "boot-o9",
            1,
        ),
    )
    db.execute(
        """
        INSERT INTO job_log_cleanup
            (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, ?, NULL, NULL, ?)
        """,
        (job_id, workspace_id, "2026-09-05T00:00:00+00:00", 7),
    )
    db.commit()

    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: None)
    server = create_server(minimal_config)
    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["job_log_cleanup", "ssh_agent"]},
        )
    ).structured_content
    assert plan["candidate_count"] == 2

    original_unlink = Path.unlink

    def _fail_only_agent_socket(self: Path, missing_ok: bool = False) -> None:
        if self == socket_path:
            raise PermissionError(errno.EACCES, "forced O9 secondary cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _fail_only_agent_socket)
    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content
    outcomes = {item["kind"]: item for item in result["outcomes"]}

    assert result["status"] == "mixed"
    assert result["reclaimed_count"] == 1
    assert result["failed_count"] == 1
    assert outcomes["job_log_cleanup"]["status"] == "reclaimed"
    assert outcomes["ssh_agent"]["status"] == "failed"
    assert outcomes["ssh_agent"]["reason"] == "secondary_cleanup_failed"
    assert not job_dir.exists()
    assert socket_path.exists()
    assert db.execute(
        "SELECT 1 FROM ssh_agent WHERE workspace_id = ?", (workspace_id,)
    ).fetchone() is None

    debt = db.execute(
        """
        SELECT workspace_id, bytes_hint, last_error
        FROM ssh_agent_cleanup
        WHERE workspace_id = ?
        """,
        (workspace_id,),
    ).fetchone()
    assert debt is not None
    assert debt["workspace_id"] == workspace_id
    assert debt["bytes_hint"] == len(socket_payload)
    assert debt["last_error"] is not None

    inventory = (await server.call_tool("resource_inventory", {})).structured_content
    cleanup_resources = [
        resource
        for resource in inventory["resources"]
        if resource["kind"] == "ssh_agent_cleanup"
    ]
    assert len(cleanup_resources) == 1
    assert cleanup_resources[0]["resource_id"] == workspace_id
    assert cleanup_resources[0]["lifecycle_state"] == "cleanup_pending"
    assert cleanup_resources[0]["bytes"] == {
        "value": len(socket_payload),
        "provenance": "accounted",
        "reason": None,
    }
    assert inventory["cleanup_debt"]["count"] == 1
    assert inventory["cleanup_debt"]["bytes"]["value"] == len(socket_payload)

    # The original plan remains retryable while its token is alive: restoring
    # the secondary cleanup operation must clear debt rather than converting
    # the already-removed primary resource into blanket success.
    monkeypatch.setattr(Path, "unlink", original_unlink)
    original_retry = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert original_retry["status"] == "success"
    assert original_retry["reclaimed_count"] == 1
    assert original_retry["already_absent_count"] == 1
    assert not socket_path.exists()
    assert db.execute(
        "SELECT 1 FROM ssh_agent_cleanup WHERE workspace_id = ?", (workspace_id,)
    ).fetchone() is None

    # Durable retry must also survive loss of the original authorization token:
    # recreate debt, then prove a fresh cleanup-specific plan can reclaim it.
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_bytes(socket_payload)
    db.execute(
        """
        INSERT INTO ssh_agent_cleanup
            (workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, NULL, ?, ?)
        """,
        (
            workspace_id,
            "2026-09-05T00:01:00+00:00",
            "forced persisted debt",
            len(socket_payload),
        ),
    )
    db.commit()

    retry_plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["ssh_agent_cleanup"]},
        )
    ).structured_content
    assert retry_plan["candidate_count"] == 1
    assert retry_plan["candidates"][0]["kind"] == "ssh_agent_cleanup"
    assert retry_plan["candidates"][0]["bytes"]["value"] == len(socket_payload)

    retry = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": retry_plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert retry["status"] == "success"
    assert retry["reclaimed_count"] == 1
    assert retry["failed_count"] == 0
    assert not socket_path.exists()
    assert db.execute(
        "SELECT 1 FROM ssh_agent_cleanup WHERE workspace_id = ?", (workspace_id,)
    ).fetchone() is None

    repeated = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": retry_plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert repeated["status"] == "success"
    assert repeated["reclaimed_count"] == 0
    assert repeated["already_absent_count"] == 1
    assert repeated["failed_count"] == 0

    final_inventory = (await server.call_tool("resource_inventory", {})).structured_content
    assert not any(
        resource["kind"] == "ssh_agent_cleanup"
        for resource in final_inventory["resources"]
    )
    assert final_inventory["cleanup_debt"]["count"] == 0


def test_o9_cleanup_debt_schema_persists_identity_not_host_path(
    minimal_config: ServerConfig,
) -> None:
    db = open_db(minimal_config.state_db_path)
    columns = {
        row["name"]: row
        for row in db.execute("PRAGMA table_info(ssh_agent_cleanup)").fetchall()
    }

    assert set(columns) == {
        "workspace_id",
        "created_at",
        "last_attempt_at",
        "last_error",
        "bytes_hint",
    }
    assert "socket_path" not in columns
    assert columns["workspace_id"]["pk"] == 1
