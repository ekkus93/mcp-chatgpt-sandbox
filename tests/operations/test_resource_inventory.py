"""Post-MVP Operations O2 / PMOAT-002 and PMOAT-003 inventory acceptance."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from docker.errors import DockerException

from dev_workspace_mcp.db.schema import apply_migrations
from dev_workspace_mcp.operations.resources import (
    LABEL_RESOURCE_KIND,
    RESOURCE_KIND_CACHE_VOLUME,
    ResourceKind,
)
from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_WORKSPACE_ID,
)


class _MutationRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, name: str) -> None:
        self.calls.append(name)
        raise AssertionError(f"read-only inventory attempted Docker mutation: {name}")


class _FakeContainer:
    def __init__(
        self,
        *,
        container_id: str,
        labels: dict[str, str],
        status: str = "exited",
        reload_error: BaseException | None = None,
        recorder: _MutationRecorder,
    ) -> None:
        self.id = container_id
        self.name = f"container-{container_id}"
        self.labels = labels
        self.status = status
        self.attrs: dict[str, Any] = {"Config": {"Labels": labels}, "State": {"Status": status}}
        self._reload_error = reload_error
        self._recorder = recorder

    def reload(self) -> None:
        if self._reload_error is not None:
            raise self._reload_error

    def stop(self, *_args: object, **_kwargs: object) -> None:
        self._recorder.record("container.stop")

    def kill(self, *_args: object, **_kwargs: object) -> None:
        self._recorder.record("container.kill")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._recorder.record("container.remove")


class _FakeVolume:
    def __init__(
        self,
        *,
        name: str,
        labels: dict[str, str],
        reload_error: BaseException | None = None,
        recorder: _MutationRecorder,
    ) -> None:
        self.name = name
        self.attrs: dict[str, Any] = {"Name": name, "Labels": labels}
        self._reload_error = reload_error
        self._recorder = recorder

    def reload(self) -> None:
        if self._reload_error is not None:
            raise self._reload_error

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._recorder.record("volume.remove")


class _FakeContainerCollection:
    def __init__(self, items: list[_FakeContainer]) -> None:
        self._items = items

    def list(self, *_args: object, **_kwargs: object) -> list[_FakeContainer]:
        return list(self._items)

    def prune(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only inventory attempted container prune")


class _FakeVolumeCollection:
    def __init__(self, items: list[_FakeVolume]) -> None:
        self._items = items

    def list(self, *_args: object, **_kwargs: object) -> list[_FakeVolume]:
        return list(self._items)

    def prune(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only inventory attempted volume prune")


class _FakeDocker:
    def __init__(
        self,
        *,
        containers: list[_FakeContainer] | None = None,
        volumes: list[_FakeVolume] | None = None,
    ) -> None:
        self.containers = _FakeContainerCollection(containers or [])
        self.volumes = _FakeVolumeCollection(volumes or [])


def _inventory_module() -> ModuleType:
    module_name = "dev_workspace_mcp.operations.inventory"
    assert importlib.util.find_spec(module_name) is not None, (
        "PMOAT-002 RED: O2 has no internal bounded read-only inventory service yet"
    )
    return importlib.import_module(module_name)


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    apply_migrations(connection)
    return connection


def _managed_container_labels(*, workspace_id: str, repository_id: str = "repo") -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_REPOSITORY_ID: repository_id,
    }


def _managed_cache_labels(*, repository_id: str = "repo") -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
        LABEL_REPOSITORY_ID: repository_id,
    }


def test_pmoat_002_inventory_is_bounded_and_side_effect_free(tmp_path: Path) -> None:
    inventory = _inventory_module()
    recorder = _MutationRecorder()
    containers = [
        _FakeContainer(
            container_id=f"c{index}",
            labels=_managed_container_labels(workspace_id=f"ws{index}"),
            recorder=recorder,
        )
        for index in range(8)
    ]
    docker_client = _FakeDocker(containers=containers)

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=docker_client,
        state_db_path=tmp_path / "state.db",
        limits=inventory.InventoryLimits(max_resources=3, max_inspections=4, max_diagnostics=3),
    ).collect()

    assert len(result.resources) == 3
    assert result.inspected_count <= 4
    assert result.truncated is True
    assert result.incomplete is True
    assert recorder.calls == []
    assert {item.lifecycle_state.value for item in result.resources} == {"orphaned_owned"}


def test_pmoat_002_per_resource_inspection_failure_is_isolated(tmp_path: Path) -> None:
    inventory = _inventory_module()
    recorder = _MutationRecorder()
    broken = _FakeContainer(
        container_id="broken",
        labels=_managed_container_labels(workspace_id="ws-broken"),
        reload_error=DockerException("token=super-secret should not be echoed"),
        recorder=recorder,
    )
    healthy = _FakeContainer(
        container_id="healthy",
        labels=_managed_container_labels(workspace_id="ws-healthy"),
        recorder=recorder,
    )

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_FakeDocker(containers=[broken, healthy]),
        state_db_path=tmp_path / "state.db",
    ).collect()

    assert any(item.resource_id == "healthy" for item in result.resources)
    assert any(item.resource_id == "broken" and item.lifecycle_state.value == "degraded" for item in result.resources)
    diagnostic = next(item for item in result.diagnostics if item.code == "docker_container_inspection_failed")
    assert "super-secret" not in diagnostic.message
    assert "token=" not in diagnostic.message
    assert result.incomplete is True
    assert recorder.calls == []


def test_pmoat_003_job_log_and_cleanup_bytes_are_accounted_not_measured(tmp_path: Path) -> None:
    inventory = _inventory_module()
    connection = _db()
    with connection:
        connection.execute(
            "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
            ("repo", "/repo"),
        )
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES (?, ?, 'persistent', '/workspace', 'running', '2026-09-02T00:00:00Z', '2026-09-02T00:00:00Z')
            """,
            ("ws", "repo"),
        )
        connection.execute(
            """
            INSERT INTO job
                (id, workspace_id, command, cwd, state, stdout_total_bytes, stderr_total_bytes)
            VALUES (?, ?, 'echo hi', '/workspace', 'succeeded', 11, 19)
            """,
            ("job-live", "ws"),
        )
        connection.execute(
            """
            INSERT INTO job_log_cleanup
                (job_id, workspace_id, created_at, bytes_hint)
            VALUES (?, ?, '2026-09-02T00:00:00Z', 41)
            """,
            ("job-debt", "ws"),
        )

    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_FakeDocker(),
        state_db_path=tmp_path / "state.db",
    ).collect()

    job_log = next(item for item in result.resources if item.resource_id == "job-live")
    debt = next(item for item in result.resources if item.resource_id == "job-debt")
    assert job_log.kind is ResourceKind.JOB_LOG_DIRECTORY
    assert job_log.bytes.value == 30
    assert job_log.bytes.provenance.value == "accounted"
    assert debt.kind is ResourceKind.JOB_LOG_CLEANUP
    assert debt.bytes.value == 41
    assert debt.bytes.provenance.value == "accounted"


