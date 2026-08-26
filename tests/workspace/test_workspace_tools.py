"""S11: `workspace_open`, `workspace_info`, `server_info` end to end —
the point where S5-S8 come together into a real running container
(§8.3, §25, AT-032)."""

import subprocess
from pathlib import Path

import pytest
from mcp.server import MCPServer

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.schema_export import CONTRACT_VERSION
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import workspace as workspace_tools


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


def _server_with_no_docker(config: ServerConfig) -> MCPServer:
    """Registers the workspace tools with `docker_client=None`, matching
    §73's degraded-startup path — without going through `create_server`,
    which would itself try (and, in this sandbox, succeed) to reach the
    real Docker daemon."""
    server = MCPServer(name="t")
    db = open_db(config.state_db_path)
    registry = RepositoryRegistry(config)
    workspace_tools.register(
        server,
        db=db,
        registry=registry,
        docker_client=None,
        docker_config=config.docker,
        container_config=config.container,
        ssh_config=config.ssh,
        workspace_config=config.workspace,
        audit_config=config.audit,
        repository_roots=config.repository_roots,
    )
    return server


async def test_workspace_open_creates_a_running_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    info = result.structured_content
    assert info["container_state"] == "running"
    assert info["repository_id"] == "example"
    assert info["workspace_id"] == "ws_example"


async def test_at_032_reported_modes_match_configuration(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    info = result.structured_content

    assert info["git_dir_mode"] == config.container.git_dir_mode
    assert info["ssh_key_mode"] == config.ssh.mode
    assert info["ssh_key_scope"] == config.ssh.scope
    assert info["remote_protection"]["status"] == "unverified"  # no API credential configured


async def test_workspace_open_reuses_the_persistent_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    first = await server.call_tool("workspace_open", {"repository_id": "example"})
    second = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert first.structured_content["workspace_id"] == second.structured_content["workspace_id"]


async def test_workspace_info_matches_workspace_open(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
    assert info.structured_content == opened.structured_content


async def test_workspace_open_reports_git_state(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    # A real `git init`, not `_configured()`'s stub `.git` directory —
    # `git checkout`/`commit` need an actual repository structure.
    repo_path = tmp_path / "work" / "example"
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True)
    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    server = create_server(config)

    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    git_state = result.structured_content["git"]
    assert git_state["branch"] == "main"
    assert git_state["clean"] is True
    assert git_state["head_sha"] is not None


async def test_server_info_reports_real_state(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    await server.call_tool("workspace_open", {"repository_id": "example"})

    result = await server.call_tool("server_info", {})
    info = result.structured_content
    assert info["docker_available"] is True
    assert info["active_workspaces"] >= 1
    assert info["configured_repositories"] == 1
    assert info["capabilities"]["exec.jobs"] is True
    assert info["running_jobs"] == 0
    assert info["contract_version"] == CONTRACT_VERSION


# --- degraded mode (§73) — docker_client is None -----------------------------------


async def test_workspace_open_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config = minimal_config.model_copy(
        update={
            "repositories": [
                RepositoryConfig(
                    id="example", path=tmp_path / "work" / "example", github="owner/example"
                )
            ]
        }
    )
    (tmp_path / "work" / "example" / ".git").mkdir(parents=True)
    server = _server_with_no_docker(config)

    with pytest.raises(Exception, match="Docker is not reachable"):
        await server.call_tool("workspace_open", {"repository_id": "example"})


async def test_workspace_info_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    server = _server_with_no_docker(minimal_config)
    with pytest.raises(Exception, match="Docker is not reachable"):
        await server.call_tool("workspace_info", {"workspace_id": "ws_example"})


async def test_server_info_does_not_raise_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    """Unlike `workspace_open`/`workspace_info`, `server_info` degrades
    gracefully rather than raising — its whole purpose is diagnostic
    reporting, including reporting that Docker is unavailable."""
    server = _server_with_no_docker(minimal_config)
    result = await server.call_tool("server_info", {})
    assert result.structured_content["docker_available"] is False
    assert result.structured_content["docker_version"] is None
