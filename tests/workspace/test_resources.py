"""S31: resource limits (§26) — `cpus`/`memory`/`pids_limit`, through the
real `workspace_open` tool, against a real Docker daemon and the real S7
default image. Verified against the container's real `HostConfig`
(`docker inspect`), not just the Python-side kwargs construction, and
`pids_limit` additionally against real kernel enforcement — a low limit
that a real fork attempt genuinely cannot exceed.
"""

import subprocess
from pathlib import Path

import docker

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example"):
    return await server.call_tool("workspace_open", {"repository_id": repository_id})


async def test_no_resources_config_means_no_limits(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    containers = docker_client.containers.list(
        all=True, filters={"label": f"mcp-chatgpt-sandbox.workspace-id={workspace_id}"}
    )
    assert len(containers) == 1
    host_config = containers[0].attrs["HostConfig"]
    assert host_config["NanoCpus"] == 0
    assert host_config["Memory"] == 0
    assert host_config["PidsLimit"] is None


async def test_cpus_memory_and_pids_limit_are_applied_to_the_real_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    config = config.model_copy(
        update={
            "resources": config.resources.model_copy(
                update={"cpus": 1.0, "memory": "256m", "pids_limit": 128}
            )
        }
    )
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    containers = docker_client.containers.list(
        all=True, filters={"label": f"mcp-chatgpt-sandbox.workspace-id={workspace_id}"}
    )
    assert len(containers) == 1
    host_config = containers[0].attrs["HostConfig"]
    assert host_config["NanoCpus"] == 1_000_000_000
    assert host_config["Memory"] == 256 * 1024 * 1024
    assert host_config["PidsLimit"] == 128


async def test_pids_limit_is_really_enforced_by_the_kernel(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    config = config.model_copy(
        update={"resources": config.resources.model_copy(update={"pids_limit": 10})}
    )
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]

    # a real fork attempt against a real, low kernel-enforced pids limit —
    # verified empirically before trusting it: exit code 2, "can't fork:
    # Resource temporarily unavailable" on a plain alpine container with
    # the same limit, before writing this test against the real image.
    result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "for i in $(seq 1 40); do sleep 100 & done; wait; echo done",
            "timeout_seconds": 30,
        },
    )
    content = result.structured_content
    assert content["exit_code"] != 0
    assert "Resource temporarily unavailable" in content["stderr_tail"]
