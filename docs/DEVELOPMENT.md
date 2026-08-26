# Development

## Setup

```bash
uv sync --group dev
```

## Running checks

```bash
uv run pytest              # unit tests
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

All four SHALL pass before a commit, per `docs/TODO.md`'s task loop and
`docs/SPEC.md` §71 (CI).

## Running the server locally

```bash
uv run dev-workspace-mcp serve --config <path>
```

`--config` currently only checks the file exists; full validation lands with
S2 (`docs/TODO.md`).

## Project layout

```
src/dev_workspace_mcp/   the package
tests/                   pytest, mirroring the package layout
docs/SPEC.md             specification — authoritative behavioral design
docs/TODO.md             task list, traced to SPEC §§ / FRs / ATs
schemas/tools.json        exported tool contract (from S13 onward)
```
