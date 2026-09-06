"""Post-MVP Operations O12 / PMOAT-012 reclamation-audit acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.operations.prune_execution import (
    PruneExecutionAggregateStatus,
    PruneExecutionOutcome,
    PruneExecutionResult,
    PruneExecutionService,
    PruneExecutionStatus,
)
from dev_workspace_mcp.server import create_server


def _audit_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_pmoat_012_mixed_reclamation_audit_is_complete_and_redacted(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit must reconstruct the plan/execute decision without host-detail leaks."""
    db = open_db(minimal_config.state_db_path)
    db.executemany(
        """
        INSERT INTO job_log_cleanup
            (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, ?, NULL, NULL, ?)
        """,
        (
            ("job-o12-reclaimed", "ws-o12", "2026-09-05T00:00:00+00:00", 17),
            ("job-o12-failed", "ws-o12", "2026-09-05T00:00:01+00:00", 23),
        ),
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
    assert plan["candidate_count"] == 2

    secret = "ghp_" + ("A" * 36)
    host_path = "/home/phil/.ssh/id_rsa"
    hostile_failure_detail = f"action failed with {secret} while opening {host_path}"

    def _mixed_result(
        _self: PruneExecutionService,
        stored_plan: Any,
        *,
        confirmed: bool,
    ) -> PruneExecutionResult:
        assert confirmed is True
        outcomes = []
        for candidate in stored_plan.candidates:
            reclaimed = candidate.resource_id == "job-o12-reclaimed"
            outcomes.append(
                PruneExecutionOutcome(
                    candidate=candidate,
                    status=(
                        PruneExecutionStatus.RECLAIMED
                        if reclaimed
                        else PruneExecutionStatus.FAILED
                    ),
                    reason=None if reclaimed else hostile_failure_detail,
                )
            )
        return PruneExecutionResult(
            confirmed=True,
            status=PruneExecutionAggregateStatus.MIXED,
            outcomes=tuple(outcomes),
        )

    monkeypatch.setattr(PruneExecutionService, "execute", _mixed_result)
    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content
    assert result["status"] == "mixed"

    raw_audit = minimal_config.audit.path.read_text()
    records = _audit_records(minimal_config.audit.path)
    operations = [record["operation"] for record in records]
    assert operations == ["resource_prune_plan", "resource_prune_execute"]
    assert secret not in raw_audit
    assert host_path not in raw_audit

    plan_event, execute_event = records
    assert plan_event["result"] == "success"
    assert plan_event["metadata"]["plan_id"] == plan["plan_token"]
    plan_candidates = plan_event["metadata"]["candidates"]
    assert [candidate["decision"] for candidate in plan_candidates] == ["planned", "planned"]

    assert execute_event["result"] == "mixed"
    metadata = execute_event["metadata"]
    assert metadata["plan_id"] == plan["plan_token"]
    assert metadata["confirmed"] is True
    assert metadata["status"] == "mixed"
    assert metadata["candidate_count"] == 2
    assert metadata["reclaimed_bytes"] == {
        "known_bytes": 17,
        "unknown_candidate_count": 0,
    }

    candidates = {item["candidate_id"]: item for item in metadata["candidates"]}
    planned = {item["resource_id"]: item for item in plan["candidates"]}
    reclaimed = candidates[planned["job-o12-reclaimed"]["candidate_id"]]
    failed = candidates[planned["job-o12-failed"]["candidate_id"]]

    assert reclaimed == {
        "candidate_id": planned["job-o12-reclaimed"]["candidate_id"],
        "kind": "job_log_cleanup",
        "decision": "executed",
        "outcome": "reclaimed",
        "bytes_reclaimed": 17,
        "failure_classification": None,
    }
    assert failed == {
        "candidate_id": planned["job-o12-failed"]["candidate_id"],
        "kind": "job_log_cleanup",
        "decision": "executed",
        "outcome": "failed",
        "bytes_reclaimed": None,
        "failure_classification": "failure_detail_redacted",
    }
