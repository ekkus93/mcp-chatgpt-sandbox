"""S21: `git_diff`/`git_log` through the real MCP tool layer.

Host-side (§20.1) — no Docker container is actually needed for these
tools themselves, but `workspace_open` (to get a `workspace_id`) does
need one, so this lives alongside the other real-Docker workspace tests
for consistency with how a real client would reach the tools.
"""

import subprocess
from pathlib import Path

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


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


async def test_git_diff_reflects_a_write_through_the_filesystem_tool(
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
        "write_file",
        {"workspace_id": workspace_id, "path": "README.md", "content": "hello\nmore\n"},
    )

    result = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    content = result.structured_content
    assert "more" in content["content"]
    assert content["truncated"] is False


async def test_git_diff_staged_only(
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

    (repo_path / "README.md").write_text("hello\nstaged\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)

    working_tree = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert working_tree.structured_content["content"] == ""

    staged = await server.call_tool("git_diff", {"workspace_id": workspace_id, "staged": True})
    assert "staged" in staged.structured_content["content"]


async def test_git_log_reports_the_initial_commit(
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

    result = await server.call_tool("git_log", {"workspace_id": workspace_id})
    entries = result.structured_content["result"]
    assert len(entries) == 1
    assert entries[0]["subject"] == "initial"
