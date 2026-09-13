"""End-to-end acceptance for wizard-generated production configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import docker

from dev_workspace_mcp.config import load_config
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.setup.controller import SetupController
from dev_workspace_mcp.setup.git_policy import recommended_policy
from dev_workspace_mcp.setup.model import GitPolicyPreset, SetupRepository, SetupStepStatus


def _init_repository(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=setup@example.invalid",
            "-c",
            "user.name=setup-acceptance",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            "https://github.com/owner/example.git",
        ],
        check=True,
    )


async def test_fresh_setup_generates_operational_config_and_opens_workspace(
    tmp_path: Path,
    default_image_tag: str,
    docker_client: docker.DockerClient,
) -> None:
    del docker_client  # fixture owns exact-resource teardown for the server-created container
    repository_path = tmp_path / "work" / "example"
    _init_repository(repository_path)

    config_path = tmp_path / "config.yml"
    state_path = tmp_path / "setup-state.json"
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    controller = SetupController.create(config_path=config_path, state_path=state_path)

    controller.apply_storage(
        state_db_path=str(tmp_path / "state.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        ssh_keys_dir=str(keys_dir),
        known_hosts_path=str(tmp_path / "known_hosts"),
    )
    docker_result = controller.apply_docker(
        image=default_image_tag,
        workspace_path="/workspace",
        container_user="host",
        sudo=True,
        git_dir_mode="read-write",
        idle_stop_after="4h",
    )
    assert docker_result.status.value == "available"

    controller.apply_repositories(
        (
            SetupRepository(
                id="example",
                path=str(repository_path),
                github="owner/example",
            ),
        ),
        (),
    )
    controller.apply_ssh_github(
        ssh_mode="agent",
        ssh_scope="repository",
        shared_key=None,
        github_api_auth="none",
        github_api_token_path=None,
    )
    controller.apply_git_policy(GitPolicyPreset.RECOMMENDED)
    controller.skip_advanced()
    controller.apply_chatgpt(enabled=False)

    plan = controller.prepare_review()
    assert not config_path.exists()
    assert any("new file" in line for line in plan.summary)
    result = controller.write_reviewed_configuration()

    config = load_config(config_path)
    assert result.config == config
    assert config.git_policy == recommended_policy()
    assert config.logging.level == "INFO"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert state_path.stat().st_mode & 0o777 == 0o600

    report = controller.run_final_diagnostics()
    assert report.valid, [
        (item.code, item.status.value, item.message)
        for item in report.unresolved
    ]
    assert controller.state.statuses[controller.state.current_step] is not SetupStepStatus.FAILED

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert opened.structured_content["container_state"] == "running"
    assert opened.structured_content["repository_id"] == "example"
    assert opened.structured_content["workspace_id"] == "ws_example"

    serialized_state = state_path.read_text(encoding="utf-8").casefold()
    serialized_config = config_path.read_text(encoding="utf-8").casefold()
    assert "control_plane_api_key" not in serialized_state
    assert "control_plane_api_key" not in serialized_config
