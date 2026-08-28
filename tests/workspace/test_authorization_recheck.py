"""HR-016 (`docs/HARDENING_SPEC.md` §8), HAT-013: authorization is
re-checked against the live `RepositoryRegistry`, not persisted state
alone. A workspace opened while its repository was configured SHALL stop
being reachable through every workspace-scoped tool the moment that
repository is removed from configuration — the stale `workspace`/
`repository` rows in the state DB SHALL NOT keep it accessible.

Each test opens a workspace against one server, then builds a *second*
server from a config with the repository removed but the same
`state_db_path` — simulating an operator editing `repositories:` and
restarting the daemon, the way `test_recovery.py`'s own
`test_at_017_...` simulates a restart via a fresh manager over the same
on-disk state. `workspace_id` deliberately outlives the repository's
authorization; every tool call below reuses it against the second
server.
"""

import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

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


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_at_hat013_workspace_info_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Verified this fails the same way even *without* this task's own
    `get_authorized` change: `workspace_info`'s `_info()` helper already
    calls `registry.get(record.repository_id)` incidentally, to read the
    repository's live `canonical_path` for `git_state` — so this
    assertion held before H14 too, for an unrelated reason. Kept as
    behavioral coverage of the HAT-013 requirement regardless of
    mechanism; `test_at_hat013_git_commit_fails_after_repository_removed`
    below is the one that actually regresses without this task's fix."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("workspace_info", {"workspace_id": workspace_id})


async def test_at_hat013_exec_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool(
            "exec", {"workspace_id": workspace_id, "argv": ["true"]}
        )


async def test_at_hat013_filesystem_tools_fail_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`list_files`/`read_file`/`write_file`/`patch_file`/`delete_file`/
    `search_files` all resolve through the same `_resolve_repository_root`
    chokepoint (`tools/filesystem.py`) — one representative tool from
    that group (`list_files`) stands in for all six, the same way this
    module's own docstring justifies testing the chokepoint once."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("list_files", {"workspace_id": workspace_id})


async def test_at_hat013_git_status_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The host-side, container-free Git reads (`git_status`/`git_diff`/
    `git_log`) resolve through `resolve_repository_root` directly rather
    than through `DockerWorkspaceManager` — a distinct code path from
    `exec`'s, and the one most likely to have been missed."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("git_status", {"workspace_id": workspace_id})


async def test_at_hat013_git_commit_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`git_commit`/`git_pull`/`git_push` run in-container, resolving the
    workspace through `manager.get`/`get_authorized`, not
    `resolve_repository_root` — the third and last distinct resolution
    path in `tools/git.py`. `git_commit` specifically, not `git_push`/
    `git_pull`: those two already call `registry.get(record.repository_id)`
    incidentally (to read the repository's `github` field for remote
    validation), so they'd fail the same way even without this task's
    fix — `git_commit` never touches the registry anywhere else, making
    it the one that actually isolates `get_authorized`'s effect."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "test", "paths": ["new.txt"]},
        )


async def test_at_hat013_a_still_configured_repository_is_unaffected(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The re-check SHALL NOT be overzealous: removing an unrelated
    repository from configuration leaves a *different*, still-configured
    repository's workspace fully usable through the same live registry."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    other_repo_path = tmp_path / "work" / "other"
    other_repo_path.mkdir(parents=True)
    (other_repo_path / ".git").mkdir()
    config = config.model_copy(
        update={
            "repositories": [
                *config.repositories,
                RepositoryConfig(id="other", path=other_repo_path, github="owner/other"),
            ]
        }
    )
    server = create_server(config)
    workspace_id = await _open(server, repository_id="example")

    # "restart" with only "other" removed from config leaves "example" intact
    survives = config.model_copy(
        update={"repositories": [r for r in config.repositories if r.id != "other"]}
    )
    server_after_removal = create_server(survives)

    result = await server_after_removal.call_tool("workspace_info", {"workspace_id": workspace_id})
    assert result.structured_content["workspace_id"] == workspace_id


# --- RR-010 (`docs/REMEDIATION_SPEC.md` §5): job-scoped tools re-check too ---------


async def test_rr010_exec_status_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Reproduced empirically before this fix: a `job_id` started before
    deauthorization kept returning that job's live status indefinitely —
    `exec_status_tool` took only `job_id` and never touched the registry,
    unlike `exec`/`exec_start`, which both call `get_authorized`."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["sleep", "0.2"]}
    )
    job_id = started.structured_content["job_id"]

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("exec_status", {"job_id": job_id})


async def test_rr010_exec_output_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "hi"]}
    )
    job_id = started.structured_content["job_id"]

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("exec_output", {"job_id": job_id})


async def test_rr011_exec_cancel_fails_after_repository_removed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["sleep", "5"]}
    )
    job_id = started.structured_content["job_id"]

    deconfigured = config.model_copy(update={"repositories": []})
    server_after_removal = create_server(deconfigured)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server_after_removal.call_tool("exec_cancel", {"job_id": job_id})


async def test_rr010_job_scoped_tools_still_work_for_a_still_configured_repository(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The re-check SHALL NOT be overzealous here either — removing an
    unrelated repository leaves a still-configured one's jobs fully
    reachable through the same live-as-of-restart registry."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    other_repo_path = tmp_path / "work" / "other"
    other_repo_path.mkdir(parents=True)
    (other_repo_path / ".git").mkdir()
    config = config.model_copy(
        update={
            "repositories": [
                *config.repositories,
                RepositoryConfig(id="other", path=other_repo_path, github="owner/other"),
            ]
        }
    )
    server = create_server(config)
    workspace_id = await _open(server, repository_id="example")
    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "hi"]}
    )
    job_id = started.structured_content["job_id"]

    survives = config.model_copy(
        update={"repositories": [r for r in config.repositories if r.id != "other"]}
    )
    server_after_removal = create_server(survives)

    result = await server_after_removal.call_tool("exec_status", {"job_id": job_id})
    assert result.structured_content["job_id"] == job_id
