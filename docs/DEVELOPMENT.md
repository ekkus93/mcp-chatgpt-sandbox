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
- `setup/filesystem.py` — fail-closed bounded reads, backups, atomic writes;
- `setup/config_writer.py` — reviewed/CAS-bound config planning, serialized final
  source verification, and atomic commit;
- `setup/host.py`, `docker_setup.py`, `repository_setup.py`,
  `ssh_github_setup.py`, `git_policy_setup.py`, `runtime_setup.py` — independently
  testable domain validation/actions;
- `setup/git_policy_form.py` — guided Custom `GitPolicyConfig` form snapshots
  validated through the production model;
- `setup/materialize.py` — translation between production config and reviewed
  setup choices;
- `setup/tunnel.py` — optional Secure MCP Tunnel planning/health;
- `setup/diagnostics.py` — shared TUI/headless final diagnostic engine;
- `setup/step_panels.py` — Textual controls for each wizard step;
- `setup/step_actions.py`, `setup/step_actions_docker.py`, and
  `setup/step_actions_hardened.py` — step-specific Textual event handlers that
  call the domain modules without owning production config semantics;
- `setup/app.py` — setup lifecycle, navigation, validation-gating, and state
  activation shell.

Production config models remain authoritative. Do not add a second validator to
the TUI when an existing production helper can be reused or extracted.

### Reviewed config commit serialization

A reviewed configuration plan is bound to the source bytes seen during review.
Final commit is serialized with an empty sibling lock file named
`.CONFIG_NAME.setup.lock` (for example, `.config.yml.setup.lock`). On Linux,
`setup/config_writer.py` opens that file owner-only and takes an exclusive,
non-blocking `flock` before it re-reads the source configuration.

The lock file is intentionally persistent and contains no configuration or
credential material. Removing an advisory lock file after release can create a
second independently lockable inode during contention, so only the kernel lock
state is released. Kernel lock ownership is tied to the open descriptor and is
therefore released if the process exits or crashes; there is no stale-lock age
or PID file to recover.

Lock contention fails immediately with an actionable setup error; setup never
waits indefinitely or retries in the background. While the lock is held, the
writer revalidates the reviewed source digest and then performs backup creation,
same-directory atomic replacement, byte-for-byte verification, and production
`load_config()` reload before releasing the lock. All setup writers use this
same lock identity. Operators or other writers that bypass the setup protocol
are not made cooperative by an advisory lock, so manual edits should not be
performed concurrently with an approved setup commit.

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
  the stale source digest. Prepare a fresh review; there is no overwrite bypass.
- **Another setup writer is committing the same config.** Commit lock contention
  fails immediately. Let the other setup operation finish, then prepare/review
  again if the source changed.
- **Docker is unavailable.** Configuration may continue, but final diagnostics
  remain ACTION REQUIRED for Docker-dependent checks.
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
