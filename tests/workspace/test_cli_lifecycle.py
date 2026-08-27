"""S31: `reset`/`rebuild`/`delete` — §8.4's operator path, exposed as
real CLI subcommands, against a real Docker daemon and the real S7
default image.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

import docker
from dev_workspace_mcp.cli import main
from dev_workspace_mcp.config import load_config
from dev_workspace_mcp.server import create_server


def _write_config(path: Path, data: dict[str, Any]) -> Path:
    config_path = path / "config.yml"
    config_path.write_text(yaml.safe_dump(data))
    return config_path


def _configured_repo(
    minimal_config_dict: dict[str, Any], default_image_tag: str, repo_path: Path
) -> dict[str, Any]:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    minimal_config_dict["docker"]["default_image"] = default_image_tag
    minimal_config_dict["repositories"] = [
        {"id": "example", "path": str(repo_path), "github": "owner/example"}
    ]
    return minimal_config_dict


async def _open(config_path: Path) -> str:
    server = create_server(load_config(config_path))
    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    return result.structured_content["workspace_id"]


async def _exec(config_path: Path, workspace_id: str, shell: str):
    server = create_server(load_config(config_path))
    return await server.call_tool("exec", {"workspace_id": workspace_id, "shell": shell})


async def test_at_005_reset_recreates_the_container_and_preserves_the_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-005: repository contents remain intact; container-local
    installed state is removed."""
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)

    workspace_id = await _open(config_path)
    # a real host-side write to the repository (bind mount) — must
    # survive the reset...
    (repo_path / "committed.txt").write_text("keep me\n")
    # ...and a container-local marker — must NOT survive it.
    await _exec(config_path, workspace_id, "echo container-local > /tmp/marker")

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert "reset" in cli_result.output

    assert (repo_path / "committed.txt").read_text() == "keep me\n"

    marker_check = await _exec(config_path, workspace_id, "test -f /tmp/marker; echo $?")
    assert marker_check.structured_content["stdout_tail"].strip() != "0"  # gone: a fresh container

    repo_check = await _exec(config_path, workspace_id, "cat /workspace/committed.txt")
    assert repo_check.structured_content["stdout_tail"].strip() == "keep me"


async def test_reset_uses_the_project_configs_image_when_present(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, "this-image-does-not-exist:latest", repo_path)
    # `load_project_config` reads this straight off the filesystem — it
    # does not need to be committed to git.
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump({"version": 1, "image": default_image_tag})
    )
    config_path = _write_config(tmp_path, minimal_config_dict)

    await _open(config_path)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output


async def test_reset_fails_clearly_for_a_workspace_never_opened(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code != 0
    assert "nothing to reset" in cli_result.output


async def test_reset_fails_clearly_for_an_unknown_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "does-not-exist"]
    )
    assert cli_result.exit_code != 0
    assert "not authorized" in cli_result.output or "error" in cli_result.output.lower()


async def test_rebuild_recreates_the_container(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    await _exec(config_path, workspace_id, "echo x > /tmp/marker")

    cli_result = CliRunner().invoke(
        main, ["rebuild", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    marker_check = await _exec(config_path, workspace_id, "test -f /tmp/marker; echo $?")
    assert marker_check.structured_content["stdout_tail"].strip() != "0"


async def test_delete_removes_the_workspace_and_preserves_the_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    (repo_path / "committed.txt").write_text("still here\n")

    cli_result = CliRunner().invoke(
        main, ["delete", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    assert (repo_path / "committed.txt").read_text() == "still here\n"

    server = create_server(load_config(config_path))
    with pytest.raises(ToolError, match="WORKSPACE_NOT_FOUND"):
        await server.call_tool("workspace_info", {"workspace_id": workspace_id})
