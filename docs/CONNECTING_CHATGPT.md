# Connecting ChatGPT

This server speaks MCP over `stdio` by default (`--transport stdio`,
`docs/SPEC.md` §56) — the shape almost every local MCP client (Claude
Desktop, etc.) expects: the client spawns the server as a subprocess and
talks JSON-RPC over its stdin/stdout. ChatGPT is a hosted web client; it
cannot spawn a local subprocess on your machine, so it cannot speak
`stdio` to this server directly. The two ways to bridge that are either
exposing an HTTP transport (`sse`/`streamable-http`) publicly, or using a
tunnel that connects outbound from your machine to ChatGPT with no
inbound listener at all. §56's own requirements ("no public
unauthenticated exposure", "authenticated channel", §4) point at the
second option — this project has no host/port configuration or
transport-level authentication implemented for the first (verified: no
`host`/`port` setting exists anywhere in `config/models.py` or the CLI),
so the tunnel path below is what's actually supported today.

**OpenAI's Secure MCP Tunnel** does this: a small client
(`tunnel-client`) runs on your machine, connects outbound only to
OpenAI's control plane, and forwards MCP requests to your server and
responses back — no inbound firewall rule, no public listener, ever.
This server's own `stdio` default is exactly what it wants: the tunnel
client spawns `dev-workspace-mcp serve` itself, the same way a local
`stdio` client would, just relayed over the tunnel instead of a direct
pipe.

## Prerequisites

- The server itself set up and runnable locally first — see
  [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) (`uv sync`, the workspace image
  built) and [`docs/CONFIGURATION.md`](CONFIGURATION.md) (a config file
  you can already `serve --config` successfully). Confirm this works
  *before* adding the tunnel — a tunnel around a server that doesn't
  start on its own just moves the same failure somewhere harder to
  debug.
- Docker reachable from wherever `tunnel-client` runs (§4's own
  "Intended Deployment Model": a Linux workstation or server with Docker
  installed) — the tunnel client only relays MCP requests, the daemon it
  spawns still needs Docker for every workspace operation.
- A ChatGPT plan with Developer Mode / custom connectors: Plus, Pro,
  Team, Enterprise, or Edu — not the free tier — and workspace admin
  access to turn Developer Mode on.
- An OpenAI platform account, for the tunnel ID and API key below.

## 1. Install `tunnel-client`

```bash
brew install openai/tools/tunnel-client   # macOS/Linux
tunnel-client --version
```

Or build from source — see the
[`openai/tunnel-client`](https://github.com/openai/tunnel-client) repo
if Homebrew isn't available.

## 2. Create a tunnel and a runtime API key

Both come from the OpenAI platform, not from this project:

- Tunnel ID: <https://platform.openai.com/settings/organization/tunnels>
  — needs Tunnels **Manage** permission to create one, **Use** to run
  the client against it afterward.
- Runtime API key: <https://platform.openai.com/settings/organization/api-keys>

## 3. Point the tunnel at this server

`tunnel-client` spawns the server itself via `--mcp-command`, so there's
no separate "start the server" step — the tunnel *is* what starts it.
`uv run --project <repo>` (not a shell `cd`) is what points `uv` at this
checkout regardless of the tunnel client's own working directory.
`CONTROL_PLANE_API_KEY` is the runtime key and `--tunnel-id` the tunnel
ID, both from step 2:

```bash
export CONTROL_PLANE_API_KEY="sk-..."

tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile chatgpt-sandbox \
  --tunnel-id tunnel_... \
  --mcp-command "uv run --project /path/to/mcp-chatgpt-sandbox dev-workspace-mcp serve --config /path/to/config.yml"

tunnel-client doctor --profile chatgpt-sandbox --explain
```

`doctor` SHALL report the profile healthy before continuing — it
validates the tunnel ID, the API key, and that the `--mcp-command`
actually starts cleanly, which surfaces a bad `--config` path or an
unbuilt workspace image right here rather than as a confusing failure
inside ChatGPT later.

## 4. Run it

```bash
tunnel-client run --profile chatgpt-sandbox
```

This blocks, holding the tunnel open — the same one long-lived server
process the whole session talks to, matching ordinary `stdio` MCP
semantics (not a fresh subprocess per request). Run it under whatever
this host already uses for long-running processes (`systemd`, a
persistent terminal/`screen`/`tmux` session, a container) the same way
you'd run `dev-workspace-mcp serve` itself.

## 5. Register the tunnel in ChatGPT

1. Profile picture → **Settings** → **Security and login** → turn on
   **Developer mode**. (Moved here from Connectors → Advanced in a
   recent ChatGPT update — look here first if you don't see it where an
   older guide says to.)
2. Go to **Plugins**, select **+**, give it a name and description.
3. Under **Connection**, choose **Tunnel**, then select the tunnel from
   step 2 (or paste the `tunnel_id` directly).
4. ChatGPT will list the tools this server exposes — everything in
   [`docs/TOOLS.md`](TOOLS.md) — once the connector is created.

## Notes

- **This closes §56's transport requirements without weakening §4's
  trust model.** No public listener exists at any point; `tunnel-client`
  authenticates outbound with your OpenAI runtime API key, and ChatGPT's
  own connector is scoped to your workspace. What §4 already assumes
  still holds unchanged: the repositories this server is configured
  against are yours, and the container is a workstation handed to a
  trusted client, not a sandbox for untrusted code.
- **An HTTP transport (`--transport streamable-http`/`sse`) is an
  alternative** the code already supports, but this project has no
  host/port configuration or authentication for it — you'd be
  responsible for both yourself (a reverse proxy, TLS, and real auth in
  front of it) before it would satisfy §56 at all. The tunnel above
  needs none of that, which is why it's the recommended path rather than
  one of two equally-supported options.
- Config changes need the tunnel restarted (`tunnel-client run` again)
  the same way editing a `stdio` client's own launch config would —
  nothing here auto-reloads.

Sources: [OpenAI — Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) · [openai/tunnel-client](https://github.com/openai/tunnel-client) · [OpenAI Help Center — Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
