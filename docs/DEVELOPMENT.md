# Development

## Environment setup

Requirements:

- Linux;
- Python 3.12, matching `.python-version`;
- Docker installed and running for the complete test/integration path;
- [`uv`](https://docs.astral.sh/uv/).

Install runtime/dev dependencies and enable the repository-managed Git hooks:

```bash
uv sync --group dev
git config core.hooksPath .githooks
```

Always invoke project tools through `uv run`. The repository's pre-commit hook
runs the pinned Ruff format/lint and strict mypy checks before a commit. The full
pytest suite is deliberately not in the hook because it is Docker-heavy and
multi-minute.

If you have a global `core.hooksPath`, this repository explicitly forwards the
supported global hooks from `.githooks/`; Git itself otherwise treats a local
`core.hooksPath` as a replacement, not a search chain.

## Dependencies

The authoritative dependency list is `pyproject.toml` / `uv.lock`.

Important runtime dependencies include:

- `mcp` — MCP server/tool implementation;
- `docker` — Docker Engine control plane;
- `pydantic` — production configuration/runtime models;
- `pyyaml` — YAML configuration parsing;
- `click` — CLI commands;
- `textual` — interactive setup TUI.

Development dependencies include pytest/coverage, Ruff, strict mypy, and typing
stubs.

After intentionally changing dependencies, regenerate and commit `uv.lock`; do
not hand-maintain resolver entries.

## Main quality gates

Run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

CI runs the same checks plus Docker image construction, the MCP exported-schema
contract check, `contract_version` history enforcement, and the `docs/TOOLS.md`
drift check.

To match the CI test command:

```bash
uv run pytest --cov --cov-report=term-missing
```

`mypy` runs in strict mode against `src/`; Ruff uses the configured rule set and
100-column default. The setup Textual shell and step panels have narrow E501
exceptions because they contain operator-facing TUI strings; formatting, every
other lint rule, and strict mypy still apply normally.

## Setup wizard development

Launch the TUI against an isolated config path while developing:

```bash
uv run dev-workspace-mcp setup --config /tmp/dev-workspace-mcp/config.yml
```

Useful modes:

```bash
uv run dev-workspace-mcp setup --resume --config /tmp/dev-workspace-mcp/config.yml
uv run dev-workspace-mcp setup --advanced --config /tmp/dev-workspace-mcp/config.yml
uv run dev-workspace-mcp setup --no-browser --config /tmp/dev-workspace-mcp/config.yml
uv run dev-workspace-mcp setup --check --config /tmp/dev-workspace-mcp/config.yml
uv run dev-workspace-mcp doctor --config /tmp/dev-workspace-mcp/config.yml
```

The setup implementation is intentionally layered:

- `setup/model.py` / `choices.py` — versioned non-secret resumable state;
- `setup/filesystem.py` — fail-closed bounded reads, backups, atomic writes, and
  explicit leaf-private setup-directory creation;
- `setup/config_writer.py` — reviewed/CAS-bound config planning, serialized final
  source verification, backup/atomic replacement, byte verification, and
  production reload;
- `setup/host.py`, `docker_setup.py`, `repository_setup.py`,
  `ssh_github_setup.py`, `git_policy_setup.py`, `runtime_setup.py` — independently
  testable domain validation/actions;
- `setup/git_policy_form.py` — guided Custom `GitPolicyConfig` form snapshots
  validated through the production model;
- `setup/materialize.py` — translation between production config and reviewed
  setup choices;
- `setup/tunnel.py` — optional Secure MCP Tunnel planning/health;
- `setup/diagnostics.py` — shared TUI/headless final diagnostic engine;
- `setup/step_panels.py` and `setup/git_policy_panel.py` — Textual controls;
- `setup/step_actions.py`, `setup/step_actions_docker.py`, and
  `setup/step_actions_hardened.py` — step-specific Textual actions that call
  domain modules without owning production config persistence/semantics;
- `setup/app.py` — setup lifecycle, navigation, validation-gating, resumable-state
  activation, and widget population shell.

`step_actions_hardened.py` remains separate deliberately: it is a small bounded
adapter for guided Git-policy extraction/validation, while
`step_actions_docker.py` independently owns reviewed Docker action boundaries.
Folding those back into the main action module would increase coordinator size
without clarifying ownership.

The static architecture suite discovers presentation modules by the documented
convention (`app.py`, `step_panels.py`, `step_actions*`, and `*_panel.py`) and
requires that discovery to equal an explicit per-module manifest. New
presentation modules therefore require an explicit ownership/line-budget
disposition. Current coordinator caps remain 800 lines for `app.py` and 900 for
`step_actions.py`; the extracted modules have smaller independent caps.

Production config models remain authoritative. Do not add a second validator to
the TUI when an existing production helper can be reused or extracted.

### Reviewed config commit serialization

A reviewed configuration plan is bound to the source bytes seen during review.
Final commit is serialized with an empty sibling lock file named
`.CONFIG_NAME.setup.lock` (for example, `.config.yml.setup.lock`). On supported
Linux hosts, `setup/config_writer.py` opens that file owner-only and takes an
exclusive advisory `flock` before the final source re-read.

Lock acquisition is bounded (currently two seconds, polled in short intervals),
not an indefinite wait. The lock file is intentionally persistent and contains
no configuration or credential material. Removing an advisory lock file after
release can create a second independently lockable inode during contention, so
only the kernel lock state is released. Kernel ownership is tied to the open
descriptor and is released automatically on process exit/crash; there is no
stale-lock age or PID-file recovery path.

While the lock is held, the writer revalidates the reviewed source digest and
then performs backup creation, same-directory atomic replacement, byte-for-byte
verification, and production `load_config()` reload before releasing the lock.
All setup writers use the same lock identity. A platform without the required
safe advisory-lock primitive fails closed instead of reverting to an
unserialized writer.

### Focused setup tests

Run the setup suite independently while iterating:

```bash
uv run pytest tests/setup
```

Textual pilot tests cover navigation, validation gating, lifecycle behavior, and
guided Git-policy editing. The setup acceptance tests cover fresh configuration,
existing-config preservation/CAS, repair/backups, unsafe targets, production
reload, diagnostics, and server construction.

When adding persisted setup-state fields, update the secret-field/state-schema
tests. Raw credentials must never become resumable state merely to simplify a
screen.

## Full-suite Docker isolation warning

Do not run the full workspace/container test suite concurrently with another
copy of the suite or a live server using the same repository IDs. Workspace
names are intentionally deterministic for crash recovery/reopen semantics; two
live processes using the same IDs can adopt or reconcile the same container and
cause lifecycle-shaped failures.

This is not ordinary test flakiness. If workspace-open/lifecycle tests fail only
under concurrent Docker activity, stop the competing process and rerun rather
than adding retries that hide the collision.

## Running the server locally

For a normal developer/operator first run, use the wizard:

```bash
uv run dev-workspace-mcp setup
```

It can explicitly build the selected project default image, create reviewed
storage paths/pinned `known_hosts`, register repositories, review/write config,
and run final diagnostics.

Then start the server:

```bash
uv run dev-workspace-mcp serve \
  --config ~/.config/dev-workspace-mcp/config.yml
```

The default transport is `stdio`. A local MCP client can spawn it directly;
ChatGPT uses the optional Secure MCP Tunnel path described in
[`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md).

Manual configuration remains supported. See
[`docs/CONFIGURATION.md`](CONFIGURATION.md) and
[`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).

## Troubleshooting

- **`serve` rejects the config.** Run `dev-workspace-mcp doctor --config ...`
  and compare the field with `docs/CONFIGURATION.md` / the production models.
- **The setup wizard will not enable Next.** The active step is not currently
  validated. Use that screen's explicit validation/remediation action; changing
  an upstream input intentionally invalidates dependent review/diagnostics.
- **A config changed after the review screen.** The writer intentionally rejects
  the stale source digest while holding the setup-writer lock. Prepare a fresh
  review; there is no overwrite bypass.
- **Another setup writer owns the config lock.** Acquisition is bounded; let the
  other setup operation finish and review again if the source changed.
- **Docker is unavailable.** Configuration may continue, but final diagnostics
  remain ACTION REQUIRED for Docker-dependent checks.
- **The bundled Docker build context is unavailable.** The Docker step remains
  ACTION REQUIRED and the wizard does not invoke a build. Repair/reinstall the
  package/source checkout or build an appropriate image manually.
- **A setup-state file is corrupt.** Use Repair or Start fresh. Start fresh resets
  wizard progress; it does not delete the production config, repositories,
  credentials, database contents, Docker volumes, or workspaces.
- **Tool schemas changed.** Regenerate `schemas/tools.json` with
  `dev-workspace-mcp export-schemas` and make the required `contract_version`
  decision; the setup TUI itself does not alter the public MCP tool surface.

## Project layout

```text
src/dev_workspace_mcp/        runtime package
src/dev_workspace_mcp/setup/  interactive setup + shared diagnostics
tests/setup/                   setup unit/TUI/acceptance tests
tests/                         remaining unit/integration tests
docs/SPEC.md                   authoritative server behavior
docs/SETUP_WIZARD_SPEC.md      setup wizard safety/UX contract
docs/SETUP_WIZARD_TODO.md      setup wizard implementation/acceptance checklist
schemas/tools.json             exported MCP tool contract
```