def test_pmoat_003_unavailable_state_metrics_never_become_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _inventory_module()

    def unavailable(_path: Path | str) -> object:
        raise OSError(13, "permission denied at /secret/state")

    monkeypatch.setattr(inventory.os, "statvfs", unavailable)

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_FakeDocker(),
        state_db_path=tmp_path / "missing" / "state.db",
    ).collect()

    state = next(item for item in result.resources if item.kind is ResourceKind.STATE_STORAGE)
    observations = [state.bytes, *state.metrics.values()]
    unavailable_observations = [item for item in observations if item.provenance.value == "unavailable"]
    assert unavailable_observations
    assert all(item.value is None for item in unavailable_observations)
    assert all(item.value != 0 for item in unavailable_observations)
    assert result.incomplete is True
    assert all("/secret/state" not in diagnostic.message for diagnostic in result.diagnostics)


def test_pmoat_002_inventory_enumerates_cache_and_ssh_tracking_without_mutation(
    tmp_path: Path,
) -> None:
    inventory = _inventory_module()
    connection = _db()
    with connection:
        connection.execute(
            "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
            ("repo", "/repo"),
        )
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES (?, ?, 'persistent', '/workspace', 'stopped', '2026-09-02T00:00:00Z', '2026-09-02T00:00:00Z')
            """,
            ("ws", "repo"),
        )
        connection.execute(
            """
            INSERT INTO ssh_agent (workspace_id, pid, socket_path, created_at)
            VALUES (?, 1234, '/tmp/agent.sock', '2026-09-02T00:00:00Z')
            """,
            ("ws",),
        )

    recorder = _MutationRecorder()
    volume = _FakeVolume(
        name="cache-owned",
        labels=_managed_cache_labels(),
        recorder=recorder,
    )
    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_FakeDocker(volumes=[volume]),
        state_db_path=tmp_path / "state.db",
    ).collect()

    by_kind = {item.kind for item in result.resources}
    assert ResourceKind.CACHE_VOLUME in by_kind
    assert ResourceKind.SSH_AGENT in by_kind
    assert ResourceKind.STATE_STORAGE in by_kind
    assert recorder.calls == []
