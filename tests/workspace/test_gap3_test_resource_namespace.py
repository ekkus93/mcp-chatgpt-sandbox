"""GAP3 Y15 / G3AT-015: concurrent Docker test contexts are isolated."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import docker
import pytest

from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.workspace.caches import resolve_caches

_TEST_NAMESPACE_ENV = "DEV_WORKSPACE_MCP_TEST_RESOURCE_NAMESPACE"
_TEST_NAMESPACE_LABEL = "dev-workspace-mcp.test-namespace"

_CHILD = r"""
import json
import sys
import time
from pathlib import Path

import docker

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


db_path, repo_path, result_path, release_path = map(Path, sys.argv[1:])
client = docker.from_env()
db = open_db(db_path)
manager = DockerWorkspaceManager(client, db)
repository = RepositoryRecord(id="example", canonical_path=repo_path, github="owner/example")
record = manager.ensure_workspace(
    repository=repository,
    image="alpine:3.20",
    run_kwargs={"command": ["sleep", "3600"]},
)
container = client.containers.get(record.container_id)
result_path.write_text(
    json.dumps(
        {
            "workspace_id": record.id,
            "container_id": record.container_id,
            "container_name": record.container_name,
            "labels": container.labels,
        }
    )
)
try:
    deadline = time.monotonic() + 30.0
    while not release_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not release_path.exists():
        raise RuntimeError("parent did not release G3AT-015 child before timeout")
finally:
    try:
        manager.delete(record.id)
    finally:
        db.close()
        client.close()
"""


def _start_context(
    *,
    tmp_path: Path,
    namespace: str,
    suffix: str,
) -> tuple[subprocess.Popen[str], Path, Path]:
    result_path = tmp_path / f"result-{suffix}.json"
    release_path = tmp_path / f"release-{suffix}"
    env = os.environ.copy()
    env[_TEST_NAMESPACE_ENV] = namespace
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CHILD,
            str(tmp_path / f"state-{suffix}.db"),
            str(tmp_path / "repository"),
            str(result_path),
            str(release_path),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, result_path, release_path


def _wait_result(process: subprocess.Popen[str], path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"G3AT-015 child exited before publishing its container: "
                f"rc={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        time.sleep(0.02)
    raise AssertionError("G3AT-015 child did not publish its container before timeout")


def _release(process: subprocess.Popen[str], release_path: Path) -> None:
    release_path.touch(exist_ok=True)
    try:
        stdout, stderr = process.communicate(timeout=20.0)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"G3AT-015 child did not terminate after release: stdout={stdout!r}, stderr={stderr!r}"
        ) from None
    assert process.returncode == 0, (
        f"G3AT-015 child cleanup failed: stdout={stdout!r}, stderr={stderr!r}"
    )


def _remove_if_present(client: docker.DockerClient, container_id: str | None) -> None:
    if not container_id:
        return
    try:
        client.containers.get(container_id).remove(force=True)
    except docker.errors.NotFound:
        pass


def test_g3at_015_two_live_test_contexts_do_not_adopt_or_delete_each_other(
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Two same-repository test contexts may coexist on one Docker daemon."""
    (tmp_path / "repository").mkdir()
    process_a, result_path_a, release_a = _start_context(
        tmp_path=tmp_path,
        namespace="g3at-015-a",
        suffix="a",
    )
    process_b: subprocess.Popen[str] | None = None
    release_b: Path | None = None
    result_a: dict[str, Any] | None = None
    result_b: dict[str, Any] | None = None
    try:
        result_a = _wait_result(process_a, result_path_a)
        process_b, result_path_b, release_b = _start_context(
            tmp_path=tmp_path,
            namespace="g3at-015-b",
            suffix="b",
        )
        result_b = _wait_result(process_b, result_path_b)

        assert result_a["workspace_id"] == result_b["workspace_id"] == "ws_example"
        assert result_a["container_id"] != result_b["container_id"], (
            "G3AT-015: independently namespaced contexts adopted the same Docker container"
        )
        assert result_a["container_name"] != result_b["container_name"]
        assert result_a["labels"].get(_TEST_NAMESPACE_LABEL)
        assert result_b["labels"].get(_TEST_NAMESPACE_LABEL)
        assert result_a["labels"][_TEST_NAMESPACE_LABEL] != result_b["labels"][_TEST_NAMESPACE_LABEL]

        # Teardown of context A must remove only A's physical Docker resource.
        _release(process_a, release_a)
        container_b = docker_client.containers.get(result_b["container_id"])
        container_b.reload()
        assert container_b.status == "running"
    finally:
        if process_a.poll() is None:
            release_a.touch(exist_ok=True)
            process_a.communicate(timeout=20.0)
        if process_b is not None and release_b is not None and process_b.poll() is None:
            release_b.touch(exist_ok=True)
            process_b.communicate(timeout=20.0)
        _remove_if_present(
            docker_client,
            None if result_a is None else result_a.get("container_id"),
        )
        _remove_if_present(
            docker_client,
            None if result_b is None else result_b.get("container_id"),
        )


def test_g3at_015_test_cache_volumes_are_namespaced_without_changing_production_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test namespace applies to implicit named volumes too."""
    cache = ProjectCacheEntry(name="cargo", path="$HOME/.cargo")

    monkeypatch.setenv(_TEST_NAMESPACE_ENV, "g3at-015-a")
    volume_a = resolve_caches(repository_id="example", project_caches=[cache])[0].volume_name
    monkeypatch.setenv(_TEST_NAMESPACE_ENV, "g3at-015-b")
    volume_b = resolve_caches(repository_id="example", project_caches=[cache])[0].volume_name
    assert volume_a != volume_b

    monkeypatch.delenv(_TEST_NAMESPACE_ENV, raising=False)
    production_a = resolve_caches(repository_id="example", project_caches=[cache])[0].volume_name
    production_b = resolve_caches(repository_id="example", project_caches=[cache])[0].volume_name
    assert production_a == production_b
    assert production_a.startswith("mcp-cache-example-cargo-")
