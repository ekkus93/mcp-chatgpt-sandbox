"""H1 (`docs/HARDENING_SPEC.md` HR-026, HAT-020): the shared `docker_client`
fixture's teardown must remove only what the test that used it actually
created — never a global label or name filter, which would remove a real
deployment's own live containers and dependency-cache volumes.

`remove_tracked_docker_resources` (the function `docker_client`'s teardown
calls) is invoked directly here, mid-test, rather than relying on pytest
finalizer ordering across fixtures: a sentinel container/volume are
created via the `docker` CLI directly (`subprocess`), deliberately
bypassing the `ContainerCollection.run` monkeypatch that tracks
test-created resources — standing in for a real deployment's own live
container and cache volume — and a *tracked* container is created through
`docker_client` itself, so the cleanup call has something real to remove.
The assertions run between that call and the test's own cleanup, so they
observe teardown's effect directly rather than inferring it from what
survives to the end of the test.
"""

import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import docker.types
import pytest

import docker
from dev_workspace_mcp.workspace.caches import cache_volume_name
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED

from .conftest import remove_tracked_docker_resources

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def real_docker_client() -> Iterator[docker.DockerClient]:
    """A plain, untracked client — deliberately not the `docker_client`
    fixture under test, so nothing here is itself removed by the mechanism
    being verified."""
    client = docker.from_env()
    try:
        yield client
    finally:
        client.close()


def _create_sentinel_container(real_docker_client: docker.DockerClient) -> str:
    name = f"mcp-hardening-sentinel-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"{LABEL_MANAGED}=true",
            "alpine:3.20",
            "sleep",
            "3600",
        ],
        check=True,
        capture_output=True,
    )
    return name


def _create_sentinel_volume() -> str:
    name = f"mcp-cache-hardening-sentinel-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "volume", "create", name], check=True, capture_output=True)
    return name


def test_teardown_removes_only_what_it_tracked(
    docker_client: docker.DockerClient, real_docker_client: docker.DockerClient
) -> None:
    """Unit-level: the extracted removal function itself is selective —
    removes what was tracked, ignores what wasn't."""
    sentinel_container_name = _create_sentinel_container(real_docker_client)
    sentinel_volume_name = _create_sentinel_volume()
    try:
        # a *tracked* container, created through the client under test —
        # something real for the cleanup call to remove.
        tracked = docker_client.containers.run(
            "alpine:3.20",
            ["sleep", "3600"],
            labels={LABEL_MANAGED: "true"},
            detach=True,
        )
        assert tracked.id is not None
        tracked_id = tracked.id

        remove_tracked_docker_resources(docker_client)

        with pytest.raises(docker.errors.NotFound):
            real_docker_client.containers.get(tracked_id)

        # the untracked, subprocess-created sentinels are untouched.
        assert real_docker_client.containers.get(sentinel_container_name).status in (
            "running",
            "created",
        )
        assert real_docker_client.volumes.get(sentinel_volume_name) is not None
    finally:
        subprocess.run(["docker", "rm", "-f", sentinel_container_name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", sentinel_volume_name], capture_output=True)


#: A throwaway test, run as a genuinely separate pytest process below —
#: exercises `docker_client`'s real, wired-in `finally:` block (not a
#: direct call to the extracted function), so this proves the fixture is
#: actually plumbed correctly, not just that the removal logic is correct
#: in isolation. Written into `tests/workspace/` itself (not an unrelated
#: tmp dir) so the nested pytest process resolves the *real*
#: `conftest.py` and its `docker_client` fixture, then deleted in
#: `finally` regardless of outcome.
_NESTED_TEST_SOURCE = """
def test_uses_docker_client_and_creates_a_tracked_container(docker_client):
    docker_client.containers.run(
        "alpine:3.20",
        ["sleep", "3600"],
        labels={"dev-workspace-mcp.managed": "true"},
        detach=True,
    )
"""


def test_a_full_suite_run_leaves_a_foreign_sentinel_intact(
    real_docker_client: docker.DockerClient,
) -> None:
    """HAT-020, literally: run a real, separate pytest process against a
    throwaway test that uses `docker_client` the ordinary way (no direct
    call into cleanup internals), so its actual fixture teardown — not a
    stand-in — executes. A sentinel container and volume created outside
    that process SHALL both survive it."""
    sentinel_container_name = _create_sentinel_container(real_docker_client)
    sentinel_volume_name = _create_sentinel_volume()
    nested_test_path = (
        Path(__file__).parent / f"test_zz_nested_teardown_probe_{uuid.uuid4().hex[:8]}.py"
    )
    nested_test_path.write_text(_NESTED_TEST_SOURCE)
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", str(nested_test_path), "-q"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

        assert real_docker_client.containers.get(sentinel_container_name).status in (
            "running",
            "created",
        )
        assert real_docker_client.volumes.get(sentinel_volume_name) is not None
    finally:
        nested_test_path.unlink(missing_ok=True)
        subprocess.run(["docker", "rm", "-f", sentinel_container_name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", sentinel_volume_name], capture_output=True)


def test_rr024_a_production_shaped_volume_survives_being_referenced_by_a_tracked_run(
    docker_client: docker.DockerClient, real_docker_client: docker.DockerClient
) -> None:
    """RAT-020 (`docs/REMEDIATION_SPEC.md` §18), and RR-024's own exact
    reproduction: this suite's `repository` fixture always uses
    `repository_id="example"`, so `cache_volume_name(repository_id=
    "example", cache_name="cargo")` is *the* deterministic name a real
    deployment serving a repository actually configured with that id and
    a `cargo` cache would use too. A pre-existing volume under that exact
    name is created here directly (standing in for that real deployment's
    volume, with real content in it), then *referenced* — mounted, the
    same way a real cache-volume workspace open does — by a container run
    through the tracked `docker_client`. Before RR-024, being referenced
    was enough to get it queued for removal regardless of who created it;
    teardown SHALL leave both the volume and its content alone."""
    production_volume_name = cache_volume_name(repository_id="example", cache_name="cargo")
    subprocess.run(
        ["docker", "volume", "create", production_volume_name], check=True, capture_output=True
    )
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{production_volume_name}:/data",
                "alpine:3.20",
                "sh",
                "-c",
                "echo production-data > /data/marker",
            ],
            check=True,
            capture_output=True,
        )

        mount = docker.types.Mount(target="/cargo", source=production_volume_name, type="volume")
        tracked = docker_client.containers.run(
            "alpine:3.20",
            ["sleep", "3600"],
            labels={LABEL_MANAGED: "true"},
            mounts=[mount],
            detach=True,
        )
        assert tracked.id is not None

        remove_tracked_docker_resources(docker_client)

        # The volume survived teardown at all...
        assert real_docker_client.volumes.get(production_volume_name) is not None
        # ...with its real content still intact, not just re-created empty.
        readback = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{production_volume_name}:/data",
                "alpine:3.20",
                "cat",
                "/data/marker",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert readback.stdout.strip() == "production-data"

        # The container itself, genuinely created by the tracked call,
        # was still removed — this test isn't proving teardown does
        # nothing, only that it is now selective about volumes.
        with pytest.raises(docker.errors.NotFound):
            real_docker_client.containers.get(tracked.id)
    finally:
        subprocess.run(
            ["docker", "volume", "rm", "-f", production_volume_name], capture_output=True
        )
