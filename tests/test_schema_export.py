"""S13: tool contract export (§33.1)."""

import json
from pathlib import Path

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.schema_export import (
    collect_tool_schemas,
    export_schemas,
    render_schema_document,
)
from mcp_chatgpt_sandbox.server import create_server


async def test_collect_tool_schemas_covers_every_registered_tool(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tools = await server.list_tools()
    schemas = await collect_tool_schemas(server)
    assert {s["name"] for s in schemas} == {t.name for t in tools}


async def test_collect_tool_schemas_is_sorted_by_name(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    schemas = await collect_tool_schemas(server)
    names = [s["name"] for s in schemas]
    assert names == sorted(names)


async def test_each_schema_has_input_and_output_schema(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    schemas = await collect_tool_schemas(server)
    for schema in schemas:
        assert schema["input_schema"]["type"] == "object"
        assert schema["output_schema"] is not None


def test_render_schema_document_is_stable_json_with_trailing_newline() -> None:
    schemas = [{"name": "b", "description": None, "input_schema": {}, "output_schema": {}}]
    content = render_schema_document(schemas, contract_version="1.2.3")
    assert content.endswith("\n")
    document = json.loads(content)
    assert document["contract_version"] == "1.2.3"
    assert document["tools"] == schemas


def test_render_schema_document_is_deterministic_across_calls() -> None:
    schemas = [{"name": "a", "description": "x", "input_schema": {}, "output_schema": {}}]
    assert render_schema_document(schemas) == render_schema_document(schemas)


async def test_export_schemas_writes_the_file(minimal_config: ServerConfig, tmp_path: Path) -> None:
    server = create_server(minimal_config)
    out_path = tmp_path / "schemas" / "tools.json"
    content = await export_schemas(server, out_path)

    assert out_path.exists()
    assert out_path.read_text() == content
    document = json.loads(content)
    assert "tools" in document
    assert len(document["tools"]) > 0


async def test_export_schemas_creates_parent_directories(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    server = create_server(minimal_config)
    out_path = tmp_path / "a" / "b" / "c" / "tools.json"
    await export_schemas(server, out_path)
    assert out_path.exists()


async def test_reexporting_an_unchanged_server_is_byte_for_byte_identical(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """The property the CI check in §71 depends on."""
    server_a = create_server(minimal_config)
    server_b = create_server(minimal_config)
    content_a = await export_schemas(server_a, tmp_path / "a.json")
    content_b = await export_schemas(server_b, tmp_path / "b.json")
    assert content_a == content_b


async def test_committed_schemas_file_matches_a_fresh_export(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """Exactly what CI checks (§71): the committed `schemas/tools.json`
    must match what the current server actually produces."""
    committed_path = Path(__file__).resolve().parents[1] / "schemas" / "tools.json"
    committed = committed_path.read_text()

    server = create_server(minimal_config)
    fresh = await export_schemas(server, tmp_path / "fresh.json")

    committed_document = json.loads(committed)
    fresh_document = json.loads(fresh)
    assert fresh_document == committed_document, (
        "schemas/tools.json is stale — run "
        "`mcp-chatgpt-sandbox export-schemas --config <path>` and commit the result"
    )
