"""Fixtures for real Docker integration tests (S6). Every container this
suite creates carries the standard §50 labels, so teardown can find and
force-remove exactly what it created — nothing else on the host's Docker
daemon is touched."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.config.models import SSHConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.container_config import DEFAULT_IMAGE_BUILD_CONTEXT
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED

#: Small, always-fast, already present on this host (verified in S6's
#: development). Needs a foreground command or it exits immediately.
TEST_IMAGE = "alpine:3.20"
KEEP_ALIVE_RUN_KWARGS = {"command": ["sleep", "3600"]}

#: Distinct from DEFAULT_IMAGE_TAG so a test build never overwrites
#: whatever an operator has actually deployed under that name.
DEFAULT_IMAGE_TEST_TAG = "mcp-dev-test-s7:24.04"


@pytest.fixture(scope="session")
def default_image_tag() -> Iterator[str]:
    """Builds `docker/default/` once per test session (§9.1) — the real
    Dockerfile, not a stand-in, so a build failure here is a real defect.
    ~30s cold; free afterward via Docker's layer cache."""
    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException:
        pytest.skip("Docker is not reachable, skipping default-image tests")
        return

    try:
        client.images.build(
            path=str(DEFAULT_IMAGE_BUILD_CONTEXT), tag=DEFAULT_IMAGE_TEST_TAG, rm=True
        )
        yield DEFAULT_IMAGE_TEST_TAG
    finally:
        client.close()


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


@pytest.fixture
def ssh_config(tmp_path: Path) -> SSHConfig:
    """A minimal, valid `SSHConfig` for tests that only need
    `build_workspace_run_kwargs`'s identity/env construction (S7/S8), not
    SSH itself — `ssh_config` became a required parameter in S24."""
    return SSHConfig(keys_dir=tmp_path / "keys", known_hosts=tmp_path / "known_hosts")
