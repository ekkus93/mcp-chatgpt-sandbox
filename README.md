# mcp-chatgpt-sandbox

A private MCP server that gives ChatGPT and other MCP-capable clients a
Docker-backed development workstation for your own repositories. Each
repository opens into its own isolated container where the client can read and
write files, run commands, and drive Git against a real local checkout without
touching the rest of your host directly.

## What this app does

`mcp-chatgpt-sandbox` is a local development-control plane for AI coding
clients. It exposes an MCP tool surface backed by:

- a configured set of local Git repositories;
- Docker containers that mount one repository at `/workspace`;
- filesystem tools for bounded read/write/search/patch/delete operations;
- execution tools for running real project commands and tests;
- structured Git tools for status/diff/log/commit/pull/push;
- read-only operational inventory and explicit, confirm-before-execute resource
  pruning.

The intended use case is a trusted AI coding client working on repositories you
own, with stronger development capabilities than a generic hosted sandbox and
stronger scoping than giving the client a raw host shell.

## Why

> Give an AI coding client the practical capabilities of a normal development
> workstation while preserving explicit isolation, repository scoping,
> credential boundaries, auditability, and control over destructive operations.

Restricted sandboxes usually cannot install a project toolchain, run a real
test suite, use Docker, or push a commit. A raw shell on the host can do those
things, but it has poor repository and credential boundaries. This project sits
between those extremes: the daemon is allowed to talk to Docker and your local
repositories, while each client workspace is scoped to a single configured
repository container.

That model is especially useful for toolchain-heavy projects such as Rust,
where a useful coding environment often needs `cargo`, `rustfmt`, `clippy`,
native packages, system libraries, and project-specific build/test commands.

## Architecture

```text
                     MCP Client
                     (ChatGPT)
                         |
                         | MCP
                         v
              +----------------------+
              | Dev Workspace MCP    |
              | Server / Daemon      |
              +----------+-----------+
                         |
                         | Docker API
                         v
              +----------------------+
              | Docker Engine        |
              +----------+-----------+
                         |
            +------------+-------------+
            |                          |
            v                          v
 +----------------------+   +----------------------+
 | Project A Container  |   | Project B Container  |
 | /workspace           |   | /workspace           |
 +----------+-----------+   +----------+-----------+
            | bind mount                 | bind mount
            v                            v
 ~/work/project-a              ~/work/project-b
```

The MCP daemon is the privileged control plane; it talks to Docker, never the
other way around. Each configured repository maps to its own container, built
from a configurable image, with the checkout bind-mounted read/write so changes
the client makes are immediately visible to your own editor and tools. Nothing
is copied into the container or into the daemon database.

Workspaces can be persistent or ephemeral. Persistent workspaces are kept
around and may be idle-stopped after a configurable timeout; ephemeral
workspaces are disposable and better for clean reproducible runs.

## MCP tool surface

The current MCP surface is **24 tools**. The authoritative contract is
[`schemas/tools.json`](schemas/tools.json); the human-readable index is
[`docs/TOOLS.md`](docs/TOOLS.md).

| Category | Tools |
| --- | --- |
| Repositories | `repository_list` |
| Filesystem | `list_files`, `read_file`, `write_file`, `patch_file`, `delete_file`, `search_files` |
| Workspace / server state | `workspace_open`, `workspace_info`, `server_info` |
| Execution | `exec`, `exec_start`, `exec_status`, `exec_output`, `exec_cancel` |
| Structured Git | `git_status`, `git_diff`, `git_log`, `git_commit`, `git_pull`, `git_push` |
| Operations | `resource_inventory`, `resource_prune_plan`, `resource_prune_execute` |

The structured tools provide the policy-governed path for common operations.
`exec` is a real shell inside the workspace container, so it can run arbitrary
project commands, install packages, run custom test commands, or perform Git
operations that do not have a structured MCP wrapper. Because `exec` is not a
Git-aware policy layer, prefer the structured Git tools for normal
commit/pull/push workflows.

## Quick start

For a new installation, use the interactive setup wizard rather than writing
YAML by hand:

```bash
uv sync --group dev
git config core.hooksPath .githooks
uv run dev-workspace-mcp setup
```

The Textual wizard guides host paths, Docker, repositories, SSH/GitHub
credential references, Git safety policy, optional advanced limits, and an
optional ChatGPT Secure MCP Tunnel profile. It shows the exact configuration
changes before writing, creates timestamped backups when replacing an existing
config, reloads the result through the production config loader, and finishes
with machine-verifiable diagnostics. Docker image builds are separately
reviewed/apply actions: the wizard resolves the bundled default build context
independently of the caller's current directory and, if that context is missing,
reports **ACTION REQUIRED** without invoking Docker.

The default config path is:

```text
~/.config/dev-workspace-mcp/config.yml
```

After setup, rerun the same diagnostics headlessly whenever needed:

```bash
uv run dev-workspace-mcp doctor --config ~/.config/dev-workspace-mcp/config.yml
# equivalent:
uv run dev-workspace-mcp setup --check --config ~/.config/dev-workspace-mcp/config.yml
```

Then start the MCP server:

```bash
uv run dev-workspace-mcp serve --config ~/.config/dev-workspace-mcp/config.yml
```

