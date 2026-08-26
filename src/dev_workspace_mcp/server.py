"""MCP server construction and transport startup.

`create_server()` builds the `MCPServer` instance and registers every
structured tool from `docs/SPEC.md` §34.1. Tool registration grows as later
phases implement each tool; until then the server legitimately serves an
empty tool list, which is exactly what Phase 1 (S1) requires.

Transport selection follows §56: `stdio` for a local subprocess client,
`streamable-http` for a remote client such as ChatGPT, which additionally
requires authentication before it is used outside a private network — that
hardening lands with the tools it protects, not here.
"""

from __future__ import annotations

from typing import Literal

from mcp.server import MCPServer

from dev_workspace_mcp import __version__

Transport = Literal["stdio", "sse", "streamable-http"]

SERVER_NAME = "dev-workspace-mcp"


def create_server() -> MCPServer:
    """Build the MCP server.

    Registers no tools yet (§34.1's tool surface is added incrementally as
    each is implemented, starting with the filesystem tools in S10). Pure
    and side-effect free: it does not start a transport or touch Docker, so
    it is safe to call from tests.
    """
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Docker-backed development workspaces for authorized local "
            "repositories. See docs/SPEC.md for the full tool contract."
        ),
    )
    _register_tools(server)
    return server


def _register_tools(server: MCPServer) -> None:
    """Register the §34.1 tool surface. Empty until S10 onward."""


def run_server(server: MCPServer, transport: Transport = "stdio") -> None:
    """Start serving on the given transport. Blocks until the server exits."""
    server.run(transport=transport)
