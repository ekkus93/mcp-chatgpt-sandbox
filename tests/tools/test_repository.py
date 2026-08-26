"""S5: the `repository_list` MCP tool."""

from pathlib import Path

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


async def test_repository_list_returns_configured_repositories(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    repo_path = tmp_path / "work" / "example"
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    config = minimal_config.model_copy(
        update={
            "repositories": [RepositoryConfig(id="example", path=repo_path, github="owner/example")]
        }
    )
    server = create_server(config)

    result = await server.call_tool("repository_list", {})

    assert result.is_error is False
    assert result.structured_content == {
        "result": [{"id": "example", "path": str(repo_path.resolve()), "github": "owner/example"}]
    }


async def test_repository_list_is_empty_with_no_repositories_configured(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    result = await server.call_tool("repository_list", {})
    assert result.structured_content == {"result": []}


async def test_repository_list_tool_schema_takes_no_arguments(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tools = await server.list_tools()
    (tool,) = tools
    assert tool.name == "repository_list"
    assert tool.input_schema["properties"] == {}
