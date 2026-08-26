"""`repository_list` (§34.1, FR-001)."""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server import MCPServer

from dev_workspace_mcp.errors import translate_workspace_errors
from dev_workspace_mcp.repositories import RepositoryRegistry


@dataclass
class RepositoryListItem:
    id: str
    path: str
    github: str | None


def register(server: MCPServer, registry: RepositoryRegistry) -> None:
    @server.tool(
        name="repository_list",
        description="List repositories available to the MCP client (§7).",
    )
    @translate_workspace_errors
    async def repository_list() -> list[RepositoryListItem]:
        return [
            RepositoryListItem(id=r.id, path=str(r.canonical_path), github=r.github)
            for r in registry.list_all()
        ]
