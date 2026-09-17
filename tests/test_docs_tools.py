"""H33 (`docs/HARDENING_TODO.md`, HR-032): `docs/TOOLS.md`'s hand-maintained
tool table SHALL list exactly the server's actual tool surface — the
defect this task fixes was six structured Git tools shipping without ever
being added to it. Mirrors the same real-server pattern
`tests/test_schema_export.py` already uses, rather than only diffing
against the committed `schemas/tools.json` (itself already guarded
against drifting from the live server by that file's own tests) — this
test's failure mode is specifically "the table itself is wrong," so it
reads what the docs file actually says.

`.github/workflows/ci.yml`'s "docs/TOOLS.md drift check" step re-verifies
the same property against the raw committed files with `jq`/`grep`,
the same "pytest asserts it semantically, CI re-checks the raw artifact"
split `test_schema_export.py`'s own MCP contract check already
establishes.
"""

import json
import re
from pathlib import Path

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.server import create_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _REPO_ROOT / "docs" / "TOOLS.md"
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "tools.json"

_R5_DESCRIPTION_TOOLS = {
    "resource_inventory",
    "resource_prune_plan",
    "resource_prune_execute",
    "server_info",
}

#: The same shape `.github/workflows/ci.yml`'s `grep -oP` pattern matches:
#: a markdown table row whose first cell is a single backtick-quoted tool
#: name — `| `tool_name` | ... |`. RR-028 (`docs/REMEDIATION_SPEC.md`
#: §28): `[a-z0-9_]+`, not `[a-z_]+` — every character an actual tool
#: name can contain, not just the ones today's names happen to use, so a
#: future digit-containing name (still a correctly-formatted row) is
#: matched rather than silently excluded and reported as missing/stale.
_TOOL_ROW = re.compile(r"^\| `([a-z0-9_]+)` \|", re.MULTILINE)
_TOOL_PURPOSE_ROW = re.compile(r"^\| `([a-z0-9_]+)` \| (.*) \|$", re.MULTILINE)


def _documented_tool_purposes() -> dict[str, str]:
    return dict(_TOOL_PURPOSE_ROW.findall(_DOCS_PATH.read_text()))


def _schema_tool_descriptions() -> dict[str, str]:
    document = json.loads(_SCHEMA_PATH.read_text())
    return {str(tool["name"]): str(tool["description"]) for tool in document["tools"]}


def _description_drift(
    registered: dict[str, str],
    schema: dict[str, str],
    documented: dict[str, str],
) -> dict[str, str]:
    drift: dict[str, str] = {}
    for name in sorted(_R5_DESCRIPTION_TOOLS):
        live_description = registered.get(name)
        schema_description = schema.get(name)
        docs_purpose = documented.get(name)
        if live_description is None:
            drift[name] = "tool is not registered"
        elif schema_description != live_description:
            drift[name] = "exported schema description differs from registered description"
        elif docs_purpose is None:
            drift[name] = "docs/TOOLS.md row is missing"
        elif not docs_purpose.startswith(live_description):
            drift[name] = "docs/TOOLS.md purpose does not begin with registered description"
    return drift


def _documented_tool_names() -> set[str]:
    return set(_TOOL_ROW.findall(_DOCS_PATH.read_text()))


async def test_every_registered_tool_is_documented(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    registered = {t.name for t in await server.list_tools()}
    documented = _documented_tool_names()
    missing = registered - documented
    assert not missing, f"tool(s) registered but not in docs/TOOLS.md: {sorted(missing)}"


async def test_every_documented_tool_is_actually_registered(minimal_config: ServerConfig) -> None:
    """The other direction — a stale row left behind after a tool is
    renamed or removed is just as much a drift as a missing one."""
    server = create_server(minimal_config)
    registered = {t.name for t in await server.list_tools()}
    documented = _documented_tool_names()
    stale = documented - registered
    assert not stale, f"tool(s) in docs/TOOLS.md but not actually registered: {sorted(stale)}"


def test_the_regex_actually_matches_something() -> None:
    """A regex that silently matches zero rows would make both tests
    above vacuously pass — this is what H30's own "coverage checks SHALL
    NOT satisfy themselves" lesson (HR-027) looks like applied here."""
    assert len(_documented_tool_names()) >= 20  # 21 as of H33; >= tolerates a future addition


def test_rr028_a_tool_name_containing_a_digit_is_matched() -> None:
    """RR-028: the old `[a-z_]+` pattern excludes a digit — a future
    tool name containing one is still a correctly-formatted row, but
    would silently fail to match, making the two round-trip tests above
    report it as missing/stale rather than correctly reconciled. Not
    hypothetical: `.github/workflows/ci.yml`'s `grep -oP` pattern
    mirrors this one exactly (this file's own docstring) and would fail
    the same way on a real CI run."""
    sample = "| `git_oauth2_refresh` | some description |\n"
    assert _TOOL_ROW.findall(sample) == ["git_oauth2_refresh"]


# Covers: PMORRAT-005
async def test_r5_tool_description_docs_drift_is_detected(
    minimal_config: ServerConfig,
) -> None:
    """PMORRAT-005: live descriptions, exported schema descriptions, and
    the human tool table must agree for the four operations-review tools.

    `docs/TOOLS.md` may retain richer explanatory text after the registered
    description, but the public description itself must appear verbatim at
    the start of the purpose cell. A controlled mutation proves this check
    does not satisfy itself merely because all current files agree.
    """
    server = create_server(minimal_config)
    registered = {
        tool.name: tool.description or ""
        for tool in await server.list_tools()
        if tool.name in _R5_DESCRIPTION_TOOLS
    }
    schema = _schema_tool_descriptions()
    documented = _documented_tool_purposes()

    drift = _description_drift(registered, schema, documented)
    assert not drift, f"operations tool description/docs drift: {drift}"

    controlled_docs = dict(documented)
    controlled_docs["resource_inventory"] = "stale controlled purpose"
    controlled_drift = _description_drift(registered, schema, controlled_docs)
    assert controlled_drift == {
        "resource_inventory": "docs/TOOLS.md purpose does not begin with registered description"
    }
