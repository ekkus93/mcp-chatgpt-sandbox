"""S20: `git_status` through the real MCP tool layer.

Host-side (§20.1) — no Docker container is actually needed for
`git_status` itself, but `workspace_open` (to get a `workspace_id`) does
need one, so this still lives alongside the other real-Docker workspace
tests for consistency with how a real client would reach the tool.
"""

import subprocess
from pathlib import Path

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import git as git_tools


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def test_git_status_reports_clean_repo(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    result = await server.call_tool("git_status", {"workspace_id": workspace_id})
    content = result.structured_content
    assert content["branch"] == "main"
    assert content["clean"] is True
    assert content["detached_head"] is False
    assert content["conflicted"] == []
    assert content["interrupted_operation"] is None


async def test_git_status_reflects_a_write_through_the_filesystem_tool(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "new.txt", "content": "hi\n"}
    )

    result = await server.call_tool("git_status", {"workspace_id": workspace_id})
    content = result.structured_content
    assert content["clean"] is False
    assert content["untracked"] == ["new.txt"]


async def test_git_status_on_unknown_workspace_raises_not_found(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    with pytest.raises(ToolError, match="WORKSPACE_NOT_FOUND"):
        await server.call_tool("git_status", {"workspace_id": "ws_does_not_exist"})


async def test_git_status_does_not_require_docker(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§20.1's whole point: a host-side read works even when the
    container/Docker path is unavailable, as long as the workspace row
    (and its checkout) already exists."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    degraded_server = MCPServer(name="degraded")
    git_tools.register(degraded_server, db=open_db(config.state_db_path))
    result = await degraded_server.call_tool("git_status", {"workspace_id": workspace_id})
    assert result.structured_content["branch"] == "main"
