# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A private MCP server giving MCP clients Docker-backed dev workspaces scoped to configured
local Git repositories. Note the three names differ: repo `mcp-chatgpt-sandbox`, package
`dev_workspace_mcp`, CLI `dev-workspace-mcp`.

Large reference docs — read on demand, do not assume their contents:
`docs/DEVELOPMENT.md` (workflow), `docs/SPEC.md` (architecture + trust model, §-numbered),
`docs/CONFIGURATION.md`, `docs/TOOLS.md`, `docs/SECURITY.md`.

## Commands

Always invoke project tools through `uv run` — never a bare `ruff`/`mypy`/`pytest` from PATH.
Version skew is a real, reproduced failure here: a bare ruff 0.12.0 rewrote an import block
into an order the project-pinned ruff then rejected in CI.

Quality gates, in CI order:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src      # strict, src/ only; tests are excluded
uv run pytest --cov --cov-report=term-missing
```

One-time per clone: `git config core.hooksPath .githooks` (activates the pre-commit gate).
Single test: `uv run pytest tests/<area>/test_x.py::test_name`.

## Running tests

Run the full suite for any change, not just a targeted subset.

- Requires a reachable Docker daemon **and** the test image:
  `docker build -t mcp-dev-test-s7:24.04 docker/default/`. That tag must match
  `DEFAULT_IMAGE_TEST_TAG` in `tests/workspace/conftest.py`.
- Any pytest skip is converted into a session failure (`tests/_skip_policy.py`). A skip here
  means Docker was unreachable — never resolve a failure by adding or tolerating a skip.
- Never run the suite while another copy of it, or a live server, is running. Workspace names
  are deterministic for crash recovery, so two processes adopt the same container. Lifecycle
  failures under concurrent Docker activity are collisions, not flakes: stop the competing
  process and rerun rather than adding retries that hide them.

## Change workflow

Work must trace to a task ID in an active `docs/*_SPEC.md` + `docs/*_TODO.md` pair
(`HR-nnn`, `G2R-nnn`, `SWPH-nnn`, `AT-nnn`, …). Ask which task ID applies, or propose a new
spec+TODO pair, before implementing.

Commit directly to `master` using conventional-commit subjects (`fix:`, `test:`, `docs:`,
`refactor:`, `style:`, `chore:`). No branch or PR needed. The ` (#NN)` subject suffixes and
`ralph/<task>-<slug>` branches in history come from the bot's squash-merge flow — don't
imitate them for a direct commit.

## Comments

Non-obvious decisions carry a spec/task citation plus a short "why not the obvious
alternative" narrative, e.g. `HR-029 (docs/HARDENING_SPEC.md §12): ...`. This is a deliberate
convention — match it, in prose paragraphs rather than terse one-liners, and say how the
rejected alternative actually failed when you know.

## Guards that fail on structure, not behavior

- **Tool surface.** Changing it requires all three, each gated separately in CI: regenerate
  `schemas/tools.json` via `uv run dev-workspace-mcp export-schemas`, make the
  `contract_version` bump decision (must increase monotonically), and update the matching
  `docs/TOOLS.md` table row.
- **Module line budgets.** `tools/workspace.py`, `tools/execution.py`, `setup/step_actions.py`
  and `setup/app.py` must each stay under 800 lines
  (`tests/test_refactored_module_size_guard.py`). Setup presentation modules have per-module
  caps and an explicit manifest (`tests/setup/test_setup_static_audit.py`) — a new
  `*_panel.py` or `step_actions*` module fails the suite until it gets a manifest entry.
- **Acceptance coverage.** Every active acceptance criterion must be cited by a real test
  (`tests/test_acceptance_test_coverage.py`); renaming or deleting a cited test fails it.
- **Dependencies.** After an intentional change, regenerate and commit `uv.lock`; never
  hand-edit resolver entries.

## Style and invariants

- Ruff: line-length 100, target py312, `select = ["E","F","I","UP","B","SIM","C4"]`.
  `src = ["src"]` in `pyproject.toml` is load-bearing — without it ruff's isort treats the
  non-Python `docker/` directory as a first-party package and splits `docker` imports across
  sections.
- Production config models in `config/models.py` are authoritative. Don't add a second
  validator in the setup TUI when a production helper can be reused or extracted.
- Secrets are referenced, never stored: config holds paths (`ssh_key`,
  `github.api_token_path`), not key or token material. Raw credentials must never become
  resumable setup state to simplify a screen.
- `docker/default/Dockerfile`'s ENTRYPOINT runs `python3 -I` in front of a wrapper that strips
  `PYTHON*`/bash-function state before privileges drop. Both are security-load-bearing; do not
  simplify to a direct script or a PATH-resolved interpreter.
- Per-repo `.mcp-workspace.yml` accepts only `version`, `image`, `setup`, `environment`,
  `caches`. Other keys are rejected, not ignored.
