# Connecting ChatGPT

This server speaks MCP over `stdio` by default (`--transport stdio`,
`docs/SPEC.md` §56). A local MCP client can spawn
`dev-workspace-mcp serve` as a subprocess and exchange JSON-RPC over
stdin/stdout.

ChatGPT is a hosted web client. It cannot directly spawn a local `stdio`
process on your machine, and this project does not implement a supported
public HTTP deployment profile with host/port config and transport-level
authentication. For this project, the supported ChatGPT path is therefore
OpenAI Secure MCP Tunnel: `tunnel-client` runs on your machine, starts or
reaches the local MCP server, and connects outbound to OpenAI without opening an
inbound firewall port or exposing this development server to the public
internet.

## Capability and plan check

Before setup, confirm that your current ChatGPT account/workspace can use
write-capable custom MCP apps in developer mode.

This server is not read-only. Its MCP tool surface includes file writes,
patches, deletes, shell execution, `git_commit`, `git_pull`, `git_push`, and
resource-prune execution after confirmation. A ChatGPT surface that only allows
search/read/fetch MCP tools is not enough for the full development workflow.

OpenAI's plan eligibility and UI placement can change. Treat these current
OpenAI docs as the source of truth rather than this repository:

- Developer mode and MCP apps in ChatGPT:
  https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt
- ChatGPT Developer mode:
  https://developers.openai.com/api/docs/guides/developer-mode
- Secure MCP Tunnel:
  https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- Connect and test an MCP server in ChatGPT:
  https://developers.openai.com/plugins/deploy/connect-chatgpt

As of this documentation update, OpenAI's Help Center distinguishes full MCP
support from read/fetch-only MCP support on some plans. If your account can
only connect read/fetch MCP tools, do not use that as validation for this
server's write-capable workflow.

## Prerequisites

- A working local `mcp-chatgpt-sandbox` server configuration. Confirm
  `uv run dev-workspace-mcp serve --config <path>` starts cleanly before adding
  a tunnel.
- Docker reachable from the host running `tunnel-client`. The tunnel only
  relays MCP traffic; the server still needs Docker for every workspace.
- A workspace image built locally, usually:

  ```bash
  docker build -t mcp-dev-ubuntu:24.04 docker/default/
  ```

- A ChatGPT account/workspace with developer-mode access for write-capable
  custom MCP apps.
- An OpenAI Platform organization with permission to create/use a tunnel, plus
  a runtime credential for `tunnel-client`.

## 1. Install `tunnel-client`

```bash
brew install openai/tools/tunnel-client   # macOS/Linux with Homebrew
tunnel-client --version
```

If Homebrew is not available, use the installation path from OpenAI's current
Secure MCP Tunnel documentation or the `openai/tunnel-client` repository.

## 2. Create a tunnel and runtime credential

Create or select a tunnel in OpenAI Platform tunnel settings:

```text
https://platform.openai.com/settings/organization/tunnels
```

Create the runtime credential used by the tunnel client in OpenAI Platform API
key settings:

```text
https://platform.openai.com/settings/organization/api-keys
```

The platform permissions for managing tunnel definitions and running/selecting
a tunnel are separate from ChatGPT developer-mode access. Make sure the
Platform organization, tunnel, credential, and ChatGPT workspace you intend to
use belong to the expected account/workspace context.

## 3. Configure the tunnel profile

`tunnel-client` can spawn this `stdio` MCP server itself. Use `uv run --project`
so the command works regardless of the tunnel client's current working
directory.

Before running the command below, set the tunnel runtime credential in the
environment variable required by your installed `tunnel-client` version. Do not
commit the assignment or paste its value into documentation, shell transcripts,
or ChatGPT conversations.

```bash
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile chatgpt-sandbox \
  --tunnel-id <tunnel-id> \
  --mcp-command "uv run --project /path/to/mcp-chatgpt-sandbox dev-workspace-mcp serve --config /path/to/config.yml"
```

## 4. Verify the tunnel profile

```bash
tunnel-client doctor --profile chatgpt-sandbox --explain
```

`doctor` should confirm that the tunnel profile is healthy and that the
configured MCP command starts cleanly. Fix local server problems here before
opening ChatGPT. A bad config path, unbuilt Docker image, invalid YAML file, or
missing Docker socket is easier to diagnose from `doctor` than from a failed
ChatGPT tool call.

## 5. Run the tunnel

```bash
tunnel-client run --profile chatgpt-sandbox
```

Leave this process running while ChatGPT uses the server. For long-lived use,
run it under the service manager you already trust on this host, such as
`systemd`, `tmux`, `screen`, or a supervised container.

