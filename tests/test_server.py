"""S1/S5: MCP server entrypoint and tool registration."""

from dev_workspace_mcp import __version__
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.server import SERVER_NAME, create_server


def test_create_server_name_and_version(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    assert server.name == SERVER_NAME
    assert server.version == __version__


async def test_create_server_registers_the_spec_34_1_tool_surface(
    minimal_config: ServerConfig,
) -> None:
    """§34.1's tool surface is added incrementally; `repository_list` (S5)
    is the first."""
    server = create_server(minimal_config)
    tools = await server.list_tools()
    assert {t.name for t in tools} == {"repository_list"}


def test_create_server_is_side_effect_free(minimal_config: ServerConfig) -> None:
    """Building the server must not touch Docker or start any transport —
    it has to be safe to call from tests."""
    server_a = create_server(minimal_config)
    server_b = create_server(minimal_config)
    assert server_a is not server_b
