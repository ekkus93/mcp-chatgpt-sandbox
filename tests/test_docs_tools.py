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

import re
from pathlib import Path

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.server import create_server

_DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "TOOLS.md"

#: The same shape `.github/workflows/ci.yml`'s `grep -oP` pattern matches:
#: a markdown table row whose first cell is a single backtick-quoted tool
#: name — `| `tool_name` | ... |`. RR-028 (`docs/REMEDIATION_SPEC.md`
#: §28): `[a-z0-9_]+`, not `[a-z_]+` — every character an actual tool
#: name can contain, not just the ones today's names happen to use, so a
#: future digit-containing name (still a correctly-formatted row) is
#: matched rather than silently excluded and reported as missing/stale.
_TOOL_ROW = re.compile(r"^\| `([a-z0-9_]+)` \|", re.MULTILINE)


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
