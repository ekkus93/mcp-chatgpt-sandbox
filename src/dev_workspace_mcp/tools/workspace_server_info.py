"""Read-only server health/capability projection for workspace tools."""

from __future__ import annotations

import sqlite3

import docker

from dev_workspace_mcp import __version__
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.schema_export import CONTRACT_VERSION
from dev_workspace_mcp.tools.workspace_models import ServerInfoOutput


def get_server_info(
    *,
    docker_client: docker.DockerClient | None,
    db: sqlite3.Connection,
    job_store: JobStore,
    registry: RepositoryRegistry,
) -> ServerInfoOutput:
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
            "git.commit.expected_head_sha": True,
            "git.push.remote_protection": True,
            "workspace.devcontainer": False,
        },
    )
