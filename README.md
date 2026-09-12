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

For the condensed end-to-end path, read [`docs/QUICKSTART.md`](docs/QUICKSTART.md).
The minimal local sequence is:

```bash
uv sync --group dev
git config core.hooksPath .githooks

docker build -t mcp-dev-ubuntu:24.04 docker/default/
```

Create a config file with the required fields:

```yaml
version: 1
state_db_path: /var/lib/dev-workspace-mcp/state.db

docker:
  default_image: mcp-dev-ubuntu:24.04

ssh:
  keys_dir: /etc/dev-workspace-mcp/keys
  known_hosts: /etc/dev-workspace-mcp/github_known_hosts

audit:
  path: /var/log/dev-workspace-mcp/audit.jsonl

repositories:
  - id: my-repo
    path: /home/you/work/my-repo
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

Then run the server:

```bash
uv run dev-workspace-mcp serve --config config.yml
```

That starts the MCP server on `stdio`, which is suitable for local MCP clients
or for OpenAI Secure MCP Tunnel when connecting ChatGPT to a private local
server.

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

Repository entries look like this:

```yaml
repositories:
  - id: my-repo
    path: /home/you/work/my-repo
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

Use [`docs/CONNECTING_A_REPOSITORY.md`](docs/CONNECTING_A_REPOSITORY.md) for the
end-to-end deploy-key and repository-registration flow.

## Running the app

Build the workspace image named by your config:

```bash
docker build -t mcp-dev-ubuntu:24.04 docker/default/
```

Start the daemon:

```bash
uv run dev-workspace-mcp serve --config config.yml
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
and the tunnel client connects outbound to OpenAI. See
[`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md).

This MCP server includes write-capable tools. Make sure the ChatGPT account or
workspace you use supports write-capable custom MCP apps/developer mode; a
read/fetch-only MCP connection is insufficient for the full workflow.

## Documentation map

- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — copy-pasteable setup path
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local development, checks, and server run instructions
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — configuration guide
- [`docs/CONNECTING_A_REPOSITORY.md`](docs/CONNECTING_A_REPOSITORY.md) — deploy-key and repository registration walkthrough
- [`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md) — ChatGPT + Secure MCP Tunnel setup
- [`docs/TOOLS.md`](docs/TOOLS.md) — human-readable MCP tool index
- [`schemas/tools.json`](schemas/tools.json) — authoritative exported tool contract
- [`docs/SECURITY.md`](docs/SECURITY.md) — trust model and security notes
- [`docs/SPEC.md`](docs/SPEC.md) — full behavioral specification
- [`docs/TODO.md`](docs/TODO.md) — implementation status/task history

## Tech stack

Python 3.12, the `mcp` SDK, Docker SDK for Python, Pydantic, PyYAML, Click, and
`uv`. Workspaces run in Docker containers on Linux.

## License

[Apache License 2.0](LICENSE).
