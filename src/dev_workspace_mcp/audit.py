"""Audit event writing (§24) — the JSONL log, extended in S29 to cover
`workspace_opened`, `repository_authorization_failure`, and structured
Git denials (not only successes) alongside S17's original idle-stop
event; not the full audit surface.

`AuditConfig.path` (§52) is an append-only JSONL file — one §24-shaped
record per line, matching the recommended shape (`timestamp`,
`request_id`, `session_id`, `workspace_id`, `repository_id`, `operation`,
`result`, plus free-form `metadata`). §67/§68's `audit_event` SQLite
table (a queryable *index* over the same records, not a second copy of
the log) is deliberately still not written here: this function has no
`db: sqlite3.Connection` of its own, and every one of its ~10 call sites
would need one threaded through to add it. S29 (§24's event-list
coverage, denial auditing) left this specific piece deferred rather than
half-built, since its only consumer — `audit_recent` — is itself
deferred to a later phase (§34.3); wiring the write side alone, with
nothing to read it back, was judged not worth the same invasive
`db`-threading `audit_denials` (S29, `tools/git.py`) was specifically
designed to avoid for its own scope. `request_id`/`session_id` are
always `None`: nothing in the codebase threads either through to a tool
call yet.

HR-015 (`docs/HARDENING_SPEC.md` §8): an unwritable audit path (a
misconfigured `audit.path`, a full disk, a permissions change after
startup) SHALL NOT turn the write below into an unhandled crash that
replaces the real result — most importantly a denial, where a failed
audit write would otherwise mask a correctly-computed `PolicyDeniedError`
behind an opaque, unrelated `OSError` raised from deep inside
`audit_denials`'s own exception handler in `tools/git.py`, but just as
real for an ordinary successful commit/pull/push, where the actual Git
operation already happened and there is nothing left to roll back."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from dev_workspace_mcp.config.models import AuditConfig
from dev_workspace_mcp.errors import redact_secrets


def write_audit_event(
    config: AuditConfig,
    *,
    operation: str,
    result: str,
    workspace_id: str | None = None,
    repository_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Appends one JSON line to `config.path`. A no-op when auditing is
    disabled (`audit.enabled: false`) — not an error, since disabling
    audit logging is a deliberate configuration choice, not a fault.
    `metadata` is passed through `redact_secrets` (§24.1) before being
    written, the same second layer `to_tool_error` applies to a client
    response.

    HR-015: an `OSError` from the filesystem operations below (an
    unwritable `audit.path`, a full disk, a permissions problem) is
    caught and reported to stderr rather than propagated — the caller's
    own result (a denial, or an already-completed Git operation) is what
    matters to the caller; a broken audit log is a real operational
    problem, but SHALL NOT itself become the response the client sees in
    place of the one that was actually computed.
    """
    if not config.enabled:
        return
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": None,
        "session_id": None,
        "workspace_id": workspace_id,
        "repository_id": repository_id,
        "operation": operation,
        "result": result,
        "metadata": redact_secrets(metadata) if metadata else {},
    }
    try:
        config.path.parent.mkdir(parents=True, exist_ok=True)
        with config.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        print(
            f"audit: failed to write {operation!r}/{result!r} event to {config.path}: {exc}",
            file=sys.stderr,
        )
