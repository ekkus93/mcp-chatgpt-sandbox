"""Fixtures for real Docker integration tests (S6). Every container this
suite creates carries the standard §50 labels, so teardown can find and
force-remove exactly what it created — nothing else on the host's Docker
daemon is touched."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import docker
import pytest

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED

#: Small, always-fast, already present on this host (verified in S6's
#: development). Needs a foreground command or it exits immediately.
TEST_IMAGE = "alpine:3.20"
KEEP_ALIVE_RUN_KWARGS = {"command": ["sleep", "3600"]}


@pytest.fixture
def docker_client() -> Iterator[docker.DockerClient]:
    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException as exc:
        pytest.skip(f"Docker is not reachable, skipping integration tests: {exc}")

    try:
        yield client
    finally:
        for container in client.containers.list(all=True, filters={"label": LABEL_MANAGED}):
            container.remove(force=True)
        client.close()


@pytest.fixture
def db() -> sqlite3.Connection:
    return open_db(":memory:")


@pytest.fixture
def manager(docker_client: docker.DockerClient, db: sqlite3.Connection) -> DockerWorkspaceManager:
    return DockerWorkspaceManager(docker_client, db)


@pytest.fixture
def repository(tmp_path: Path) -> RepositoryRecord:
    return RepositoryRecord(id="example", canonical_path=tmp_path, github="owner/example")
