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
uv run mcp-chatgpt-sandbox setup --config /tmp/mcp-chatgpt-sandbox/config.yml
```

Useful modes:

```bash
uv run mcp-chatgpt-sandbox setup --resume --config /tmp/mcp-chatgpt-sandbox/config.yml
uv run mcp-chatgpt-sandbox setup --advanced --config /tmp/mcp-chatgpt-sandbox/config.yml
uv run mcp-chatgpt-sandbox setup --no-browser --config /tmp/mcp-chatgpt-sandbox/config.yml
uv run mcp-chatgpt-sandbox setup --check --config /tmp/mcp-chatgpt-sandbox/config.yml
uv run mcp-chatgpt-sandbox doctor --config /tmp/mcp-chatgpt-sandbox/config.yml
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
- `setup/dialogs_panel.py` — existing-installation, repair, start-fresh, and
  cancellation dialogs;
- `setup/step_actions_common.py` — shared presentation protocols and widget/value
  helpers used by action mixins;
- `setup/step_actions_core.py` — prerequisites, storage, repository, SSH/GitHub,
  runtime, and ordinary step validation/actions;
- `setup/step_actions.py` — tunnel review/apply, config review/write, and final
  diagnostics coordination;
- `setup/step_actions_docker.py` — reviewed Docker prepare/apply boundaries;
- `setup/step_actions_hardened.py` — guided Git-policy extraction/validation
  adapter;
- `setup/app.py` — setup lifecycle, navigation, validation-gating, resumable-state
  activation, and widget population shell.

The split keeps host/domain mutations out of `app.py`, keeps the ordinary action
surface out of the final-review coordinator, and leaves Docker and Git-policy
hardening boundaries independently reviewable. `step_actions_hardened.py` remains
separate deliberately because folding it back into the main action layer would
increase coordinator size without clarifying ownership.

The static architecture suite discovers presentation modules by the documented
convention (`app.py`, `step_panels.py`, `step_actions*`, and `*_panel.py`) and
requires that discovery to equal an explicit per-module manifest. New
presentation modules therefore require an explicit ownership/line-budget
disposition. Current caps are 750 lines for `app.py`, 300 for `step_actions.py`,
150 for `step_actions_common.py`, 650 for `step_actions_core.py`, 200 each for
`step_actions_docker.py` and `step_actions_hardened.py`, 150 for
`dialogs_panel.py`, 450 for `step_panels.py`, and 200 for
`git_policy_panel.py`.

The larger MCP tool coordinators use the same responsibility-splitting approach:
`tools/workspace_models.py` owns workspace output dataclasses,
`tools/workspace_server_info.py` owns base server-info construction, and
`tools/execution_models.py` owns execution output dataclasses. The four
coordinators refactored in September 2026 (`tools/workspace.py`,
`tools/execution.py`, `setup/step_actions.py`, and `setup/app.py`) are protected
by a repository test requiring each to stay below 800 physical lines.

Production config models remain authoritative. Do not add a second validator to
the TUI when an existing production helper can be reused or extracted.

### Reviewed config commit serialization

A reviewed configuration plan is bound to the source bytes seen during review.
Final commit is serialized with an empty sibling lock file named
`.CONFIG_NAME.setup.lock` (for example, `.config.yml.setup.lock`). On supported
Linux hosts, `setup/config_writer.py` opens that file owner-only and takes an
exclusive advisory `flock` before the final source re-read.

Lock acquisition is bounded by one non-blocking attempt rather than an indefinite
wait. Contention fails immediately with an actionable error; setup does not retry
or sleep. The lock file is intentionally persistent and contains no configuration
or credential material. Removing an advisory lock file after release can create a
second independently lockable inode during contention, so only the kernel lock
state is released. Kernel ownership is tied to the open descriptor and is released
automatically on process exit/crash; there is no stale-lock age or PID-file recovery
path.

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

## Running the suite concurrently

Workspace names are deterministic for crash recovery and reopen semantics, so
two processes using the same repository IDs *could* adopt or reconcile each
other's containers. Two mechanisms prevent that:

- **G3R-015** gives every Docker-using pytest context its own namespace. The
  `docker_client` fixture sets `MCP_CHATGPT_SANDBOX_TEST_RESOURCE_NAMESPACE` to a
  fresh UUID, which `naming.container_name()` folds into both the container name
  and a `test-namespace` label. `DockerWorkspaceManager.reconcile()` adopts a
  container only when PMOR-001's `classify_workspace_container()` proves
  ownership, and its first check, `_namespace_proof()`
  (`operations/resources.py`), treats a container from any other namespace as
  foreign. (`_CoreMixin.reconcile()` in `manager_core.py` is shadowed by that
  override and never runs; do not read it as the live path.)
