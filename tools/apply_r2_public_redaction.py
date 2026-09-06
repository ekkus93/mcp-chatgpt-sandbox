from pathlib import Path

path = Path("src/dev_workspace_mcp/tools/prune.py")
text = path.read_text()

old_constant = '''_SAFE_AUDIT_FAILURE_CLASSIFICATIONS = frozenset(
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
'''
new_constant = '''_SAFE_AUDIT_FAILURE_CLASSIFICATIONS = frozenset(
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
_SAFE_PUBLIC_EXECUTION_REASONS = _SAFE_AUDIT_FAILURE_CLASSIFICATIONS | frozenset(
    {
        "identity_ambiguous",
        "inventory_incomplete",
        "lifecycle_changed",
        "ownership_changed",
        "ownership_evidence_changed",
        "ownership_unprovable",
        "precondition_changed",
        "resource_absent",
        "scope_changed",
    }
)
'''
if old_constant not in text:
    raise SystemExit("could not find R2 failure-classification constant block")
text = text.replace(old_constant, new_constant, 1)

old_function = '''def _audit_failure_classification(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason in _SAFE_AUDIT_FAILURE_CLASSIFICATIONS:
        return reason
    return "failure_detail_redacted"


'''
new_function = '''def _audit_failure_classification(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason in _SAFE_AUDIT_FAILURE_CLASSIFICATIONS:
        return reason
    return "failure_detail_redacted"


def _public_execution_reason(
    status: PruneExecutionStatus,
    reason: str | None,
) -> str | None:
    if reason is None:
        return None
    if status not in {PruneExecutionStatus.STALE, PruneExecutionStatus.FAILED}:
        return reason if reason in _SAFE_PUBLIC_EXECUTION_REASONS else None
    if reason in _SAFE_PUBLIC_EXECUTION_REASONS:
        return reason
    return "failure_detail_redacted"


'''
if old_function not in text:
    raise SystemExit("could not find R2 audit failure helper block")
text = text.replace(old_function, new_function, 1)

old_output = '''                reason=outcome.reason,
'''
new_output = '''                reason=_public_execution_reason(outcome.status, outcome.reason),
'''
if text.count(old_output) != 1:
    raise SystemExit(f"expected one public outcome reason assignment, found {text.count(old_output)}")
text = text.replace(old_output, new_output, 1)

path.write_text(text)
