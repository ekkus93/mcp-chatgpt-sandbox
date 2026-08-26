"""MCP server construction and transport startup.

`create_server()` builds the `MCPServer` instance and registers every
structured tool from `docs/SPEC.md` §34.1. Tool registration grows as later
phases implement each tool; until S5, the server legitimately served an
empty tool list.
"""

from __future__ import annotations

from typing import Literal

from mcp.server import MCPServer

from dev_workspace_mcp import __version__
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.tools import repository as repository_tools

Transport = Literal["stdio", "sse", "streamable-http"]

SERVER_NAME = "dev-workspace-mcp"


def create_server(config: ServerConfig) -> MCPServer:
    """Build the MCP server for the given validated configuration.

    Pure and side-effect free beyond what building the repository registry
    requires (resolving configured/discovered paths, §7) — it does not
    touch Docker or start a transport, so it is safe to call from tests.
    """
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Docker-backed development workspaces for authorized local "
            "repositories. See docs/SPEC.md for the full tool contract."
        ),
    )
    registry = RepositoryRegistry(config)
    _register_tools(server, registry)
    return server


def _register_tools(server: MCPServer, registry: RepositoryRegistry) -> None:
    """Register the §34.1 tool surface. Grows one module per task from
    S9 (filesystem) onward; `repository_list` is the first (S5)."""
    repository_tools.register(server, registry)


def run_server(server: MCPServer, transport: Transport = "stdio") -> None:
    """Start serving on the given transport. Blocks until the server exits."""
    server.run(transport=transport)
