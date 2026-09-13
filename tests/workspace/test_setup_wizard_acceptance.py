from __future__ import annotations

import json
import subprocess
from pathlib import Path

import docker
import pytest

from dev_workspace_mcp.config import load_config
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.setup.choices import SetupChoices
from dev_workspace_mcp.setup.config_writer import commit_config_update, prepare_config_update
from dev_workspace_mcp.setup.host import StorageChoices, prepare_storage_directories
from dev_workspace_mcp.setup.materialize import materialize_setup
from dev_workspace_mcp.setup.model import SetupRepositoryState, SetupState


def _init_repository(path: Path) -> SetupRepositoryState:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            "git@github.com:example/example.git",
        ],
        check=True,
    )
    return SetupRepositoryState.from_dict(
        {
            "id": "example",
            "path": str(path),
            "github": "example/example",
            "ssh_key": "example.ed25519",
        }
    )


def _storage(tmp_path: Path) -> StorageChoices:
    return StorageChoices(
        state_db_path=tmp_path / "state" / "state.db",
        audit_path=tmp_path / "audit" / "audit.jsonl",
        keys_dir=tmp_path / "keys",
        known_hosts=tmp_path / "ssh" / "known_hosts",
    )


def _fresh_state(
    tmp_path: Path,
    repository: SetupRepositoryState,
    *,
    docker_image: str,
) -> SetupState:
    storage = _storage(tmp_path)
    return SetupState(
        config_path=str(tmp_path / "config.yml"),
        repositories=(repository,),
        choices=SetupChoices(
            state_db_path=str(storage.state_db_path),
            audit_path=str(storage.audit_path),
            keys_dir=str(storage.keys_dir),
            known_hosts=str(storage.known_hosts),
            docker_image=docker_image,
        ),
        reviewed_fields=frozenset(
            {
                "state_db_path",
                "audit",
                "docker",
                "workspace",
                "container",
                "repositories",
                "repository_roots",
                "ssh",
                "github",
                "git_policy",
            }
        ),
    )


@pytest.mark.asyncio
async def test_wizv_016_generated_config_opens_workspace_through_mcp_harness(
    tmp_path: Path,
    default_image_tag: str,
    docker_client: docker.DockerClient,
) -> None:
    """Prove wizard output is usable through the real Docker-backed MCP workspace path."""

    del docker_client  # fixture establishes isolated namespace and exact-resource cleanup
    repository = _init_repository(tmp_path / "repo")
    storage = _storage(tmp_path)
    prepare_storage_directories(storage, approved=True)
    key = storage.keys_dir / "example.ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    key.chmod(0o600)
    sensitive_sentinel = key.read_text()

    state = _fresh_state(tmp_path, repository, docker_image=default_image_tag)
    materialized = materialize_setup(state, new_config=True)
    plan = prepare_config_update(
        tmp_path / "config.yml",
        wizard_values=materialized.values,
        managed_fields=materialized.managed_fields,
    )
    result = commit_config_update(tmp_path / "config.yml", plan)
    config = load_config(result.path)

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert opened.structured_content["container_state"] == "running"
    assert opened.structured_content["repository_id"] == "example"
    assert opened.structured_content["workspace_id"] == "ws_example"

    persisted = result.path.read_text() + json.dumps(state.to_dict())
    if storage.audit_path.exists():
        persisted += storage.audit_path.read_text()
    assert sensitive_sentinel not in persisted
