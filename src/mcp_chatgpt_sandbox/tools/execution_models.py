"""Public output models for synchronous and background execution tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.config import JsonDict

from mcp_chatgpt_sandbox.operations.pressure import PressureDiagnostic

_COMPLETED_FIELDS = (
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "stdout_truncated",
    "stderr_truncated",
    "stdout_total_bytes",
    "stderr_total_bytes",
)
_PROMOTED_FIELDS = ("job_id", "state")
_NULL = {"type": "null"}


def _exec_output_branches() -> list[dict[str, Any]]:
    """The two `oneOf` branches of `exec`'s output (JPO-001, D2-A).

    Each branch pins the non-null type of its own fields AND `null` for the
    other branch's. Pinning only `required` would say nothing: every field
    is always present (unused ones serialise as `null`), so
    `required: ["job_id"]` is satisfied by `job_id: null` and both branches
    would match every value — a `oneOf` that matches twice is a schema that
    rejects everything (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §2.5).
    `pressure` and `started_at` are legitimately nullable on their own
    branch, so they are only pinned to `null` on the other.
    """
    completed_types: dict[str, Any] = {
        "exit_code": {"type": "integer"},
        "stdout_tail": {"type": "string"},
        "stderr_tail": {"type": "string"},
        "stdout_truncated": {"type": "boolean"},
        "stderr_truncated": {"type": "boolean"},
        "stdout_total_bytes": {"type": "integer"},
        "stderr_total_bytes": {"type": "integer"},
    }
    promoted_types: dict[str, Any] = {"job_id": {"type": "string"}, "state": {"type": "string"}}
    return [
        {
            "title": "ExecCompleted",
            "properties": {
                "promoted_to_job": {"const": False},
                **completed_types,
                **dict.fromkeys((*_PROMOTED_FIELDS, "started_at"), _NULL),
            },
        },
        {
            "title": "ExecPromotedToJob",
            "properties": {
                "promoted_to_job": {"const": True},
                **promoted_types,
                **dict.fromkeys((*_COMPLETED_FIELDS, "pressure"), _NULL),
            },
        },
    ]


class ExecOutput(BaseModel):
    """`exec`'s two response shapes in one flat object (§14.4; JPO-001).

    D2-A (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §3): a completed result,
    or — `promoted_to_job: true` — a job handle identical to `exec_start`'s.

    Why not the obvious `-> ExecOutput | ExecStartOutput`: the MCP SDK wraps
    a union return as `{"result": ...}`, which would move `exit_code` to
    `result.exit_code` for every existing client. Why not a discriminated
    `RootModel`: its exported schema is a bare top-level `oneOf` with no
    `"type": "object"`, which the MCP `Tool.outputSchema` type requires and
    every other tool here has. Both were probed against the real SDK before
    this shape was chosen (spec §2.5).

    Every field is *required and nullable* rather than optional: dropping a
    name from `required` is a removal from a consumer's point of view, while
    a `null` that only a caller who opted into promotion can ever receive is
    not.

    The SDK does not validate a returned value against the `oneOf` — a probe
    returned `promoted_to_job: true` with no `job_id` without complaint — so
    `_exactly_one_branch` enforces it here (I9).
    """

    model_config = ConfigDict(
        json_schema_extra=cast("JsonDict", {"oneOf": _exec_output_branches()})
    )

    promoted_to_job: bool
    exit_code: int | None
    stdout_tail: str | None
    stderr_tail: str | None
    stdout_truncated: bool | None
    stderr_truncated: bool | None
    stdout_total_bytes: int | None
    stderr_total_bytes: int | None
    pressure: PressureDiagnostic | None
    job_id: str | None
    state: str | None
    started_at: str | None

    @model_validator(mode="after")
    def _exactly_one_branch(self) -> ExecOutput:
        own, other = (
            (_PROMOTED_FIELDS, (*_COMPLETED_FIELDS, "pressure"))
            if self.promoted_to_job
            else (_COMPLETED_FIELDS, (*_PROMOTED_FIELDS, "started_at"))
        )
        missing = [name for name in own if getattr(self, name) is None]
        foreign = [name for name in other if getattr(self, name) is not None]
        if missing or foreign:
            raise ValueError(
                f"exec output with promoted_to_job={self.promoted_to_job} "
                f"is missing {missing} and must not set {foreign}"
            )
        return self

    @classmethod
    def completed(
        cls,
        *,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        stdout_total_bytes: int,
        stderr_total_bytes: int,
        pressure: PressureDiagnostic | None,
    ) -> ExecOutput:
        return cls(
            promoted_to_job=False,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_total_bytes=stdout_total_bytes,
            stderr_total_bytes=stderr_total_bytes,
            pressure=pressure,
            job_id=None,
            state=None,
            started_at=None,
        )

    @classmethod
    def promoted(cls, *, job_id: str, state: str, started_at: str | None) -> ExecOutput:
        return cls(
            promoted_to_job=True,
            exit_code=None,
            stdout_tail=None,
            stderr_tail=None,
            stdout_truncated=None,
            stderr_truncated=None,
            stdout_total_bytes=None,
            stderr_total_bytes=None,
            pressure=None,
            job_id=job_id,
            state=state,
            started_at=started_at,
        )


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
