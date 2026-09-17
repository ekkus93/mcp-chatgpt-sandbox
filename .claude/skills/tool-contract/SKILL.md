---
name: tool-contract
description: Complete the three CI-gated steps required when the MCP tool surface changes — regenerate schemas/tools.json, decide the contract_version bump, and update the docs/TOOLS.md row. Use when adding, removing, or changing the name, description, or input schema of an MCP tool.
---

# MCP tool contract changes

The public tool surface is pinned by three artifacts that CI verifies **separately**. Changing
a tool and updating only one of them leaves the build red in a way that looks unrelated to the
change. Do all three.

This applies whenever a tool is added or removed, or its name, description, or input schema
changes. It does not apply to a change in a tool's internal implementation that leaves the
registered schema byte-identical — but verify that with Step 1 rather than assuming it.

## Step 1: regenerate the exported schema

```bash
uv run dev-workspace-mcp export-schemas --config <path> --out schemas/tools.json
```

CI diffs the generator's output against the committed file byte-for-byte, so the file must be
the generator's own output — never hand-edit it. `git diff schemas/tools.json` afterwards:

- no diff → the registered surface did not actually change; Steps 2 and 3 do not apply.
- a diff → continue, and read it. It tells you exactly which tools changed, which drives the
  next two steps.

## Step 2: decide the contract_version

CI enforces two things: if any tool schema changed in the commit then `contract_version` must
also have changed, and the value must increase monotonically (compared with `sort -V`).

Choose deliberately from the diff in Step 1 and state the reasoning in the commit message:

- **Patch** — a description or docs-facing wording change; no client that worked before can
  break.
- **Minor** — a new tool, or a new optional field on an existing one; existing calls still
  valid.
- **Major** — a removed or renamed tool, a removed or renamed field, a field becoming
  required, or a type narrowing. Any existing client call can now fail.

Ask the user before committing a major bump — it breaks connected clients.

## Step 3: update docs/TOOLS.md

The table there is hand-maintained and gated bidirectionally against the live tool surface by
`tests/test_docs_tools.py`: a registered tool with no row fails, and a row for a tool that is
no longer registered also fails.

Each row's purpose text must **begin with** the tool's registered description; it may then
continue with richer explanatory prose. So when a description changes, the row's opening text
has to change with it.

## Step 4: verify

```bash
uv run pytest tests/test_schema_export.py tests/test_docs_tools.py
```

Then run the full gates (`/gates`) before committing — the contract checks are only part of
what CI runs.
