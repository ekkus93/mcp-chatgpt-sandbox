"""Post-MVP Operations review remediation R1 / PMORRAT-001 acceptance."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.server import create_server

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _audit_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _assert_bounded_request_attribution(record: dict[str, Any]) -> None:
    request_id = record["request_id"]
    assert isinstance(request_id, str)
    assert _REQUEST_ID_RE.fullmatch(request_id)
    assert "/" not in request_id
    assert "ghp_" not in request_id
    assert "OPENAI_API_KEY" not in request_id


async def test_r1_reclamation_audit_has_request_attribution(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan/execute audit must identify the invoking request without host details."""
    db = open_db(minimal_config.state_db_path)
    db.execute(
        """
        INSERT INTO job_log_cleanup
            (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, ?, NULL, NULL, ?)
        """,
        ("job-r1-attribution", "ws-r1", "2026-09-06T00:00:00+00:00", 19),
    )
    db.commit()

    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: None)
    server = create_server(minimal_config)

    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["job_log_cleanup"]},
        )
    ).structured_content
    assert plan["candidate_count"] == 1

    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": False},
        )
    ).structured_content
    assert result["status"] == "not_confirmed"

    records = _audit_records(minimal_config.audit.path)
    assert [record["operation"] for record in records] == [
        "resource_prune_plan",
        "resource_prune_execute",
    ]

    plan_event, execute_event = records
    for record in records:
        _assert_bounded_request_attribution(record)
        raw_record = json.dumps(record, sort_keys=True)
        assert "/home/phil" not in raw_record
        assert ".ssh" not in raw_record
        assert "Authorization" not in raw_record

    assert plan_event["metadata"]["plan_id"] == plan["plan_token"]
    assert plan_event["metadata"]["candidate_count"] == 1
    assert plan_event["metadata"]["candidates"] == [
        {
            "candidate_id": plan["candidates"][0]["candidate_id"],
            "kind": "job_log_cleanup",
            "reason": "durable job-log cleanup debt is pending",
            "decision": "planned",
        }
    ]

    assert execute_event["metadata"]["plan_id"] == plan["plan_token"]
    assert execute_event["metadata"]["confirmed"] is False
    assert execute_event["metadata"]["status"] == "not_confirmed"
    assert execute_event["metadata"]["candidate_count"] == 1
    assert execute_event["metadata"]["candidates"] == [
        {
            "candidate_id": plan["candidates"][0]["candidate_id"],
            "kind": "job_log_cleanup",
            "decision": "not_executed",
            "outcome": "not_confirmed",
            "bytes_reclaimed": None,
            "failure_classification": None,
        }
    ]
