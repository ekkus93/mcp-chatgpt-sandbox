"""Exports the server's `tools/list` response to `schemas/tools.json`
(§33.1) — the authoritative tool contract. `docs/SPEC.md` §62-§66's JSON
examples are illustrative; this file, generated from the running server
rather than hand-maintained, is what governs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

#: Bumped by hand per §33.2's rules (patch/minor/major) — not derived from
#: the schema content itself, since "what changed" is a judgment call
#: (a loosened constraint is a patch; a tightened one is a breaking major)
#: that a diff cannot make on its own.
#:
#: 0.2.0 (S15): minor bump — four new tools (`exec_start`, `exec_status`,
#: `exec_output`, `exec_cancel`), purely additive.
#: 0.2.1 (S16): patch bump — `exec_output` gained one new output field,
#: `logs_pruned` (§15.6's retention flag).
#: 0.3.0 (S20): minor bump — one new tool, `git_status`.
#: 0.4.0 (S21): minor bump — two new tools, `git_diff` and `git_log`.
#: 0.5.0 (S22): minor bump — one new tool, `git_commit`.
#: 0.6.0 (S26): minor bump — one new tool, `git_pull`.
CONTRACT_VERSION = "0.6.0"


async def collect_tool_schemas(server: MCPServer) -> list[dict[str, Any]]:
    """Every registered tool's name, description, and input/output JSON
    Schema, sorted by name for a stable, reviewable diff."""
    tools = await server.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
        for tool in sorted(tools, key=lambda t: t.name)
    ]


def render_schema_document(
    tool_schemas: list[dict[str, Any]], *, contract_version: str = CONTRACT_VERSION
) -> str:
    """The full `schemas/tools.json` content: stable key ordering and a
    trailing newline, so re-exporting an unchanged server byte-for-byte
    matches what is already committed (§71's CI check depends on this)."""
    document = {"contract_version": contract_version, "tools": tool_schemas}
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


async def export_schemas(
    server: MCPServer, out_path: Path, *, contract_version: str = CONTRACT_VERSION
) -> str:
    """Writes the schema document to `out_path` (creating parent
    directories as needed) and returns the content written."""
    tool_schemas = await collect_tool_schemas(server)
    content = render_schema_document(tool_schemas, contract_version=contract_version)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    return content
