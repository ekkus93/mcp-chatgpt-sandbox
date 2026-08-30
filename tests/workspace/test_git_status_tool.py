"""S20: `git_status` through the real MCP tool layer.

H36 (`docs/HARDENING_TODO.md`, 2/8): runs in-container now (§20.1, as
amended) — a real Docker daemon and a *running* workspace are both
needed, the same as every other structured Git tool. Before H36 this ran
host-side and needed neither; `test_git_status_requires_docker` and
`test_git_status_requires_a_running_workspace` below replace the old
`test_git_status_does_not_require_docker`, which asserted exactly the
behavior this migration removes.
"""

import os
import subprocess
from pathlib import Path

import docker
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import git as git_tools
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


async def test_at_gr006_a_non_utf8_path_raises_rather_than_reports_a_mangled_one(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """GR-006 (`docs/GAP_SPEC.md`): `run_git_in_container` (`git/
    service.py`) — the shared read path behind `git_status`/`git_diff`/
    `git_log`/`read_committed_files` — checked `stdout_truncated` but not
    `stdout_lossy`, so a non-UTF-8 path (real on Linux; Git does not
    require valid UTF-8 filenames) reached the client silently
    U+FFFD-substituted, the same defect class AR-001
    (`docs/AUDIT_SPEC.md`) closed at `git_commit`'s two index-capture
    sites, at a surface AR-001 didn't cover.

    `os.fsdecode(b"bad\\xffname.txt")`, not a path containing a literal
    `\N{REPLACEMENT CHARACTER}`: `decode_utf8_lossy` (`execution/
    service.py`) tries a strict decode first specifically so a genuine
    U+FFFD already in the source bytes isn't misreported as lossy — a
    literal-U+FFFD path would not set `stdout_lossy` at all and this
    test would pass against the unfixed code."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    bad_path = repo_path / os.fsdecode(b"bad\xffname.txt")
    bad_path.write_bytes(b"content\n")

    with pytest.raises(ToolError, match="COMMAND_FAILED") as excinfo:
        await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert "not valid UTF-8" in str(excinfo.value)


async def test_git_status_on_unknown_workspace_raises_not_found(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    with pytest.raises(ToolError, match="WORKSPACE_NOT_FOUND"):
        await server.call_tool("git_status", {"workspace_id": "ws_does_not_exist"})


async def test_git_status_requires_docker(minimal_config: ServerConfig) -> None:
    """H36: `git_status` now runs in-container — it is one of the tools
    that cannot possibly work without Docker, and SHALL say so plainly
    (§73's degraded state) rather than hang or crash with an unrelated
    error. Replaces the old `test_git_status_does_not_require_docker`,
    which asserted the opposite — the exact behavior this migration
    removes."""
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
        await degraded_server.call_tool("git_status", {"workspace_id": "ws_example"})


async def test_git_status_requires_a_running_workspace(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H36: same requirement `git_commit`/`git_pull`/`git_push`/`exec`
    already have, extended to `git_status` now that it too runs
    in-container."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    DockerWorkspaceManager(docker_client, db).stop(workspace_id)

    with pytest.raises(ToolError, match="WORKSPACE_NOT_RUNNING"):
        await server.call_tool("git_status", {"workspace_id": workspace_id})
