# mcp-chatgpt-sandbox

A private MCP server giving ChatGPT and other MCP-capable clients a
Docker-backed development workstation for your own repositories — each
repository opens into its own container, where the client can read/write
files, run commands, and drive Git (status/diff/log/commit/pull/push)
against a real, isolated checkout, without ever touching your host
directly.

## Why

> Give an AI coding client the practical capabilities of a normal
> development workstation while preserving explicit isolation, repository
> scoping, credential boundaries, auditability, and control over
> destructive operations.

That's `docs/SPEC.md` §1's stated design goal, and it's the reason this
exists rather than pointing ChatGPT at a generic sandbox: a coding agent
is only as useful as the environment it's allowed to work in. Restricted
sandboxes can't install a toolchain, run a real test suite, or push a
commit. A raw shell on the host can do all of that but with no scoping —
one wrong command touches the wrong repository, or the wrong credential.
This project's answer is a Docker container per repository, mounted
read/write from a real local checkout, holding a deploy key scoped to
that one repository and nothing else — a full development workstation,
just one the client can't use to reach outside the project it was opened
for.

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
 | /workspace            |   | /workspace           |
 +----------+-----------+   +----------+-----------+
            | bind mount                 | bind mount
            v                            v
 ~/work/project-a              ~/work/project-b
```

The MCP daemon is the privileged control plane; it talks to Docker, never
the other way around. Each configured repository maps to its own
container, built from a configurable default image, with the checkout
bind-mounted read/write so changes the client makes are immediately
visible to your own editor and tools — nothing is copied into the
container or the daemon's database. Containers can be persistent (kept
around, idle-stopped after a configurable timeout) or ephemeral
(disposable, for clean reproducible runs), per `docs/SPEC.md` §8.

## Tool surface

21 tools, exposed over MCP (`docs/SPEC.md` §34.1; full contract in
[`schemas/tools.json`](schemas/tools.json), human-readable index in
[`docs/TOOLS.md`](docs/TOOLS.md)):

| Category | Tools |
| --- | --- |
| Repositories | `repository_list` |
| Filesystem | `list_files`, `read_file`, `write_file`, `patch_file`, `delete_file`, `search_files` |
| Workspace / server state | `workspace_open`, `workspace_info`, `server_info` |
| Execution | `exec`, `exec_start`, `exec_status`, `exec_output`, `exec_cancel` |
| Structured Git | `git_status`, `git_diff`, `git_log`, `git_commit`, `git_pull`, `git_push` |

`exec` runs arbitrary shell commands in the workspace — installing
toolchains, running a build or test suite, or any Git operation the
structured tools don't cover (rebase, submodules, a hard reset). The
structured Git tools exist for the common, policy-governed path: they
apply `git_policy` (force-push, branch protection, pull strategy —
`docs/SPEC.md` §18.9) that `exec` doesn't and never will, since `exec` is
a real shell, not something the daemon can constrain after the fact.

## Getting started

- **Local development, running checks, running the server:**
  [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- **Configuring a server instance:**
  [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)
- **Connecting a running server to ChatGPT:**
  [`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md)
- **Trust/threat model this design assumes:**
  [`docs/SECURITY.md`](docs/SECURITY.md)
- **Full specification and implementation status:**
  [`docs/SPEC.md`](docs/SPEC.md) / [`docs/TODO.md`](docs/TODO.md)

## Tech stack

Python 3.12, the [`mcp`](https://pypi.org/project/mcp/) SDK, the Docker
SDK for Python, Pydantic for config validation, and `uv` for packaging
and dependency management. Workspaces run in Docker containers on Linux.
See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the full dependency
list and how to set up a development environment.

## License

[Apache License 2.0](LICENSE).
