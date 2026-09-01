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

`core.hooksPath` is a single directory, not a search chain — if you
already have your own global hooks configured
(`git config --global core.hooksPath`), setting it repo-locally would
otherwise shadow them, since git stops consulting the global value the
moment a local one exists (G2R-007, `docs/GAP2_SPEC.md`).
`.githooks/commit-msg`, `post-checkout`, `post-commit`, `post-merge`, and
`pre-push` are symlinks to `.githooks/_global-hook-dispatch`, which
re-reads your global `core.hooksPath` directly and forwards to the
same-named global hook when present.

`pre-commit` has an explicit same-name policy (G3R-011/G3AT-011,
`docs/GAP3_SPEC.md`): **the repository's local pre-commit runs first; if
and only if its Ruff/mypy checks pass, the developer's global
`pre-commit` runs second.** A failure from either hook blocks the commit.
The local hook tears down its temporary index-snapshot worktree, returns
to the real repository, and restores Git's hook environment before
chaining, so the global hook does not run inside the scratch worktree.
A clone with no global `pre-commit` configured simply finishes after the
local checks.

This forwarding is intentionally explicit, not a claim that
`core.hooksPath` became a generic search chain. The proxy files preserve
the five global hook names listed above, and the local `pre-commit`
explicitly composes with a global `pre-commit`; an arbitrary global hook
name for which `.githooks/` has no corresponding proxy is still not
discovered by Git.

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

### Run `uv run pytest` with no concurrent Docker activity

G2R-022 (`docs/GAP2_SPEC.md`): `tests/workspace/`'s workspace/container
names are deterministic (`persistent_workspace_id` is a pure function of
`repository_id` — §59's "reopening a repository always resolves to the
same workspace" is the point, not a bug to route around here) and every
one of these fixtures uses the same `repository_id` (`"example"`). Two
processes racing to open a workspace for the same repository id — a
second concurrent `pytest tests/workspace/` run, a second reviewer's
suite, a live `serve` instance already running against a same-id
repository, or (on a shared self-hosted CI runner) this project's own
CI job and a local `pytest` run landing at the same moment — collide on
that name: `manager_lifecycle.py`'s `_adopt_or_remove_by_name` finds a
container already there under the same `workspace_id` label and *adopts
it* (HR-017's crash-recovery path, `docs/HARDENING_SPEC.md` §9 — the
right behavior for its actual purpose, recovering from a crash between
`containers.run` and persisting the result, not for two live processes
racing) rather than creating a fresh one, so the second process's own
container is silently shared with — and can be stopped, removed, or
reconciled out from under — the first. `tests/workspace/conftest.py`'s
RR-024/HR-026 resource tracking stops one test run from destroying
*another process's* resources; it does nothing about this, since from
the daemon's perspective it is the same, correctly-adopted workspace
either way.

**Name it — this is not flakiness.** Observed twice independently while
producing `docs/GAP2_TODO.md`'s X1–X22 list: a full-suite run alongside
concurrent Docker activity produced 11 failures and 2 errors, all in
`tests/workspace/`, all `workspace_open`-related; the identical run
immediately afterward, with nothing else touching Docker, passed clean.
A `workspace_open`/lifecycle-shaped failure that doesn't reproduce in
isolation is this collision, not a flaky test — before spending time
chasing it as one, check `docker ps` and (on this project's own
self-hosted CI) `gh run list --limit 1` for an in-progress run first.
**A retry does not fix this** — it would only turn a loud wrong answer
into a slow one while hiding the same collision underneath, since the
racing process is still there and the names are still the same.

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
