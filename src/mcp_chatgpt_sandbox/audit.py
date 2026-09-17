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
designed to avoid for its own scope. Request/session and workspace/
repository attribution is optional at this layer: callers that can
provide bounded, non-secret identifiers thread them through explicitly,
while unsafe or older caller values fail closed to `None`.

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
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from mcp_chatgpt_sandbox.config.models import AuditConfig
from mcp_chatgpt_sandbox.errors import looks_like_secret_value, redact_secrets


# G3R-012: process-lifetime health for the configured audit sink. Audit
# remains best-effort for already-completed operations; a failed write makes
# the sink observably degraded until a later write succeeds. Last-failure
# details remain available after recovery for operator diagnostics.
@dataclass(frozen=True)
class AuditHealth:
    status: Literal["healthy", "degraded"]
    mode: Literal["best_effort", "disabled"]
    last_failure_at: str | None
    last_failure_reason: str | None


_AUDIT_HEALTH_LOCK = threading.Lock()
_AUDIT_HEALTH_BY_PATH: dict[str, AuditHealth] = {}
_AUDIT_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _safe_audit_identifier(value: str | None) -> str | None:
    """Return a bounded audit identifier, or drop unsafe values.

    Audit attribution is useful only when it is also safe to persist. The
    audit writer therefore accepts only short opaque identifiers made from
    this project's non-path ID alphabet. Raw headers, prompts,
    environment values, paths, shell fragments, free-form client metadata,
    and pure credential/token-shaped strings fail closed to `None` instead
    of being copied into JSONL.
    """
    if value is None:
        return None
    if _AUDIT_SAFE_ID_RE.fullmatch(value) is None:
        return None
    if looks_like_secret_value(value):
        return None
    return value


def _audit_health_key(config: AuditConfig) -> str:
    return str(config.path)


def _healthy_state(
    *,
    mode: Literal["best_effort", "disabled"],
    previous: AuditHealth | None = None,
) -> AuditHealth:
    return AuditHealth(
        status="healthy",
        mode=mode,
        last_failure_at=previous.last_failure_at if previous is not None else None,
        last_failure_reason=previous.last_failure_reason if previous is not None else None,
    )


def get_audit_health(config: AuditConfig) -> AuditHealth:
    """Return secret/path-safe process-lifetime audit health for `config`."""
    if not config.enabled:
        return _healthy_state(mode="disabled")
    key = _audit_health_key(config)
    with _AUDIT_HEALTH_LOCK:
        return _AUDIT_HEALTH_BY_PATH.get(key, _healthy_state(mode="best_effort"))


def _audit_failure_reason(exc: OSError) -> str:
    """Describe an audit filesystem failure without echoing filesystem paths."""
    if exc.errno is None:
        return type(exc).__name__
    try:
        description = os.strerror(exc.errno)
    except ValueError:
        description = "OS error"
    return f"{type(exc).__name__} (errno {exc.errno}: {description})"


def _mark_audit_failure(config: AuditConfig, exc: OSError) -> AuditHealth:
    health = AuditHealth(
        status="degraded",
        mode="best_effort",
        last_failure_at=datetime.now(UTC).isoformat(),
        last_failure_reason=_audit_failure_reason(exc),
    )
    with _AUDIT_HEALTH_LOCK:
        _AUDIT_HEALTH_BY_PATH[_audit_health_key(config)] = health
    return health


def _mark_audit_success(config: AuditConfig) -> None:
    key = _audit_health_key(config)
    with _AUDIT_HEALTH_LOCK:
        previous = _AUDIT_HEALTH_BY_PATH.get(key)
        _AUDIT_HEALTH_BY_PATH[key] = _healthy_state(mode="best_effort", previous=previous)


def write_audit_event(
    config: AuditConfig,
    *,
    operation: str,
    result: str,
    workspace_id: str | None = None,
    repository_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Appends one JSON line to `config.path`. A no-op when auditing is
    disabled (`audit.enabled: false`) — not an error, since disabling
    audit logging is a deliberate configuration choice, not a fault.
    `metadata` is passed through `redact_secrets` (§24.1) before being
    written, the same second layer `to_tool_error` applies to a client
    response. `request_id`, `session_id`, `workspace_id`, and
    `repository_id` are optional attribution fields and are accepted only
    when they are bounded, opaque, non-path, non-secret audit identifiers.

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
        "request_id": _safe_audit_identifier(request_id),
        "session_id": _safe_audit_identifier(session_id),
        "workspace_id": _safe_audit_identifier(workspace_id),
        "repository_id": _safe_audit_identifier(repository_id),
        "operation": operation,
        "result": result,
        "metadata": redact_secrets(metadata) if metadata else {},
    }
    try:
        config.path.parent.mkdir(parents=True, exist_ok=True)
        with config.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        health = _mark_audit_failure(config, exc)
        print(
            f"audit: failed to write {operation!r}/{result!r} event: {health.last_failure_reason}",
            file=sys.stderr,
        )
    else:
        _mark_audit_success(config)
