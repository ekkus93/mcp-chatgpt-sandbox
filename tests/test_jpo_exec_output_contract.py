"""JPO-001 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §3 D2-A, I9; JPOV-001,
JPOV-008): `exec`'s output is ONE flat object expressing §14.4's union.

Read from the real server, not a probe server: the two shapes that were
rejected (a plain union return, a discriminated `RootModel`) both looked
fine as Python and only showed their defect in what the MCP SDK actually
exported — a `{"result": ...}` wrapper, and a schema with no top-level
`"type": "object"`.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.schema_export import collect_tool_schemas
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools.execution_models import ExecOutput

_EXISTING_FIELDS = {
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "stdout_truncated",
    "stderr_truncated",
    "stdout_total_bytes",
    "stderr_total_bytes",
    "pressure",
}


def _completed() -> ExecOutput:
    return ExecOutput.completed(
        exit_code=0,
        stdout_tail="out",
        stderr_tail="",
        stdout_truncated=False,
        stderr_truncated=False,
        stdout_total_bytes=3,
        stderr_total_bytes=0,
        pressure=None,
    )


def _promoted() -> ExecOutput:
    return ExecOutput.promoted(job_id="job_1", state="running", started_at="2026-09-18T00:00:00Z")


async def _exec_output_schema(config: ServerConfig) -> dict[str, Any]:
    schemas = await collect_tool_schemas(create_server(config))
    (schema,) = [s["output_schema"] for s in schemas if s["name"] == "exec"]
    assert isinstance(schema, dict)
    return schema


async def test_jpov001_exec_output_schema_is_a_flat_object_with_a_two_branch_one_of(
    minimal_config: ServerConfig,
) -> None:
    schema = await _exec_output_schema(minimal_config)

    assert schema["type"] == "object"
    # Flat: `exit_code` is a top-level property, not under a `result` wrapper.
    assert "result" not in schema["properties"]
    assert set(schema["properties"]) >= _EXISTING_FIELDS
    # Nothing that was required stopped being required (D2-A).
    assert set(schema["required"]) >= _EXISTING_FIELDS | {"promoted_to_job"}
    discriminators = [
        branch["properties"]["promoted_to_job"]["const"] for branch in schema["oneOf"]
    ]
    assert discriminators == [False, True]


async def test_jpov001_each_shape_matches_exactly_one_branch_of_the_exported_schema(
    minimal_config: ServerConfig,
) -> None:
    """A `oneOf` whose branches only said `required: [...]` would match BOTH
    branches for every value (unused fields are present as `null`), and a
    `oneOf` that matches twice rejects everything — so validate the values
    the server would really send against the schema it really exports."""
    schema = await _exec_output_schema(minimal_config)

    jsonschema.validate(_completed().model_dump(mode="json"), schema)
    jsonschema.validate(_promoted().model_dump(mode="json"), schema)

    mixed = _promoted().model_dump(mode="json") | {"exit_code": 0}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(mixed, schema)


def test_jpov008_promoted_without_a_job_id_is_rejected() -> None:
    fields = _promoted().model_dump() | {"job_id": None}
    with pytest.raises(ValidationError, match="job_id"):
        ExecOutput(**fields)


def test_jpov008_completed_without_an_exit_code_is_rejected() -> None:
    fields = _completed().model_dump() | {"exit_code": None}
    with pytest.raises(ValidationError, match="exit_code"):
        ExecOutput(**fields)


def test_jpov008_fields_from_both_branches_are_rejected() -> None:
    fields = _completed().model_dump() | {"job_id": "job_1", "state": "running"}
    with pytest.raises(ValidationError, match="job_id"):
        ExecOutput(**fields)


def test_jpov008_a_promoted_handle_may_not_carry_a_pressure_diagnostic_or_tails() -> None:
    fields = _promoted().model_dump() | {"stdout_tail": "leftover"}
    with pytest.raises(ValidationError, match="stdout_tail"):
        ExecOutput(**fields)
