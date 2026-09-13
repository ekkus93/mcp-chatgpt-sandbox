# Connecting ChatGPT

This server speaks MCP over `stdio` by default. ChatGPT is a hosted client and
cannot directly spawn a local process on your machine, so this project's
supported private ChatGPT path uses OpenAI Secure MCP Tunnel: `tunnel-client`
runs locally and connects outbound to OpenAI without exposing the development
server to the public internet.

The setup wizard contains an **optional** ChatGPT connection step:

```bash
uv run dev-workspace-mcp setup
```

You may skip it entirely for local MCP clients. The wizard detects
`tunnel-client`, keeps raw runtime credentials out of resumable setup state,
shows/reviews the exact profile command before mutation, runs tunnel health
checks, and records ChatGPT UI completion separately as **USER-ATTESTED** because
the local program cannot independently inspect that hosted UI.

The manual flow below remains the recovery/reference path.

## Capability and plan check

Before connecting, confirm that your current ChatGPT account/workspace can use
write-capable custom MCP apps in developer mode.

This server is not read-only. Its MCP surface includes file writes, patches,
deletes, shell execution, Git mutation, and explicitly confirmed resource-prune
execution. A ChatGPT surface limited to search/read/fetch MCP tools is not enough
for the complete development workflow.

OpenAI plan eligibility and UI placement can change. Treat OpenAI's current docs
as the source of truth rather than hardcoded claims in this repository:

- Developer mode and MCP apps in ChatGPT:
  https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt
- ChatGPT Developer mode:
  https://developers.openai.com/api/docs/guides/developer-mode
- Secure MCP Tunnel:
  https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- Connect and test an MCP server in ChatGPT:
  https://developers.openai.com/plugins/deploy/connect-chatgpt

The wizard uses these documentation URLs as updateable constants and does not
persist subscription/plan assumptions in configuration.

## Prerequisites

- A production-valid local server config.
- Docker installed and, for final diagnostics/workspaces, reachable.
- The configured workspace image built locally.
- `tunnel-client` installed if you select the ChatGPT wizard step.
- An OpenAI Platform organization/tunnel/runtime credential appropriate for your
  current OpenAI setup.

## Wizard path

In the optional **ChatGPT connection** step:

1. Select **Configure optional ChatGPT Secure MCP Tunnel**.
2. Enter the non-secret tunnel ID and local profile name.
3. Review the server command shown by the wizard. It launches:

   ```text
   uv run --project <project-root> dev-workspace-mcp serve --config <config-path>
   ```

4. Explicitly run **Create / repair reviewed tunnel profile**.
5. Run the profile validation action.
6. Complete or refresh the custom MCP app in ChatGPT using OpenAI's current UI.
7. Mark the ChatGPT UI checkbox only after doing that hosted action. The final
   summary labels this **USER-ATTESTED**, not machine PASS.

`--no-browser` guarantees the wizard does not launch documentation URLs:

```bash
uv run dev-workspace-mcp setup --no-browser
```

It prints/shows the current URL for manual opening instead.

If ChatGPT setup is skipped, it is shown as `SKIPPED` and does not block an
otherwise valid local server.

## Manual tunnel flow

### 1. Install `tunnel-client`

```bash
brew install openai/tools/tunnel-client
tunnel-client --version
```

If Homebrew is unavailable, follow OpenAI's current Secure MCP Tunnel docs.

### 2. Create/select a tunnel and runtime credential

Open the current OpenAI Platform tunnel/API-key settings described by OpenAI's
documentation. Keep runtime credentials in the external secure environment
expected by your installed `tunnel-client`; do not put raw values into this
repository, config YAML, setup state, logs, or ChatGPT messages.

### 3. Configure a profile

```bash
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile chatgpt-sandbox \
  --tunnel-id <tunnel-id> \
  --mcp-command "uv run --project /path/to/mcp-chatgpt-sandbox dev-workspace-mcp serve --config /path/to/config.yml"
```

### 4. Verify it

```bash
tunnel-client doctor --profile chatgpt-sandbox --explain
```

Fix local server/config problems here before diagnosing ChatGPT UI behavior.

### 5. Run it

```bash
tunnel-client run --profile chatgpt-sandbox
```

Leave the tunnel client running while ChatGPT uses the server. Long-lived
operation may be placed under a service manager you already trust.

## Create or refresh the ChatGPT app

Use OpenAI's current developer-mode/app documentation as the exact UI source.
The UI has moved before, so this repository intentionally describes the general
flow rather than freezing a brittle settings path:

1. Enable developer mode according to current OpenAI instructions.
2. Create a custom app/MCP connection.
3. Choose **Tunnel** as the connection method when offered.
4. Select the tunnel or enter its ID.
5. Scan/review discovered tools.
6. Compare them with [`docs/TOOLS.md`](TOOLS.md), including write-capable tools.
7. Enable/publish according to workspace policy.
8. Start a new conversation with the app/tool enabled.

If only read/fetch capabilities are available, do not treat that as successful
validation for this write-capable development server.

## Validate end to end

Start read-only:

1. `repository_list` shows configured IDs.
2. `workspace_open` returns a running test workspace.
3. `read_file` can read a small known file.
4. `git_status` reports the expected state.

Then, only on a disposable branch/test repository:

1. make a harmless `write_file`/`patch_file` change;
2. inspect it with `git_diff`;
3. create a commit with `git_commit`;
4. push with structured `git_push` under the configured Git policy.

That final push proves the ChatGPT app, tunnel, local daemon, Docker workspace,
repository mount, remote identity, and deploy key are all connected.

## Refreshing tool metadata

Refresh/rescan the ChatGPT app after MCP-visible changes such as tool names,
descriptions, input/output schemas, annotations, server instructions, or auth
metadata. Restart the local server/tunnel first, then use ChatGPT's current
Refresh/Scan/Update UI and review the resulting tool permissions.

## Troubleshooting

- **Wizard says `tunnel-client` is unavailable.** Install it using OpenAI's
  current Secure MCP Tunnel instructions, or skip the optional step.
- **`tunnel-client doctor` cannot start the server command.** Run the exact
  displayed `dev-workspace-mcp serve --config ...` command directly and fix the
  local error first.
- **ChatGPT cannot see the tunnel.** Reconfirm tunnel ID, Platform organization,
  runtime credential context, and ChatGPT workspace using current OpenAI docs.
- **Tools are stale/missing.** Restart the tunnel and rescan the app; compare
  with [`docs/TOOLS.md`](TOOLS.md) and [`schemas/tools.json`](../schemas/tools.json).
- **Read tools work but write tools are unavailable.** The connected ChatGPT
  surface may be read/fetch-only; that is insufficient for this project's full
  workflow.
- **`workspace_open` reports a Docker image problem.** Build the image selected
  by `docker.default_image` or use the wizard's explicit image-build action.
- **Git push fails.** Recheck repository deploy-key installation, canonical
  `owner/repo`, and Git policy.

## Security notes

Keep this server private, prefer repository-scoped deploy keys and SSH-agent
mode, review every connected tool permission, and use disposable branches/test
repositories for first validation. Secure MCP Tunnel prevents a public inbound
server exposure; it does not make the development tool surface harmless.
