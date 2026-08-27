"""S1/S5/S10/S11/S12: MCP server entrypoint and tool registration."""

from dev_workspace_mcp import __version__
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.server import SERVER_NAME, create_server

#: §34.1's tool surface, as much of it as is registered through S20.
REGISTERED_TOOLS = {
    "repository_list",
    "list_files",
    "read_file",
    "write_file",
    "patch_file",
    "delete_file",
    "search_files",
    "workspace_open",
    "workspace_info",
    "server_info",
    "exec",
    "exec_start",
    "exec_status",
    "exec_output",
    "exec_cancel",
    "git_status",
}


def test_create_server_name_and_version(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    assert server.name == SERVER_NAME
    assert server.version == __version__


async def test_create_server_registers_the_spec_34_1_tool_surface(
    minimal_config: ServerConfig,
) -> None:
    """§34.1's tool surface is added incrementally, one module per task."""
    server = create_server(minimal_config)
    tools = await server.list_tools()
    assert {t.name for t in tools} == REGISTERED_TOOLS


def test_create_server_is_safe_to_call_repeatedly(minimal_config: ServerConfig) -> None:
    """Building the server does not touch Docker or start any transport.
    It does open the state database (§68) — a real, expected side effect
    once tools depend on it — but `open_db`'s migrations are idempotent,
    so calling this twice with the same config must not error."""
    server_a = create_server(minimal_config)
    server_b = create_server(minimal_config)
    assert server_a is not server_b