For the condensed end-to-end walkthrough, including deploy-key setup and the
manual-config fallback, see [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Development environment

Requirements:

- Linux
- Python 3.12, matching `.python-version`
- Docker installed and running
- [`uv`](https://docs.astral.sh/uv/)

Set up dependencies and Git hooks:

```bash
uv sync --group dev
git config core.hooksPath .githooks
```

The local hook path enables repository-managed Git hooks. The pre-commit hook
runs formatting/lint/type checks through the project-pinned `uv` environment so
local results match CI.

## Linting, type checking, and tests

Run the main checks locally with:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

CI runs the same checks plus schema/contract drift gates. To match the CI test
coverage command more closely:

```bash
uv run pytest --cov --cov-report=term-missing
```

The test suite is Docker-heavy and multi-minute. Avoid running concurrent
workspace/container tests or a live server using the same repository IDs while
running the full suite; see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the
full warning and troubleshooting notes.

## Configuration

Configuration is YAML and is validated before the server starts. The enforced
schema lives in `src/dev_workspace_mcp/config/models.py`; the primary
configuration guide is [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

The recommended way to create or edit it is:

```bash
uv run dev-workspace-mcp setup --config /path/to/config.yml
```

The wizard preserves valid settings it did not review, persists only non-secret
resume state, and keeps raw private keys/API/runtime credentials out of both the
config and setup-state file. Reviewed config commits are serialized across the
final source check and atomic replacement so a competing setup writer cannot
silently overwrite the same reviewed source. Existing arbitrary parent
directories are never chmodded merely because setup writes a file inside them.
Manual YAML authoring remains supported for recovery, automation, and operators
who deliberately prefer it.

Required top-level/operator fields are:

| Field | Purpose |
| --- | --- |
| `state_db_path` | SQLite state database for workspaces, jobs, audit indexes, and maintenance records. |
| `docker.default_image` | Default Docker image used for workspace containers. |
| `ssh.keys_dir` | Host directory containing repository deploy keys. |
| `ssh.known_hosts` | Pinned SSH host-key file used by workspace Git connections. |
| `audit.path` | Append-only structured audit log path. |
| `repositories` or `repository_roots` | The repositories the daemon is allowed to expose. |

A config with neither `repositories` nor `repository_roots` is valid but exposes
no repositories, so `workspace_open` has nothing to open.

A manual repository entry looks like this:

```yaml
repositories:
  - id: my-repo
    path: /home/you/work/my-repo
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

Use [`docs/CONNECTING_A_REPOSITORY.md`](docs/CONNECTING_A_REPOSITORY.md) for the
end-to-end deploy-key and repository-registration flow.

## Setup command reference

The main first-run command is:

```bash
uv run dev-workspace-mcp setup [--config PATH]
```

Useful modes:

- `--resume` — resume valid incomplete non-secret setup state;
- `--check` — run the shared final diagnostics without opening the TUI;
- `--no-browser` — never launch external documentation/browser URLs;
- `--advanced` — expose advanced runtime/resource controls immediately;
- `doctor --config PATH` — alias for the same headless diagnostics engine.

Interactive setup exits `0` on successful Finish, `2` on a fatal setup error,
and `130` when the operator confirms cancellation. Headless diagnostics exit
`0` when mandatory checks pass, `1` while mandatory remediation/action remains,
and `2` for a mandatory hard failure.

## Running the app

The wizard can build the project default workspace image explicitly. It keeps
the exact Docker build as argv until shell-safe preview rendering and validates
the same bundled context before the approved build. If the bundled context is
unavailable, the action stays **ACTION REQUIRED** and no Docker build is run.
For manual setup, build it yourself from a source checkout:

```bash
docker build -t mcp-dev-ubuntu:24.04 docker/default/
```

Start the daemon:

```bash
uv run dev-workspace-mcp serve --config ~/.config/dev-workspace-mcp/config.yml
```

From an MCP client, open a configured repository:

```json
{ "repository_id": "my-repo", "mode": "persistent" }
```

A working setup returns a running container state. Use a real `git_push` through
the structured Git tool or through `exec` as the final end-to-end proof that the
workspace, deploy key, remote validation, and container environment are wired
correctly.

## Connecting ChatGPT

ChatGPT cannot directly spawn this server as a local `stdio` process. For a
private local server, use OpenAI Secure MCP Tunnel so the server stays private
and the tunnel client connects outbound to OpenAI. The setup wizard contains an
optional guided tunnel step; the complete manual/current-OpenAI-doc flow remains
in [`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md).

This MCP server includes write-capable tools. Make sure the ChatGPT account or
workspace you use supports write-capable custom MCP apps/developer mode; a
read/fetch-only MCP connection is insufficient for the full workflow.

## Documentation map

- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — wizard-first setup path and manual fallback
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local development, checks, TUI tests, and server run instructions
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — configuration and wizard-ownership guide
- [`docs/CONNECTING_A_REPOSITORY.md`](docs/CONNECTING_A_REPOSITORY.md) — deploy-key and repository registration walkthrough
- [`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md) — ChatGPT + Secure MCP Tunnel setup
- [`docs/TOOLS.md`](docs/TOOLS.md) — human-readable MCP tool index
- [`schemas/tools.json`](schemas/tools.json) — authoritative exported tool contract
- [`docs/SECURITY.md`](docs/SECURITY.md) — trust model and security notes
- [`docs/SETUP_WIZARD_SPEC.md`](docs/SETUP_WIZARD_SPEC.md) — interactive setup design/safety contract
- [`docs/SPEC.md`](docs/SPEC.md) — full behavioral specification

## Tech stack

Python 3.12, the `mcp` SDK, Docker SDK for Python, Pydantic, PyYAML, Click,
Textual, and `uv`. Workspaces run in Docker containers on Linux.

## License

[Apache License 2.0](LICENSE).
