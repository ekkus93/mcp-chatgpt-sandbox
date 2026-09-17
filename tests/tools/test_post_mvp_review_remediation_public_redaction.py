"""Post-MVP Operations review remediation R2 / PMORRAT-002 acceptance."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.db.connection import open_db
from mcp_chatgpt_sandbox.operations.prune_execution import (
    PruneExecutionAggregateStatus,
    PruneExecutionOutcome,
    PruneExecutionResult,
    PruneExecutionService,
    PruneExecutionStatus,
)
from mcp_chatgpt_sandbox.operations.prune_plans import PrunePlan
from mcp_chatgpt_sandbox.server import create_server

_HOSTILE_FAILURE_DETAIL = (
    "failed to delete /home/phil/.ssh/id_rsa with Authorization: Bearer ghp_secret "
    "DOCKER_AUTH_CONFIG={secret} SSH_AUTH_SOCK=/tmp/agent.sock OPENAI_API_KEY=sk-secret"
)


def _raw_json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


async def test_r2_public_prune_execute_failure_detail_is_classified_redacted(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public execute output must not leak raw internal failure detail."""
    db = open_db(minimal_config.state_db_path)
    for job_id in ("job-r2-safe-known", "job-r2-hostile-detail"):
        db.execute(
            """
            INSERT INTO job_log_cleanup
                (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
            VALUES (?, ?, ?, NULL, NULL, ?)
            """,
            (job_id, "ws-r2", "2026-09-06T01:00:00+00:00", 23),
        )
    db.commit()

    monkeypatch.setattr("mcp_chatgpt_sandbox.server._try_docker_client", lambda: None)
    server = create_server(minimal_config)
    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["job_log_cleanup"]},
        )
    ).structured_content
    assert plan["candidate_count"] == 2

    def _hostile_execute(
        self: PruneExecutionService,
        plan: PrunePlan,
        *,
        confirmed: bool,
    ) -> PruneExecutionResult:
        assert confirmed is True
        safe_candidate, hostile_candidate = plan.candidates
        return PruneExecutionResult(
            confirmed=True,
            status=PruneExecutionAggregateStatus.DEGRADED,
            outcomes=(
                PruneExecutionOutcome(
                    candidate=safe_candidate,
                    status=PruneExecutionStatus.FAILED,
                    reason="deletion_unproven",
                ),
                PruneExecutionOutcome(
                    candidate=hostile_candidate,
                    status=PruneExecutionStatus.FAILED,
                    reason=_HOSTILE_FAILURE_DETAIL,
                ),
            ),
        )

    monkeypatch.setattr(PruneExecutionService, "execute", _hostile_execute)
    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content

    assert result["status"] == "degraded"
    assert result["failed_count"] == 2
    reasons = [outcome["reason"] for outcome in result["outcomes"]]
    assert reasons == ["deletion_unproven", "failure_detail_redacted"]

    raw_public = _raw_json(result)
    raw_audit = minimal_config.audit.path.read_text()
    for forbidden in (
        _HOSTILE_FAILURE_DETAIL,
        "/home/phil",
        ".ssh/id_rsa",
        "Authorization",
        "Bearer ghp_secret",
        "DOCKER_AUTH_CONFIG",
        "SSH_AUTH_SOCK",
        "OPENAI_API_KEY",
        "sk-secret",
    ):
        assert forbidden not in raw_public
        assert forbidden not in raw_audit

    audit_records: list[dict[str, Any]] = [
        json.loads(line) for line in minimal_config.audit.path.read_text().splitlines()
    ]
    execute_event = audit_records[-1]
    assert execute_event["operation"] == "resource_prune_execute"
    assert [
        candidate["failure_classification"] for candidate in execute_event["metadata"]["candidates"]
    ] == ["deletion_unproven", "failure_detail_redacted"]
