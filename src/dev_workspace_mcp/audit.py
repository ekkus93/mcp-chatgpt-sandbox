"""Audit event writing (§24) — the minimal slice S17 needs for "audit the
[idle] stop"; not the full audit surface.

`AuditConfig.path` (§52) is an append-only JSONL file — one §24-shaped
record per line, matching the recommended shape (`timestamp`,
`request_id`, `session_id`, `workspace_id`, `repository_id`, `operation`,
`result`, plus free-form `metadata`). §67/§68's `audit_event` SQLite
table (a queryable *index* over the same records, not a second copy of
the log) is deliberately not written here — building the dual-write and
a query surface over it is S29's job (§24's full event list, denial
auditing, etc.), not something to half-build as a side effect of idle
stop. `request_id`/`session_id` are always `None` for now: nothing in
the codebase threads either through to a tool call yet, so there is
nothing honest to put there before S29 adds that plumbing.
"""

from __future__ import annotations

import json
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
    config.path.parent.mkdir(parents=True, exist_ok=True)
    with config.path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
