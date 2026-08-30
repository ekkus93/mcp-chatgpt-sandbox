# mcp-chatgpt-sandbox

A private MCP server giving ChatGPT and other MCP-capable clients a
Docker-backed development workstation for your own repositories — each
repository opens into its own container, where the client can read/write
files, run commands, and drive Git (status/diff/log/commit/pull/push)
against a real, isolated checkout, without ever touching your host
directly.

See [`docs/SPEC.md`](docs/SPEC.md) for the full specification and
[`docs/TODO.md`](docs/TODO.md) for implementation status.
For local development, see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md);
for configuring a server instance, see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md); for connecting a
running server to ChatGPT, see
[`docs/CONNECTING_CHATGPT.md`](docs/CONNECTING_CHATGPT.md).
For the trust/threat model this design assumes, see
[`docs/SECURITY.md`](docs/SECURITY.md).
