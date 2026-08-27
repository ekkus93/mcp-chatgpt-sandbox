"""MCP server construction and transport startup.

`create_server()` builds the `MCPServer` instance and registers every
structured tool from `docs/SPEC.md` §34.1. Tool registration grows as later
phases implement each tool; until S5, the server legitimately served an
empty tool list.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from mcp.server import MCPServer

import docker
from dev_workspace_mcp import __version__
from dev_workspace_mcp.config.loader import ensure_known_hosts_file_exists
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.tools import execution as execution_tools
from dev_workspace_mcp.tools import filesystem as filesystem_tools
from dev_workspace_mcp.tools import git as git_tools
from dev_workspace_mcp.tools import repository as repository_tools
from dev_workspace_mcp.tools import workspace as workspace_tools

Transport = Literal["stdio", "sse", "streamable-http"]

SERVER_NAME = "dev-workspace-mcp"


def create_server(config: ServerConfig) -> MCPServer:
    """Build the MCP server for the given validated configuration.

    Opens (creating if needed) the state database at `config.state_db_path`
    (§68) — a real, expected side effect once tools depend on it (S6
    onward), not a departure from "safe to call repeatedly": `open_db`'s
    migrations are idempotent, and each call gets its own connection to the
    same file. Also ensures `config.ssh.known_hosts` exists on disk (§19.3) —
    a filesystem precondition `workspace_open`'s mandatory known_hosts bind
    mount depends on, needed here rather than only in `load_config` because
    not every caller builds `config` by way of `load_config` (tests, for
    instance, construct `ServerConfig` directly). Does not touch Docker or
    start a transport.
    """
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Docker-backed development workspaces for authorized local "
            "repositories. See docs/SPEC.md for the full tool contract."
        ),
    )
    registry = RepositoryRegistry(config)
    ensure_known_hosts_file_exists(config)
    db = open_db(config.state_db_path)
    docker_client = _try_docker_client()
    _register_tools(server, config, registry, db, docker_client)
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
) -> None:
    """Register the §34.1 tool surface. Grows one module per task from
    S9 (filesystem) onward; `repository_list` is the first (S5)."""
    repository_tools.register(server, registry)
    filesystem_tools.register(
        server,
        db=db,
        filesystem_config=config.filesystem,
        repository_roots=config.repository_roots,
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
        repository_roots=config.repository_roots,
        ssh_agent_runtime_dir=config.state_db_path.parent / "ssh-agents",
    )
    execution_tools.register(
        server,
        db=db,
        docker_client=docker_client,
        container_config=config.container,
        execution_config=config.execution,
        state_dir=config.state_db_path.parent,
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


def run_server(server: MCPServer, transport: Transport = "stdio") -> None:
    """Start serving on the given transport. Blocks until the server exits."""
    server.run(transport=transport)