Config changes require restarting the tunnel profile because the tunnel command
spawns the MCP server process. Tool-schema changes also require refreshing or
rescanning the app in ChatGPT; see the refresh section below.

## 6. Create or refresh the ChatGPT app

Use OpenAI's current ChatGPT developer-mode/app documentation as the exact UI
source. The UI has moved before. Depending on plan and workspace policy, the
relevant controls may be under workspace settings or user settings.

General flow:

1. Enable developer mode for the account/workspace according to OpenAI's current
   instructions.
2. Create a custom app/MCP connection from the current ChatGPT app settings UI.
3. When asked for the MCP connection method, choose **Tunnel**.
4. Select the tunnel from step 2, or paste the tunnel ID directly.
5. Scan/review the discovered tools.
6. Confirm that ChatGPT shows this server's expected tools from
   [`docs/TOOLS.md`](TOOLS.md), including the write-capable development tools.
7. Enable or publish the app according to your workspace policy.
8. Start a new conversation and select the app/developer-mode tool from the
   composer/tools menu.

If ChatGPT offers only a read/fetch-only MCP connection mode, do not treat that
as a successful setup for this project. This project needs write-capable custom
MCP app support to perform its core file/Git/development workflow.

## 7. Validate end to end

Start with read-only calls:

1. `repository_list` should show the configured repository IDs.
2. `workspace_open` for a test repository should return a running container.
3. `read_file` should read a small known file.
4. `git_status` should report the expected branch/head state.

Then validate write-capable behavior only in a disposable branch or test
repository:

1. `write_file` or `patch_file` should make a small harmless change.
2. `git_diff` should show that change.
3. `git_commit` should create a commit when given explicit paths.
4. `git_push` should authenticate using the repository-scoped deploy key and
   push only to the expected remote/branch allowed by policy.

That final push is the real end-to-end proof: it exercises ChatGPT app access,
Secure MCP Tunnel, the local MCP daemon, Docker, the repository mount, Git,
remote validation, and the deploy key.

## Refreshing tool metadata

Refresh the ChatGPT app after changing any MCP-visible surface:

- tool names;
- tool descriptions;
- input or output schemas;
- annotations;
- server instructions;
- authentication metadata.

General refresh flow:

1. Restart the local server/tunnel so the live MCP server exposes the new
   metadata.
2. Open the app/connection details in ChatGPT settings.
3. Use the current **Refresh**, **Scan**, or **Update** action shown by ChatGPT.
4. Review the changed tool list and permissions before enabling/publishing the
   update.
5. Start a new conversation and rerun a small validation set.

OpenAI's Help Center notes that approved app metadata can be frozen until an
admin reviews and publishes an update, so do not assume a server restart alone
updates the tools ChatGPT will call.

## Troubleshooting

- **`tunnel-client doctor` cannot start the MCP command.** Run the same
  `uv run --project ... dev-workspace-mcp serve --config ...` command directly.
  Fix config validation, Python environment, or Docker errors before returning
  to the tunnel.
- **ChatGPT cannot see the tunnel.** Confirm the tunnel ID, Platform
  organization, runtime credential, and ChatGPT workspace are the intended
  ones. Tunnel permissions and ChatGPT developer-mode access are separate.
- **Tools are missing or stale.** Restart the tunnel, then refresh/rescan the
  app in ChatGPT. Compare the discovered tool list with [`docs/TOOLS.md`](TOOLS.md)
  and [`schemas/tools.json`](../schemas/tools.json).
- **Read tools work but write tools are unavailable.** Your current ChatGPT
  surface may be read/fetch-only for MCP. Use a plan/workspace that supports
  write-capable custom MCP apps before validating this project.
- **`workspace_open` fails with a Docker image error.** Build the image named by
  `docker.default_image` in your config.
- **Git push fails.** Confirm the repository deploy key is installed on the
  correct GitHub repository with write access, the config `github:` value matches
  the remote, and branch/push policy allows the requested push.

## Security notes

This server is intended for repositories you own and for trusted AI coding
clients. It deliberately exposes powerful development actions once connected.
Keep the server private, use repository-scoped deploy keys, avoid shared
credentials, review tool permissions in ChatGPT before enabling the app, and use
throwaway branches or test repositories for first validation.

OpenAI Secure MCP Tunnel keeps this local MCP server off the public internet,
but it does not make the tool surface harmless. The connected ChatGPT app can
still request the tools this server exposes, subject to ChatGPT's UI prompts,
workspace policy, and this server's own validation and Git/resource policies.
