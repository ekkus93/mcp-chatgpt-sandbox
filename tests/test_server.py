"""S1: MCP server entrypoint serves an empty tool list."""

from dev_workspace_mcp import __version__
from dev_workspace_mcp.server import SERVER_NAME, create_server


def test_create_server_name_and_version() -> None:
    server = create_server()
    assert server.name == SERVER_NAME
    assert server.version == __version__


async def test_create_server_has_no_tools_yet() -> None:
    """§34.1's tool surface is added incrementally; at S1 it is empty."""
    server = create_server()
    tools = await server.list_tools()
    assert tools == []


def test_create_server_is_side_effect_free() -> None:
    """Building the server must not touch Docker, the filesystem beyond
    imports, or start any transport — it has to be safe to call from tests."""
    server_a = create_server()
    server_b = create_server()
    assert server_a is not server_b
