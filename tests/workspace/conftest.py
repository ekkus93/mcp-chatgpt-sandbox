"""Fixtures for real Docker integration tests (S6).

Teardown (H1, `docs/HARDENING_SPEC.md` HR-026) removes only the specific
containers and volumes THIS test created — never a global label or name
filter. The previous version filtered by `LABEL_MANAGED`
(`dev-workspace-mcp.managed`) and by the `mcp-cache-` name prefix, both of
which are the exact labels/names a real deployment's own live containers
and dependency caches carry: running this suite on a machine serving real
workspaces destroyed them, reproduced during the 2026-08-27 review.

Every container this daemon creates goes through
`docker.models.containers.ContainerCollection.run` — production code
(`workspace/manager.py`'s `_run_container`) and the handful of tests that
create a second container directly both go through it, and since
`docker.from_env()` hands back a fresh `DockerClient` per call, patching
the *class* method is the one interception point that sees every container
this process creates, regardless of which client instance triggered it.
Cache volumes (S30) are never created through an explicit
`client.volumes.create(...)` call — they come into existence implicitly,
as a side effect of the first container that mounts a not-yet-existing
named volume — so they are tracked the same way: read out of that same
`run()` call's `mounts` kwarg.
"""

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docker.models.containers
import pytest

import docker
from dev_workspace_mcp.config.models import SSHConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.container_config import DEFAULT_IMAGE_BUILD_CONTEXT
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager

#: Small, always-fast, already present on this host (verified in S6's
#: development). Needs a foreground command or it exits immediately.
TEST_IMAGE = "alpine:3.20"
KEEP_ALIVE_RUN_KWARGS = {"command": ["sleep", "3600"]}

#: Distinct from DEFAULT_IMAGE_TAG so a test build never overwrites
#: whatever an operator has actually deployed under that name.
DEFAULT_IMAGE_TEST_TAG = "mcp-dev-test-s7:24.04"

#: Populated by `_tracking_run` below (installed by `_track_created_docker_resources`,
#: an autouse fixture) and drained by `docker_client`'s teardown once per test.
#: Module-level and lock-protected rather than per-fixture-instance: the
#: monkeypatch on the shared `ContainerCollection` class is necessarily
#: process-global, so the tracking state it writes into has to be too.
_created_container_ids: list[str] = []
_created_volume_names: list[str] = []
_tracking_lock = threading.Lock()


@pytest.fixture(scope="session", autouse=True)
def _track_created_docker_resources() -> Iterator[None]:
    original_run = docker.models.containers.ContainerCollection.run

    def _tracking_run(
        self: docker.models.containers.ContainerCollection, *args: Any, **kwargs: Any
    ) -> Any:
        result = original_run(self, *args, **kwargs)
        with _tracking_lock:
            # `detach=True` returns a `Container` to track; the
            # non-detached default (used only by one-shot,
            # `remove=True` calls in this suite) returns the captured
            # output as `bytes` instead, for a container Docker has
            # already removed itself by the time `run()` returns —
            # nothing to track.
            if isinstance(result, docker.models.containers.Container) and result.id is not None:
                _created_container_ids.append(result.id)
            for mount in kwargs.get("mounts") or []:
                if mount.get("Type") == "volume":
                    _created_volume_names.append(mount["Source"])
        return result

    docker.models.containers.ContainerCollection.run = _tracking_run  # type: ignore[method-assign]
    try:
        yield
    finally:
        docker.models.containers.ContainerCollection.run = original_run  # type: ignore[method-assign]


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


def remove_tracked_docker_resources(client: docker.DockerClient) -> None:
    """Drain and remove exactly the containers/volumes tracked since the
    last drain — the same logic `docker_client`'s teardown runs, exposed
    as a plain function so `test_conftest_teardown.py` (HAT-020) can
    invoke it at a specific point in a test body rather than relying on
    pytest finalizer ordering across fixtures."""
    with _tracking_lock:
        container_ids, _created_container_ids[:] = list(_created_container_ids), []
        volume_names, _created_volume_names[:] = list(_created_volume_names), []
    for container_id in container_ids:
        with contextlib.suppress(docker.errors.NotFound):
            client.containers.get(container_id).remove(force=True)
    for volume_name in volume_names:
        with contextlib.suppress(docker.errors.NotFound):
            client.volumes.get(volume_name).remove(force=True)


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
        remove_tracked_docker_resources(client)
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
