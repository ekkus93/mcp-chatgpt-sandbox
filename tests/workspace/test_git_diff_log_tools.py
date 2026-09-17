"""S21: `git_diff`/`git_log` through the real MCP tool layer.

H36 (`docs/HARDENING_TODO.md`, 3/8 and 4/8): both run in-container now
(§20.1, as amended) — a real Docker daemon and a *running* workspace are
both needed, the same as every other structured Git tool. Before H36
these ran host-side and needed neither;
`test_git_diff_requires_docker`/`test_git_log_requires_docker` and
`test_git_diff_requires_a_running_workspace`/`test_git_log_requires_a_
running_workspace` cover the new requirement.
"""

import subprocess
from pathlib import Path

import docker
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools import git as git_tools
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager


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


async def test_git_diff_requires_docker(minimal_config: ServerConfig) -> None:
    """H36: `git_diff` now runs in-container — one of the tools that
    cannot possibly work without Docker, and SHALL say so plainly (§73's
    degraded state)."""
    degraded_server = MCPServer(name="degraded")
    git_tools.register(
        degraded_server,
        db=open_db(minimal_config.state_db_path),
        docker_client=None,
        registry=RepositoryRegistry(minimal_config),
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        git_policy_config=minimal_config.git_policy,
        github_config=minimal_config.github,
        audit_config=minimal_config.audit,
    )
    with pytest.raises(ToolError, match="Docker is not reachable"):
        await degraded_server.call_tool("git_diff", {"workspace_id": "ws_example"})


async def test_git_diff_requires_a_running_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H36: same requirement `git_status`/`git_commit`/`git_pull`/
    `git_push`/`exec` already have, extended to `git_diff`."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    DockerWorkspaceManager(docker_client, db).stop(workspace_id)

    with pytest.raises(ToolError, match="WORKSPACE_NOT_RUNNING"):
        await server.call_tool("git_diff", {"workspace_id": workspace_id})


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


async def test_git_log_requires_docker(minimal_config: ServerConfig) -> None:
    """H36: `git_log` now runs in-container — one of the tools that
    cannot possibly work without Docker, and SHALL say so plainly (§73's
    degraded state)."""
    degraded_server = MCPServer(name="degraded")
    git_tools.register(
        degraded_server,
        db=open_db(minimal_config.state_db_path),
        docker_client=None,
        registry=RepositoryRegistry(minimal_config),
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        git_policy_config=minimal_config.git_policy,
        github_config=minimal_config.github,
        audit_config=minimal_config.audit,
    )
    with pytest.raises(ToolError, match="Docker is not reachable"):
        await degraded_server.call_tool("git_log", {"workspace_id": "ws_example"})


async def test_git_log_requires_a_running_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H36: same requirement `git_status`/`git_diff`/`git_commit`/
    `git_pull`/`git_push`/`exec` already have, extended to `git_log`."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    DockerWorkspaceManager(docker_client, db).stop(workspace_id)

    with pytest.raises(ToolError, match="WORKSPACE_NOT_RUNNING"):
        await server.call_tool("git_log", {"workspace_id": workspace_id})


# --- HAT-001 (docs/HARDENING_SPEC.md §15): option injection through the real ----
# --- MCP tool layer, not just the internal function -----------------------------


async def test_at_hat001_git_diff_base_cannot_write_a_host_file(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A real regression, reproduced exactly this way during the
    2026-08-27 review: `git_diff(base="--output=<path>")` called through
    the published MCP tool contract wrote an arbitrary host file and
    returned success. The published contract is what an attacker holds
    — validating only the internal function is not enough."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]
    target = tmp_path / "HAT001_DIFF_PWNED"

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_diff", {"workspace_id": workspace_id, "base": f"--output={target}"}
        )

    assert not target.exists()


async def test_at_hat001_git_log_ref_cannot_write_a_host_file(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Same regression, `git_log`'s `ref` parameter: a real `git_log`
    call with `ref="--output=<path>"` wrote a host file and returned
    `{'result': []}` with no error."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]
    target = tmp_path / "HAT001_LOG_PWNED"

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_log", {"workspace_id": workspace_id, "ref": f"--output={target}"}
        )

    assert not target.exists()
