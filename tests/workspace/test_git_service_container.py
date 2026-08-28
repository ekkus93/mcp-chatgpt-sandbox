"""S19/H36 (`docs/HARDENING_TODO.md`): `run_git_in_container` — the
in-container replacement for `run_git_host_side` — against a real Docker
daemon and the real S7 default image.
"""

import subprocess
from pathlib import Path

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.service import run_git_in_container
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


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


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_run_git_in_container_returns_stdout_and_exit_code(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    result = await run_git_in_container(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )

    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() == "main"


async def test_run_git_in_container_reports_failure(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    result = await run_git_in_container(
        ["rev-parse", "no-such-ref"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )

    assert not result.ok
    assert result.returncode != 0
    assert result.stderr != ""


async def test_run_git_in_container_applies_no_host_side_neutralization(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    """H36: this is the whole point of the migration — a code-executing
    config key (`filter.*.clean`) that `run_git_host_side` would have
    neutralized (HR-002, `_discover_code_executing_overrides`) now
    genuinely executes, in-container, exactly as `exec("git status")`
    already could. Reproduced directly rather than assumed: a
    `.gitattributes` binding every file to a `clean` filter that writes a
    marker file, then a real `git status` run through
    `run_git_in_container` — followed by a direct (non-`git`) exec that
    confirms the marker file the filter driver wrote actually exists."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    subprocess.run(
        ["git", "config", "filter.marker.clean", "sh -c 'touch /tmp/h36-marker; cat'"],
        cwd=repo_path,
        check=True,
    )
    (repo_path / ".gitattributes").write_text("* filter=marker\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "attrs"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("changed\n")

    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    check = await run_git_in_container(
        ["status", "--porcelain"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )
    assert check.ok

    # Not a `git` invocation — a direct exec, the same way `exec("test -e
    # ...")` would run — confirming the filter driver's side effect
    # actually happened, not merely that the config key survived.
    marker_exists = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["test", "-e", "/tmp/h36-marker"]}
    )
    assert marker_exists.structured_content["exit_code"] == 0
