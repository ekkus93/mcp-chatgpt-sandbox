"""Public output models for synchronous and background execution tools."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_chatgpt_sandbox.operations.pressure import PressureDiagnostic


@dataclass
class ExecOutput:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_total_bytes: int
    stderr_total_bytes: int
    pressure: PressureDiagnostic | None


@dataclass
class ExecStartOutput:
    job_id: str
    state: str
    started_at: str | None


@dataclass
class ExecStatusOutput:
    job_id: str
    state: str
    exit_code: int | None
    elapsed_seconds: float
    stdout_tail: str
    stderr_tail: str
    stdout_total_bytes: int
    stderr_total_bytes: int
    output_truncated: bool


@dataclass
class ExecOutputOutput:
    job_id: str
    stream: str
    content: str
    stream_total_bytes: int
    """§15.6's uncapped total — how much the job actually produced. NOT
    what `byte_start`/`byte_end` index into once a stream has ever been
    truncated on disk (see `stored_bytes`) — the two used to be silently
    conflated (H29, `docs/HARDENING_TODO.md`, HD3)."""
    stored_bytes: int
    """What's actually retrievable from this endpoint — `byte_start`/
    `byte_end` index into this, not `stream_total_bytes`."""
    truncated: bool
    logs_pruned: bool


@dataclass
class ExecCancelOutput:
    job_id: str
    state: str
