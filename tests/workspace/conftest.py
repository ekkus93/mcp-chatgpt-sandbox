"""Fixtures for real Docker integration tests (S6).

Teardown (H1, `docs/HARDENING_SPEC.md` HR-026) removes only the specific
containers and volumes THIS test created — never a global label or name
filter. The previous version filtered by `LABEL_MANAGED`
(`mcp-chatgpt-sandbox.managed`) and by the `mcp-chatgpt-sandbox-cache-` name prefix, both of
which are the exact labels/names a real deployment's own live containers
and dependency caches carry: running this suite on a machine serving real
workspaces destroyed them, reproduced during the 2026-08-27 review.

Every container this daemon creates goes through
`docker.models.containers.ContainerCollection.run` — production code and
the handful of tests that create a second container directly both go
through it. PMOR-001/O1 now also makes production cache-volume creation
explicit through `docker.models.volumes.VolumeCollection.create` so those
volumes can carry positive ownership labels. The tracker therefore hooks
both class methods and records exact resource IDs/names created by this
pytest process. It never discovers cleanup targets by managed label or
name prefix.

RR-024 (`docs/REMEDIATION_SPEC.md` §17) remains load-bearing for legacy
implicit volume creation and direct test runs: a volume-type mount may
reference a volume that existed before this test. `_tracking_run` checks
that existence *before* the container call and tracks only a volume that
call genuinely caused Docker to create. `_tracking_volume_create` applies
the same preexistence rule to O1's new explicit creation path. A
pre-existing production-shaped volume is therefore never queued for test
teardown merely because a test references or inspects it.
"""

import contextlib
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docker
import docker.models.containers
import docker.models.volumes
import pytest
from docker.models.containers import Container

from mcp_chatgpt_sandbox.config.models import SSHConfig
from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.repositories import RepositoryRecord
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager
from mcp_chatgpt_sandbox.workspace.naming import (
    TEST_RESOURCE_NAMESPACE_ENV,
    container_matches_test_namespace,
)

#: Small, always-fast, already present on this host (verified in S6's
#: development). Needs a foreground command or it exits immediately.
TEST_IMAGE = "alpine:3.20"
KEEP_ALIVE_RUN_KWARGS = {"command": ["sleep", "3600"]}

#: Distinct from DEFAULT_IMAGE_TAG so a test build never overwrites
#: whatever an operator has actually deployed under that name.
DEFAULT_IMAGE_TEST_TAG = "mcp-chatgpt-sandbox-test-s7:24.04"

#: PSCR-009 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`): defined here, not in
#: `workspace/container_config.py`. It lived in the production package, was exported from
#: `workspace/__init__.py`, and computed `parents[3] / "docker" / "default"` — correct only
#: for the `src/` layout and resolving outside the package root once installed, the same
#: defect PSCR-002 removed from the setup wizard. It never had a production consumer; this
#: fixture is its only reader. A test may legitimately assume a checkout, so the repo-root
#: idiom already used across this suite is the honest place for it.
DEFAULT_IMAGE_BUILD_CONTEXT = Path(__file__).resolve().parents[2] / "docker" / "default"

#: Populated by the process-global class-method interceptors installed by
#: `_track_created_docker_resources` and drained by `docker_client` teardown.
_created_container_ids: list[str] = []
_created_volume_names: list[str] = []
_tracking_lock = threading.Lock()


