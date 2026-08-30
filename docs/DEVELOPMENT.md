# Development

## Setup

```bash
uv sync --group dev
git config core.hooksPath .githooks
```

The second line activates `.githooks/pre-commit` (GR-002,
`docs/GAP_SPEC.md`) — it runs `ruff format --check`/`ruff check`/`mypy
src` before every commit and blocks the commit if any fails, so a
divergent local toolchain (a stray `ruff`/`mypy` on `PATH`, a different
version than the one `uv run` resolves to) is caught here rather than in
CI. `.git/hooks/` itself is per-clone, not version-controlled, which is
why this is a one-time step rather than automatic — run it once per
clone, immediately after `uv sync`. Always run the commands below through
`uv run`, never a bare binary on `PATH`: the two can genuinely disagree
(a bare `ruff` once passed something the project-pinned version, what CI
actually runs, rejected — `docs/GAP_TODO.md` G1).

## Running checks

```bash
uv run pytest              # unit tests
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

All four SHALL pass before a commit, per `docs/TODO.md`'s task loop and
`docs/SPEC.md` §71 (CI). The `.githooks/pre-commit` hook above runs the
last three automatically; `pytest` is Docker-heavy and multi-minute, so
it stays a separate, manual step the hook does not run on every commit.

## Running the server locally

Build the default workspace image first — nothing in `serve` itself does
this for you, and a config naming an image that doesn't exist yet fails
at workspace-open time, not at startup:

```bash
docker build -t mcp-dev-ubuntu:24.04 docker/default/
```

Then write a config file. There is no bundled example file to copy —
`docs/CONFIGURATION.md` explains why, and `docs/SPEC.md` §52 has the full
worked example (repositories, `docker.default_image` matching the tag
built above, SSH/GitHub/resource blocks) to start from.

```bash
uv run dev-workspace-mcp serve --config <path>
```

`--config` fully validates the file against `docs/SPEC.md` §52's schema
(S2, `docs/TODO.md`) before the server starts — a malformed or invalid
config prints a clear `error: ...` message and exits nonzero rather than
serving on bad configuration (§73 step 1).

## Project layout

```
src/dev_workspace_mcp/   the package
tests/                   pytest, mirroring the package layout
docs/SPEC.md             specification — authoritative behavioral design
docs/TODO.md             task list, traced to SPEC §§ / FRs / ATs
schemas/tools.json        exported tool contract (from S13 onward)
```
