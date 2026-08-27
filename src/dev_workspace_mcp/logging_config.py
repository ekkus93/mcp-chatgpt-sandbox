"""Operational logging (H31, `docs/HARDENING_TODO.md`, HR-031) — the
standard `logging` module, configured once at daemon startup
(`cli.py`'s `serve()`).

Distinct from `audit.py`'s structured, one-JSON-record-per-operation
audit log (§24, client-operation-facing, always UTC-timestamped
machine-readable records): this is free-text operational visibility for
whoever is running the daemon — container lifecycle transitions,
ssh-agent lifecycle, reconciliation outcomes, policy denials, and
exceptions a `try`/`except` legitimately swallows rather than
propagates. Before this task, `getLogger` appeared nowhere in this
codebase — a failure in, for example, the job drain path
(`execution/async_runner.py`) was entirely invisible.
"""

from __future__ import annotations

import logging
from typing import Any

from dev_workspace_mcp.errors import redact_secrets


def configure_logging(level: str) -> None:
    """Called once, at daemon startup (`cli.py`'s `serve()`).
    `force=True`: `logging.basicConfig` is documented to silently do
    nothing if the root logger already has a handler — which would make
    this call a no-op in any process where something configured logging
    earlier (a test harness reusing a process across multiple `serve()`
    calls, for instance), leaving *that* level in effect instead of the
    one this specific config actually requests. `force=True` makes this
    call's own `level` win every time, matching what a caller passing it
    would reasonably expect."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def redacted_extra(details: dict[str, Any]) -> dict[str, Any]:
    """HR-031: "secrets SHALL pass through the same redaction as
    HR-013" — the identical `redact_secrets` (`errors.py`) already
    applied to a client-facing error and an audit record, reused here
    rather than a third redaction implementation for log messages
    specifically. Callers pass the result as a log call's `extra`
    mapping (or interpolate it into the message) instead of the raw
    `details` dict."""
    return redact_secrets(details)
