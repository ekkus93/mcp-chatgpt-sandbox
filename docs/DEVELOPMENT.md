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

Requires Python 3.12 (`.python-version`) and Docker — the test suite is
Docker-heavy (`docs/SPEC.md` §71), and a locally running server needs it
for every workspace operation.

## Dependencies

Runtime: `mcp`, `docker`, `pydantic`, `pyyaml`, `click`. Dev-only (`uv
sync --group dev`): `pytest`, `pytest-cov`, `pytest-asyncio`, `ruff`,
`mypy`, plus `types-pyyaml`/`types-docker` for `mypy --strict`. The
authoritative list is `pyproject.toml`, not this page — this is a map of
what's there and why, not a copy kept in sync by hand.

## Running checks

```bash
uv run pytest              # unit + integration tests
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

All four SHALL pass before a commit, per `docs/TODO.md`'s task loop and
`docs/SPEC.md` §71 (CI). The `.githooks/pre-commit` hook above runs the
last three automatically; `pytest` is Docker-heavy and multi-minute, so
it stays a separate, manual step the hook does not run on every commit.

`mypy` runs in `strict` mode against `src/` only (`tests/` is excluded —
see `[tool.mypy]` in `pyproject.toml`); `ruff` runs the `E`, `F`, `I`,
`UP`, `B`, `SIM`, `C4` rule sets at a 100-column line length. CI
(`.github/workflows/ci.yml`) runs the same four checks plus two that
aren't practical to run on every local commit: an MCP contract check
(`export-schemas` output must match the committed `schemas/tools.json`
byte-for-byte, §33.1/§71) and a `contract_version` bump check (§33.2 —
if the tool schemas changed since the base commit, `contract_version`
SHALL have changed too).

### Test layout and coverage

```bash
uv run pytest --cov --cov-report=term-missing   # what CI runs
```

`tests/` mirrors `src/dev_workspace_mcp/`'s package layout (`config`,
`db`, `execution`, `filesystem`, `git`, `jobs`, `tools`, `workspace`),
plus cross-cutting top-level files (`test_cli.py`, `test_errors.py`,
`test_server.py`, `test_schema_export.py`, `test_audit.py`, and others)
for behavior that doesn't belong to a single subpackage. Coverage is
scoped to `src/dev_workspace_mcp` (`[tool.coverage.run]`); several tests
build and run real Docker containers, which is what makes the full suite
Docker-heavy and multi-minute rather than something to run on every
keystroke.

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
built above, SSH/GitHub/resource blocks) to start from. To register one
of your own repositories in it — generating a deploy key, installing it
on GitHub, adding the `repositories:` entry — see
[`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).

```bash
uv run dev-workspace-mcp serve --config <path>
```

`--config` fully validates the file against the Pydantic schema in
`src/dev_workspace_mcp/config/models.py` (S2, `docs/TODO.md`) before the
server starts — a malformed or invalid config prints a clear `error: ...`
message and exits nonzero rather than serving on bad configuration (§73
step 1).

This runs the server on `stdio` (the default `--transport`), talking to
whatever spawns it directly — a local MCP client, or `tunnel-client` if
you're connecting a hosted client. See
[`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md) to connect ChatGPT
specifically.

### Troubleshooting

- **`serve` fails immediately with a config `error: ...`.** The message
  names the offending field — check it against
  [`docs/CONFIGURATION.md`](CONFIGURATION.md) and `docs/SPEC.md` §52;
  `state_db_path`, `docker.default_image`, `ssh.keys_dir`,
  `ssh.known_hosts`, and `audit.path` have no default and must be set
  explicitly.
- **`workspace_open` fails with an image error.** The `docker build`
  step above was skipped, or `docker.default_image` in your config
  doesn't match the tag you built.
- **A committed `schemas/tools.json` diff fails CI but not local
  `pytest`.** Regenerate it: `uv run dev-workspace-mcp export-schemas
  --config <path> --out schemas/tools.json`, and bump
  `contract_version` (§33.2) if the tool surface itself changed, not
  just formatting.
- **`ruff`/`mypy` pass locally but the pre-commit hook still blocks the
  commit.** Confirm `git config core.hooksPath .githooks` was actually
  run in this clone (§ Setup above) — a bare `ruff`/`mypy` on `PATH` can
  disagree with the `uv run`-resolved version the hook and CI both use.

## Project layout

```
src/dev_workspace_mcp/   the package
tests/                   pytest, mirroring the package layout
docs/SPEC.md             specification — authoritative behavioral design
docs/TODO.md             task list, traced to SPEC §§ / FRs / ATs
schemas/tools.json        exported tool contract (from S13 onward)
```
