"""Exports the server's `tools/list` response to `schemas/tools.json`
(§33.1) — the authoritative tool contract. `docs/SPEC.md` §62-§66's JSON
examples are illustrative; this file, generated from the running server rather
than hand-maintained, is what governs.
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
#: 0.7.0 (S27): minor bump — one new tool, `git_push`.
#: 0.7.1 (S28): patch bump — `workspace_open`/`workspace_info`'s
#: `remote_protection` and `git_push`'s new `remote_protection` field
#: gained the full §20.4 shape (previously just a bare `status` string);
#: no new tools.
#: 0.7.2 (S30): patch bump — `workspace_open`/`workspace_info` gained
#: `image`, `image_digest`, `config_version`, `created_at` (§9.2); no new
#: tools.
#: 0.7.3 (H24, `docs/HARDENING_TODO.md`): patch bump — `git_diff` gained
#: `next_offset` (character-boundary-safe pagination); no new tools.
#: 0.7.4 (H29): patch bump — `exec_output` gained `stored_bytes`
#: (distinct from the uncapped `stream_total_bytes`, §15.6); no new
#: tools.
#: 0.7.5 (H32): patch bump — `workspace_open`/`workspace_info` gained
#: `oom_killed` (§26) and `devcontainer_ignored` (§30); no new tools.
#: 0.7.6 (G2R-014, `docs/GAP2_SPEC.md`): patch bump — `workspace_open`/
#: `workspace_info` gained `configured_image` (what `.mcp-workspace.yml`
#: `image:`/`docker.default_image` currently resolves to, distinct from
#: `image`, what the container actually runs); no new tools.
#: 0.7.7 (G3R-012, `docs/GAP3_SPEC.md`): patch bump — `server_info` gained
#: `audit`, exposing best-effort audit sink health, last failure time, and a
#: sanitized last failure reason; no new tools.
#: 0.7.8 (G3R-014, `docs/GAP3_SPEC.md`): patch bump — `server_info` gained
#: `maintenance`, exposing scheduler state, last cycle, and sanitized last
#: maintenance failure information; no new tools.
#: 0.8.0 (Post-MVP Operations O3): minor bump — one new additive read-only
#: tool, `resource_inventory`, with bounded opaque-ID filtering and explicit
#: cleanup-debt/degraded/unavailable observations.
#: 0.8.1 (Post-MVP Operations O4): patch bump — `exec` gained additive
#: evidence-based pressure context and `resource_inventory` gained optional
#: cgroup-v2 PID-pressure observation; no new tools.
#: 0.9.0 (Post-MVP Operations O7): minor bump — one new additive dry-run-only
#: tool, `resource_prune_plan`, exposing immutable plan tokens and a bounded,
#: path-free projection of positively owned reclamation candidates.
#: 0.10.0 (Post-MVP Operations O8): minor bump — one new additive destructive
#: tool, `resource_prune_execute`, requiring an existing immutable plan token
#: plus explicit confirmation and returning bounded per-candidate outcomes.
#: 0.10.1 (Post-MVP Operations O9): patch bump — reclamation contracts gained
#: durable `ssh_agent_cleanup` retry candidates, cleanup identity binding, and
#: aggregate execution status; no new tools.
#: 0.10.2 (Post-MVP Operations O10): patch bump — `server_info` gained an
#: additive `operations` health summary for bounded inventory degradation,
#: state-filesystem pressure/availability, and cgroup-v2 PID pressure;
#: no new tools.
CONTRACT_VERSION = "0.10.2"


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
    content = render_schema_document(
        await collect_tool_schemas(server),
        contract_version=contract_version,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return content