@pytest.fixture(scope="session", autouse=True)
def _track_created_docker_resources() -> Iterator[None]:
    original_run = docker.models.containers.ContainerCollection.run
    original_volume_create = docker.models.volumes.VolumeCollection.create

    def _tracking_run(
        self: docker.models.containers.ContainerCollection, *args: Any, **kwargs: Any
    ) -> Any:
        # RR-024: recorded *before* the call, not after — a volume that
        # already exists at this instant is one this call can only ever
        # reference, never create, regardless of what it goes on to do
        # with it.
        volume_sources = [
            mount["Source"]
            for mount in (kwargs.get("mounts") or [])
            if mount.get("Type") == "volume"
        ]
        preexisting_volumes = set()
        for source in volume_sources:
            try:
                self.client.volumes.get(source)
            except docker.errors.NotFound:
                continue
            preexisting_volumes.add(source)

        result = original_run(self, *args, **kwargs)
        with _tracking_lock:
            # `detach=True` returns a `Container` to track; the
            # non-detached default (used only by one-shot,
            # `remove=True` calls in this suite) returns the captured
            # output as `bytes` instead, for a container Docker has
            # already removed itself by the time `run()` returns —
            # nothing to track. Every `run()` call creates a genuinely
            # new container regardless (there is no "reference an
            # existing container" mode), so no equivalent pre-check is
            # needed here the way there is for volumes below.
            if isinstance(result, docker.models.containers.Container) and result.id is not None:
                _created_container_ids.append(result.id)
            for source in volume_sources:
                if source not in preexisting_volumes:
                    _created_volume_names.append(source)
        return result

    def _tracking_volume_create(
        self: docker.models.volumes.VolumeCollection, *args: Any, **kwargs: Any
    ) -> Any:
        # PMOR-001: explicit cache creation must remain compatible with H1's
        # exact-resource teardown. A create call naming a volume that was
        # already present is a reference/collision, never proof this test owns
        # the pre-existing resource.
        name = kwargs.get("name")
        if name is None and args:
            name = args[0]
        preexisting = False
        if isinstance(name, str) and name:
            try:
                self.get(name)
            except docker.errors.NotFound:
                pass
            else:
                preexisting = True
        result = original_volume_create(self, *args, **kwargs)
        created_name = getattr(result, "name", None)
        if not preexisting and isinstance(created_name, str) and created_name:
            with _tracking_lock:
                _created_volume_names.append(created_name)
        return result

    docker.models.containers.ContainerCollection.run = _tracking_run  # type: ignore[method-assign]
    docker.models.volumes.VolumeCollection.create = _tracking_volume_create  # type: ignore[method-assign]
    try:
        yield
    finally:
        docker.models.containers.ContainerCollection.run = original_run  # type: ignore[method-assign]
        docker.models.volumes.VolumeCollection.create = original_volume_create  # type: ignore[method-assign]


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


def containers_in_this_context(
    client: docker.DockerClient, *, label: str, value: str
) -> list[Container]:
    """Containers matching ``label=value`` that belong to *this* test context.

    PSCR-010 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`). G3R-015 already
    gives every Docker-using pytest context its own physical namespace, but the
    ownership labels a query filters on — `workspace-id`, `repository-id` — carry
    the *logical* id, which is identical across namespaces. So a bare label query
    matches a concurrent run's container, and no test in this suite applied
    `container_matches_test_namespace` to exclude it.

    That is not hypothetical: it produced 11 CI failures and 1 local failure in
    one overlapping run, every one an `assert [<Container: ...>] == []`. Only
    absence and exact-count assertions are affected; an inclusion assertion on a
    hash-derived namespaced name is already safe, because a foreign run cannot
    produce that name.

    With no namespace active — the production shape — this filters nothing out,
    matching only containers that likewise carry no namespace label.

    `sparse=False` is explicit (PSCR2-003) because the namespace filter reads
    `container.labels`, which docker-py *refuses* on sparse objects ("Label data
    is not available for sparse objects"). It is docker-py's default, so the
    helper worked without saying so — but a later `sparse=True`, a natural
    optimisation for a query that looks like it only needs names, would have
    broken every assertion routed through here, at a distance from the change.
    """

    return [
        container
        for container in client.containers.list(
            all=True, sparse=False, filters={"label": f"{label}={value}"}
        )
        if container_matches_test_namespace(container.labels)
    ]


@pytest.fixture
def docker_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[docker.DockerClient]:
    # G3R-015: every Docker-using pytest context owns a distinct physical
    # namespace. A nested/concurrent pytest process overrides the inherited
    # value here, so local+CI and reviewer+reviewer runs cannot adopt or
    # tear down each other's same-logical-id resources.
    monkeypatch.setenv(TEST_RESOURCE_NAMESPACE_ENV, uuid.uuid4().hex)
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