- **PSCR-010** (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`) scoped every
  absence and exact-count assertion in `tests/workspace` to the running context.
  Before it, those assertions filtered only on logical ids and saw each other's
  containers.

Concurrent suite runs are therefore **safe**. This was demonstrated, not
inferred: two overlapping `tests/workspace` sessions each passed 438 tests, and
an A/B against the pre-PSCR-010 tree under identical contention failed 6 tests
on the old side and none on the new.

Overlapping is still **slower**, because the CI runner is self-hosted on this
machine and shares its CPU, memory, and Docker daemon. Prefer not to overlap, but
there is no need to wait for CI before running the suite.

The exception is a coverage run. CI itself runs `pytest --cov`, and while it did,
a local `pytest --cov --cov-report=term-missing` started from a Claude Code
session was twice terminated about 40 seconds in by the session's background-task
memory guard. No test had failed, the kernel logged no out-of-memory event, and
the same run completed on an idle host (`docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md`,
PSCR2-008). Non-coverage runs overlapped CI repeatedly without incident. This is a
resource limit of the agent harness, not a correctness problem, but run coverage
suites on an idle host.

A live `mcp-chatgpt-sandbox serve` on the same daemon is isolated in both
directions that matter, and both are demonstrated rather than inferred:

- **Adoption.** A production-shaped reconcile does not adopt a test context's
  container, and a test context's reconcile does not adopt a production-shaped
  one (PSCR2-009, `tests/workspace/test_pscr2_live_server_isolation.py`). Each
  test also shows the container *is* adopted under its own shape, and both fail
  when `_namespace_proof()` is neutered.
- **Teardown.** Suite teardown removes only the exact resources it created, and
  leaves a production-labelled sentinel container and cache volume intact
  (HR-026, HAT-020, `tests/workspace/test_conftest_teardown.py`).

What is not isolated is capacity: a suite run shares the daemon's CPU, memory,
and disk with the workspaces a live server is serving. Prefer not to run the
suite on a daemon serving real work, for that reason rather than for safety.

If a workspace test fails with a collision shape — `assert [<Container: ...>] ==
[]`, or two containers where one was expected — treat it as a **real defect**:
the namespacing has regressed. Investigate it; do not add retries or skips that
would hide it.

## Running the server locally

For a normal developer/operator first run, use the wizard:

```bash
uv run mcp-chatgpt-sandbox setup
```

It can explicitly build the selected project default image, create reviewed
storage paths/pinned `known_hosts`, register repositories, review/write config,
and run final diagnostics.

Then start the server:

```bash
uv run mcp-chatgpt-sandbox serve \
  --config ~/.config/mcp-chatgpt-sandbox/config.yml
```

The default transport is `stdio`. A local MCP client can spawn it directly;
ChatGPT uses the optional Secure MCP Tunnel path described in
[`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md).

Manual configuration remains supported. See
[`docs/CONFIGURATION.md`](CONFIGURATION.md) and
[`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).

## Troubleshooting

- **`serve` rejects the config.** Run `mcp-chatgpt-sandbox doctor --config ...`
  and compare the field with `docs/CONFIGURATION.md` / the production models.
- **The setup wizard will not enable Next.** The active step is not currently
  validated. Use that screen's explicit validation/remediation action; changing
  an upstream input intentionally invalidates dependent review/diagnostics.
- **A config changed after the review screen.** The writer intentionally rejects
  the stale source digest while holding the setup-writer lock. Prepare a fresh
  review; there is no overwrite bypass.
- **Another setup writer owns the config lock.** Acquisition fails immediately;
  let the other setup operation finish, then retry and review again if the source
  changed.
- **Docker is unavailable.** Configuration may continue, but final diagnostics
  remain ACTION REQUIRED for Docker-dependent checks.
- **The bundled Docker build context is unavailable.** The Docker step remains
  ACTION REQUIRED and the wizard does not invoke a build. `docker/default/` is a
  source-checkout asset and is deliberately not packaged into the distribution
  (PSCR-001, `docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`), so this is the
  expected state outside a checkout. Build the image manually with
  `docker build -t <image> docker/default/` from a checkout, or select an image
  that already exists on the host.
- **A setup-state file is corrupt.** Use Repair or Start fresh. Start fresh resets
  wizard progress; it does not delete the production config, repositories,
  credentials, database contents, Docker volumes, or workspaces.
- **The installed package does not match the source checkout.** A non-editable
  install of `mcp-chatgpt-sandbox` that finds an enclosing checkout (for example
  a wheel installed into `<repo>/.venv`) compares its own installed version with
  the checkout's `pyproject.toml` before building from `docker/default/`, and
  refuses on a mismatch or an undeterminable version (PSCR2-002,
  `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`). Otherwise an older wheel in a
  newer checkout would build from a Dockerfile it was never written against.
  Reinstall from the checkout, or build the image manually and select it. The
  editable development install never trips this: its code *is* the checkout's
  `src/` tree, and a same-version checkout with uncommitted `Dockerfile` edits
  passes deliberately.
- **Tool schemas changed.** Regenerate `schemas/tools.json` with
  `mcp-chatgpt-sandbox export-schemas` and make the required `contract_version`
  decision; the setup TUI itself does not alter the public MCP tool surface.

## Project layout

```text
src/mcp_chatgpt_sandbox/        runtime package
src/mcp_chatgpt_sandbox/setup/  interactive setup + shared diagnostics
tests/setup/                   setup unit/TUI/acceptance tests
tests/                         remaining unit/integration tests
docs/SPEC.md                   authoritative server behavior
docs/SETUP_WIZARD_SPEC.md      setup wizard safety/UX contract
docs/SETUP_WIZARD_TODO.md      setup wizard implementation/acceptance checklist
schemas/tools.json             exported MCP tool contract
```
