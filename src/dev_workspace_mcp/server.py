"""MCP server construction and transport startup.

`create_server()` builds the `MCPServer` instance and registers every
structured tool from `docs/SPEC.md` §34.1. Tool registration grows as later
phases implement each tool; until S5, the server legitimately served an
empty tool list.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Literal

import docker
from mcp.server import MCPServer

from dev_workspace_mcp import __version__
from dev_workspace_mcp.audit import AuditHealth, get_audit_health
from dev_workspace_mcp.config.loader import ensure_known_hosts_file_exists
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import translate_workspace_errors
from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.maintenance import (
    DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
    MaintenanceHealth,
    MaintenanceScheduler,
)
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.tools import execution as execution_tools
from dev_workspace_mcp.tools import filesystem as filesystem_tools
from dev_workspace_mcp.tools import git as git_tools
from dev_workspace_mcp.tools import operations as operations_tools
from dev_workspace_mcp.tools import prune as prune_tools
from dev_workspace_mcp.tools import repository as repository_tools
from dev_workspace_mcp.tools import workspace as workspace_tools

Transport = Literal["stdio", "sse", "streamable-http"]

SERVER_NAME = "dev-workspace-mcp"


@dataclass
class ServerInfoOutput(workspace_tools.ServerInfoOutput):
    """Daemon health extends the existing server-info payload."""

    audit: AuditHealth
    maintenance: MaintenanceHealth


def create_server(
    config: ServerConfig,
    *,
    maintenance_interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
) -> MCPServer:
    """Build the MCP server for the given validated configuration.

    Opens (creating if needed) the state database at `config.state_db_path`
    (§68) — a real, expected side effect once tools depend on it (S6
    onward), not a departure from "safe to call repeatedly": `open_db`'s
    migrations are idempotent, and each call gets its own connection to the
    same file. Also ensures `config.ssh.known_hosts` exists on disk (§19.3) —
    a filesystem precondition `workspace_open`'s mandatory known_hosts bind
    mount depends on, needed here rather than only in `load_config` because
    not every caller builds `config` by way of `load_config` (tests, for
    instance, construct `ServerConfig` directly). Does not start Docker
    containers or a transport. G3R-014 wires maintenance into MCP lifespan:
    importing/building a server starts no background task; serving does.

    `maintenance_interval_seconds` is an internal construction seam used by
    lifecycle tests; production callers use the 60-second default.
    """
    registry = RepositoryRegistry(config)
    ensure_known_hosts_file_exists(config)
    db = open_db(config.state_db_path)
    docker_client = _try_docker_client()
    maintenance = MaintenanceScheduler(config, interval_seconds=maintenance_interval_seconds)

    @asynccontextmanager
    async def server_lifespan(_: MCPServer) -> AsyncIterator[MaintenanceScheduler]:
        maintenance.start()
        try:
            yield maintenance
        finally:
            await maintenance.stop()

    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Docker-backed development workspaces for authorized local "
            "repositories. See docs/SPEC.md for the full tool contract."
        ),
        lifespan=server_lifespan,
    )
    _register_tools(server, config, registry, db, docker_client, maintenance)
    return server


def _try_docker_client() -> docker.DockerClient | None:
    """`docker.from_env()` performs API version negotiation eagerly and
    raises immediately if the daemon is unreachable — calling it
    unconditionally would make `create_server` itself fail whenever Docker
    is down, contradicting §73's "starts degraded" contract. `None` here
    means Docker-backed tools (`workspace_open`, `workspace_info`) fail
    clearly when called, not that the server fails to start."""
    try:
        return docker.from_env()
    except docker.errors.DockerException:
        return None


def _register_tools(
    server: MCPServer,
    config: ServerConfig,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    maintenance: MaintenanceScheduler,
) -> None:
    """Register the §34.1 tool surface. Grows one module per task from
    S9 (filesystem) onward; `repository_list` is the first (S5)."""
    repository_tools.register(server, registry)
    operations_tools.register(
        server,
        db=db,
        docker_client=docker_client,
        state_db_path=config.state_db_path,
    )
    prune_tools.register(
        server,
        db=db,
        docker_client=docker_client,
        state_db_path=config.state_db_path,
        audit_config=config.audit,
    )
    filesystem_tools.register(
        server,
        db=db,
        registry=registry,
        filesystem_config=config.filesystem,
        repository_roots=config.authorized_git_dir_roots,
    )
    workspace_tools.register(
        server,
        db=db,
        registry=registry,
        docker_client=docker_client,
        docker_config=config.docker,
        container_config=config.container,
        execution_config=config.execution,
        resources_config=config.resources,
        ssh_config=config.ssh,
        workspace_config=config.workspace,
        github_config=config.github,
        git_policy_config=config.git_policy,
        audit_config=config.audit,
        repository_roots=config.authorized_git_dir_roots,
        ssh_agent_runtime_dir=config.state_db_path.parent / "ssh-agents",
    )
    _replace_server_info_with_daemon_health(
        server, config, registry, db, docker_client, maintenance
    )
    execution_tools.register(
        server,
        db=db,
        docker_client=docker_client,
        registry=registry,
        container_config=config.container,
        execution_config=config.execution,
        state_dir=config.state_db_path.parent,
        audit_config=config.audit,
        ssh_config=config.ssh,
    )
    git_tools.register(
        server,
        db=db,
        docker_client=docker_client,
        registry=registry,
        container_config=config.container,
        execution_config=config.execution,
        git_policy_config=config.git_policy,
        github_config=config.github,
        audit_config=config.audit,
    )


def _replace_server_info_with_daemon_health(
    server: MCPServer,
    config: ServerConfig,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient | None,
    maintenance: MaintenanceScheduler,
) -> None:
    """Extend the base workspace diagnostic with daemon subsystem health.

    `workspace_tools.get_server_info` remains the single builder for the
    existing Docker/workspace/job/capability fields. Audit and maintenance
    health are process-level state, so server assembly adds them without
    duplicating or weakening those established base checks.
    """
    server.remove_tool("server_info")
    job_store = JobStore(db)

    @server.tool(name="server_info", description="Server version, capabilities, and health (§25).")
    @translate_workspace_errors
    async def server_info() -> ServerInfoOutput:
        base = workspace_tools.get_server_info(
            docker_client=docker_client,
            db=db,
            job_store=job_store,
            registry=registry,
        )
        return ServerInfoOutput(
            **asdict(base),
            audit=get_audit_health(config.audit),
            maintenance=maintenance.health(),
        )


def run_server(server: MCPServer, transport: Transport = "stdio") -> None:
    """Start serving on the given transport. Blocks until the server exits."""
    server.run(transport=transport)
