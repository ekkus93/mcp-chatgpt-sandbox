from pathlib import Path

path = Path("src/dev_workspace_mcp/tools/prune.py")
text = path.read_text()

text = text.replace(
    '"""Public plan-first reclamation tools for O7-O9 / PMOR-006..010.',
    '"""Public plan-first reclamation tools for O7-O12 / PMOR-006..012.',
    1,
)

text = text.replace(
    "from dev_workspace_mcp.operations.prune_execution import (\n"
    "    PruneExecutionService,\n"
    "    PruneExecutionStatus,\n"
    ")",
    "from dev_workspace_mcp.operations.prune_execution import (\n"
    "    PruneExecutionResult,\n"
    "    PruneExecutionService,\n"
    "    PruneExecutionStatus,\n"
    ")",
    1,
)

anchor = """_DYNAMIC_OWNERSHIP_LABELS = frozenset(
    {LABEL_REPOSITORY_ID, LABEL_WORKSPACE_ID, LABEL_TEST_NAMESPACE}
)
"""
replacement = anchor + """
_SAFE_AUDIT_PLAN_REASONS = frozenset(
    {
        "positively owned workspace container is orphaned from durable state",
        "positively owned cache volume is orphaned from durable repository state",
        "durable job-log cleanup debt is pending",
        "positively owned SSH agent is orphaned from durable workspace state",
        "durable SSH-agent socket cleanup debt is pending",
    }
)
_SAFE_AUDIT_FAILURE_CLASSIFICATIONS = frozenset(
    {
        "action_failed",
        "deletion_probe_failed",
        "deletion_unproven",
        "identity_changed",
        "identity_probe_failed",
        "secondary_cleanup_failed",
        "unsupported_action",
        "unsupported_candidate_kind",
    }
)
"""
assert anchor in text
text = text.replace(anchor, replacement, 1)

anchor = """def _projected_bytes(candidates: tuple[PruneCandidate, ...]) -> ProjectedBytesOutput:
    values = [candidate.bytes.value for candidate in candidates]
    unknown = sum(value is None for value in values)
    return ProjectedBytesOutput(
        known_bytes=sum(value for value in values if value is not None),
        unknown_candidate_count=unknown,
        complete=unknown == 0,
    )
"""
replacement = anchor + """


def _audit_plan_reason(candidate: PruneCandidate) -> str:
    if candidate.reason in _SAFE_AUDIT_PLAN_REASONS:
        return candidate.reason
    return "eligible_reclamation_candidate"


def _audit_failure_classification(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason in _SAFE_AUDIT_FAILURE_CLASSIFICATIONS:
        return reason
    return "failure_detail_redacted"


def _audit_reclaimed_bytes(result: PruneExecutionResult) -> dict[str, int]:
    values = [
        outcome.candidate.bytes.value
        for outcome in result.outcomes
        if outcome.status is PruneExecutionStatus.RECLAIMED
    ]
    return {
        "known_bytes": sum(value for value in values if value is not None),
        "unknown_candidate_count": sum(value is None for value in values),
    }


def _audit_execution_candidates(result: PruneExecutionResult) -> list[dict[str, object]]:
    decision = "executed" if result.confirmed else "not_executed"
    problem_statuses = {PruneExecutionStatus.STALE, PruneExecutionStatus.FAILED}
    return [
        {
            "candidate_id": outcome.candidate.candidate_id,
            "kind": outcome.candidate.kind.value,
            "decision": decision,
            "outcome": outcome.status.value,
            "bytes_reclaimed": (
                outcome.candidate.bytes.value
                if outcome.status is PruneExecutionStatus.RECLAIMED
                else None
            ),
            "failure_classification": (
                _audit_failure_classification(outcome.reason)
                if outcome.status in problem_statuses
                else None
            ),
        }
        for outcome in result.outcomes
    ]
"""
assert anchor in text
text = text.replace(anchor, replacement, 1)

old = """                "candidates": [
                    {"candidate_id": candidate.candidate_id, "kind": candidate.kind.value}
                    for candidate in plan.candidates
                ],
"""
new = """                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "kind": candidate.kind.value,
                        "reason": _audit_plan_reason(candidate),
                        "decision": "planned",
                    }
                    for candidate in plan.candidates
                ],
"""
assert old in text
text = text.replace(old, new, 1)

old = """        return ResourcePruneExecuteOutput(
            confirmed=result.confirmed,
            executed=result.confirmed,
            status=result.status.value,
            candidate_count=len(outputs),
            reclaimed_count=sum(
                outcome.status is PruneExecutionStatus.RECLAIMED for outcome in result.outcomes
            ),
            already_absent_count=sum(
                outcome.status is PruneExecutionStatus.ALREADY_ABSENT for outcome in result.outcomes
            ),
            stale_count=sum(
                outcome.status is PruneExecutionStatus.STALE for outcome in result.outcomes
            ),
            failed_count=sum(
                outcome.status is PruneExecutionStatus.FAILED for outcome in result.outcomes
            ),
            outcomes=outputs,
        )
"""
new = """        output = ResourcePruneExecuteOutput(
            confirmed=result.confirmed,
            executed=result.confirmed,
            status=result.status.value,
            candidate_count=len(outputs),
            reclaimed_count=sum(
                outcome.status is PruneExecutionStatus.RECLAIMED for outcome in result.outcomes
            ),
            already_absent_count=sum(
                outcome.status is PruneExecutionStatus.ALREADY_ABSENT for outcome in result.outcomes
            ),
            stale_count=sum(
                outcome.status is PruneExecutionStatus.STALE for outcome in result.outcomes
            ),
            failed_count=sum(
                outcome.status is PruneExecutionStatus.FAILED for outcome in result.outcomes
            ),
            outcomes=outputs,
        )
        write_audit_event(
            audit_config,
            operation="resource_prune_execute",
            result=result.status.value,
            metadata={
                "plan_id": plan.token,
                "confirmed": result.confirmed,
                "status": result.status.value,
                "candidate_count": len(result.outcomes),
                "reclaimed_bytes": _audit_reclaimed_bytes(result),
                "candidates": _audit_execution_candidates(result),
            },
        )
        return output
"""
assert old in text
text = text.replace(old, new, 1)

path.write_text(text)
