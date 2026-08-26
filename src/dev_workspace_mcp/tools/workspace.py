"""`workspace_open`, `workspace_info`, `server_info` (§8.3, §25, §33.3,
§34.1, §62).

`workspace_open` is the point where S5 (repository registry), S6 (Docker
lifecycle), S7 (container identity), and S8 (mounts) actually come
together into a running container a client can use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mcp.server import MCPServer

import docker
from dev_workspace_mcp import __version__
from dev_workspace_mcp.config.models import ContainerConfig, DockerConfig, SSHConfig
from dev_workspace_mcp.errors import ContainerRuntimeFailedError, translate_workspace_errors
from dev_workspace_mcp.git_state import read_basic_git_state
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.schema_export import CONTRACT_VERSION
from dev_workspace_mcp.workspace.container_config import build_workspace_run_kwargs
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.mounts import build_workspace_mounts
from dev_workspace_mcp.workspace.naming import WorkspaceMode

DEFAULT_WORKSPACE_PATH = "/workspace"


@dataclass
class GitStateOutput:
    branch: str | None
    head_sha: str | None
    clean: bool | None
    upstream: str | None


@dataclass
class RemoteProtectionOutput:
    status: str
    """"verified" | "missing" | "unverified" (§20.4). Always "unverified"
    until an API credential and the check itself exist (§19.5, S28)."""


@dataclass
class WorkspaceInfoOutput:
    workspace_id: str
    repository_id: str
    container_state: str
    workspace_path: str
    git_dir_mode: str
    ssh_key_mode: str
    ssh_key_scope: str
    remote_protection: RemoteProtectionOutput
    git: GitStateOutput


@dataclass
class ServerInfoOutput:
    version: str
    contract_version: str
    docker_available: bool
    docker_version: str | None
    active_workspaces: int
    running_jobs: int
    configured_repositories: int
    capabilities: dict[str, bool]


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    registry: RepositoryRegistry,
    docker_client: docker.DockerClient | None,
    docker_config: DockerConfig,
    container_config: ContainerConfig,
    ssh_config: SSHConfig,
    repository_roots: list[Path],
) -> None:
    """`docker_client` is `None` when Docker was unreachable at startup
    (§73's degraded state) — the tools that need it fail clearly when
    called, rather than the server failing to start at all."""
    manager = DockerWorkspaceManager(docker_client, db) if docker_client is not None else None
    job_store = JobStore(db)

    def _require_docker() -> tuple[DockerWorkspaceManager, docker.DockerClient]:
        if manager is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot open or inspect workspaces"
            )
        return manager, docker_client

    def _info(
        record_id: str, repository_id: str, state: str, workspace_path: str
    ) -> WorkspaceInfoOutput:
        repository = registry.get(repository_id)
        git_state = read_basic_git_state(repository.canonical_path)
        return WorkspaceInfoOutput(
            workspace_id=record_id,
            repository_id=repository_id,
            container_state=state,
            workspace_path=workspace_path,
            git_dir_mode=container_config.git_dir_mode,
            ssh_key_mode=ssh_config.mode,
            ssh_key_scope=ssh_config.scope,
            remote_protection=RemoteProtectionOutput(status="unverified"),
            git=GitStateOutput(
                branch=git_state.branch,
                head_sha=git_state.head_sha,
                clean=git_state.clean,
                upstream=git_state.upstream,
            ),
        )

    @server.tool(
        name="workspace_open",
        description="Open (creating and starting as needed) a repository's workspace (§8.3).",
    )
    @translate_workspace_errors
    async def workspace_open(
        repository_id: str, mode: WorkspaceMode = "persistent"
    ) -> WorkspaceInfoOutput:
        active_manager, active_client = _require_docker()
        repository = registry.get(repository_id)
        run_kwargs = build_workspace_run_kwargs(
            container_config=container_config,
            docker_client=active_client,
            workspace_path=DEFAULT_WORKSPACE_PATH,
        )
        run_kwargs["mounts"] = build_workspace_mounts(
            host_repository_path=repository.canonical_path,
            workspace_path=DEFAULT_WORKSPACE_PATH,
            git_dir_mode=container_config.git_dir_mode,
            authorized_roots=repository_roots,
        )
        record = active_manager.ensure_workspace(
            repository=repository,
            image=docker_config.default_image,
            mode=mode,
            workspace_path=DEFAULT_WORKSPACE_PATH,
            run_kwargs=run_kwargs,
        )
        return _info(record.id, record.repository_id, record.state.value, record.workspace_path)

    @server.tool(name="workspace_info", description="Report current workspace state (§25).")
    @translate_workspace_errors
    async def workspace_info(workspace_id: str) -> WorkspaceInfoOutput:
        # `get()` only reads the DB row; it does not itself need Docker.
        # Still gated behind `_require_docker()` for the MVP — a workspace's
        # reported container_state is only as meaningful as the daemon's
        # ability to act on it, and a degraded daemon should say so plainly
        # rather than report state it cannot currently corroborate.
        active_manager, _ = _require_docker()
        record = active_manager.get(workspace_id)
        return _info(record.id, record.repository_id, record.state.value, record.workspace_path)

    @server.tool(name="server_info", description="Server version, capabilities, and health (§25).")
    @translate_workspace_errors
    async def server_info() -> ServerInfoOutput:
        docker_available = False
        docker_version: str | None = None
        if docker_client is not None:
            try:
                docker_client.ping()
                docker_available = True
                docker_version = docker_client.version().get("Version")
            except docker.errors.DockerException:
                pass

        (active_workspaces,) = db.execute(
            "SELECT COUNT(*) FROM workspace WHERE state = 'running'"
        ).fetchone()
        running_jobs = len(
            job_store.list_by_state(JobState.QUEUED, JobState.STARTING, JobState.RUNNING)
        )

        return ServerInfoOutput(
            version=__version__,
            contract_version=CONTRACT_VERSION,
            docker_available=docker_available,
            docker_version=docker_version,
            active_workspaces=active_workspaces,
            running_jobs=running_jobs,
            configured_repositories=len(registry.list_all()),
            capabilities={
                "filesystem.patch.unified_diff": True,
                "filesystem.write.expected_sha256": True,
                "exec.jobs": True,
                "git.commit.expected_head_sha": False,
                "git.push.remote_protection": False,
                "workspace.devcontainer": False,
            },
        )
