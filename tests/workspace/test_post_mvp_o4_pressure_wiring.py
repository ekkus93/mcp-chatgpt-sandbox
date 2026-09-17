"""O4 wiring acceptance for public Docker/exec pressure diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import docker
from docker.errors import APIError

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.errors import ContainerCreateFailedError, to_tool_error
from mcp_chatgpt_sandbox.server import create_server


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
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


def test_o4_chained_docker_api_enospc_enriches_existing_structured_error() -> None:
    cause = APIError(
        "Docker API write failed",
        explanation="failed to register layer: no space left on device",
    )
    primary = ContainerCreateFailedError(
        "failed to create container",
        details={"workspace_id": "ws-pressure"},
    )
    primary.__cause__ = cause
    before = primary.to_dict()

    payload = json.loads(str(to_tool_error(primary)))

    assert payload["code"] == before["code"]
    assert payload["message"] == before["message"]
    assert payload["recoverable"] == before["recoverable"]
    assert payload["details"]["workspace_id"] == "ws-pressure"
    assert payload["details"]["pressure"] == {
        "domain": "docker_storage",
        "classification": "enospc",
        "evidence": "docker_api_explanation_enospc",
    }


async def test_o4_exec_reports_only_evidence_supported_container_enospc(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    enospc = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "printf '%s\\n' 'cp: No space left on device' >&2; exit 1",
        },
    )
    assert enospc.structured_content["exit_code"] == 1
    assert enospc.structured_content["pressure"] == {
        "domain": "container_filesystem",
        "classification": "enospc",
        "evidence": "container_stderr_enospc",
    }

    vague = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "printf '%s\\n' 'application-specific error: disk full' >&2; exit 1",
        },
    )
    assert vague.structured_content["exit_code"] == 1
    assert vague.structured_content["pressure"] is None

    successful = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "printf '%s\\n' 'No space left on device' >&2; exit 0",
        },
    )
    assert successful.structured_content["exit_code"] == 0
    assert successful.structured_content["pressure"] is None
