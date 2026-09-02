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
    OwnershipStatus,
    ResourceKind,
)
from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_WORKSPACE_ID,
)


class _Mutations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reject(self, name: str) -> None:
        self.calls.append(name)
        raise AssertionError(f"read-only inventory attempted Docker mutation: {name}")


class _Container:
    def __init__(
        self,
        container_id: str,
        labels: dict[str, str],
        mutations: _Mutations,
        *,
        reload_error: BaseException | None = None,
    ) -> None:
        self.id = container_id
        self.name = f"container-{container_id}"
        self.labels = labels
        self.status = "exited"
        self.attrs: dict[str, Any] = {"Config": {"Labels": labels}}
        self._reload_error = reload_error
        self._mutations = mutations

    def reload(self) -> None:
        if self._reload_error is not None:
            raise self._reload_error

    def stop(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.stop")

    def kill(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.kill")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.remove")


class _Volume:
    def __init__(self, name: str, labels: dict[str, str], mutations: _Mutations) -> None:
        self.name = name
        self.attrs: dict[str, Any] = {"Name": name, "Labels": labels}
        self._mutations = mutations

    def reload(self) -> None:
        return None

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("volume.remove")


class _Collection:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.list_calls: list[dict[str, Any]] = []

    def list(self, *_args: object, **kwargs: object) -> list[Any]:
        self.list_calls.append(dict(kwargs))
        limit = kwargs.get("limit")
        if isinstance(limit, int):
            return list(self._items[:limit])
        return list(self._items)

    def prune(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only inventory attempted prune")


class _Docker:
    def __init__(
        self,
        *,
        containers: list[_Container] | None = None,
        volumes: list[_Volume] | None = None,
    ) -> None:
        self.containers = _Collection(list(containers or []))
        self.volumes = _Collection(list(volumes or []))


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


def _container_labels(workspace_id: str, repository_id: str = "repo") -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_REPOSITORY_ID: repository_id,
    }


def _cache_labels(repository_id: str = "repo") -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
        LABEL_REPOSITORY_ID: repository_id,
    }


def _insert_workspace(connection: sqlite3.Connection, *, state: str) -> None:
    with connection:
        connection.execute(
            "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
            ("repo", "/repo"),
        )
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES (?, ?, 'persistent', '/workspace', ?, ?, ?)
            """,
            ("ws", "repo", state, "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"),
        )


def test_pmoat_002_inventory_is_bounded_and_side_effect_free(tmp_path: Path) -> None:
    inventory = _inventory_module()
    mutations = _Mutations()
    containers = [
        _Container(f"c{index}", _container_labels(f"ws{index}"), mutations) for index in range(8)
    ]
    docker_client = _Docker(containers=containers)

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
    assert mutations.calls == []
    assert {item.lifecycle_state.value for item in result.resources} == {"orphaned_owned"}
    assert docker_client.containers.list_calls[0]["limit"] == 4


def test_pmoat_002_inspection_bound_is_independent_of_result_bound(tmp_path: Path) -> None:
    inventory = _inventory_module()
    mutations = _Mutations()
    containers = [
        _Container(f"c{index}", _container_labels(f"ws{index}"), mutations) for index in range(6)
    ]

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_Docker(containers=containers),
        state_db_path=tmp_path / "state.db",
        limits=inventory.InventoryLimits(max_resources=10, max_inspections=2, max_diagnostics=3),
    ).collect()

    assert len(result.resources) == 2
    assert result.inspected_count == 2
    assert result.result_limit == 10
    assert result.inspection_limit == 2
    assert result.truncated is True
    assert result.incomplete is True
    assert mutations.calls == []


def test_pmoat_002_per_resource_failure_is_isolated_and_sanitized(tmp_path: Path) -> None:
    inventory = _inventory_module()
    mutations = _Mutations()
    broken = _Container(
        "broken",
        _container_labels("ws-broken"),
        mutations,
        reload_error=DockerException("token=super-secret at /host/private"),
    )
    healthy = _Container("healthy", _container_labels("ws-healthy"), mutations)

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_Docker(containers=[broken, healthy]),
        state_db_path=tmp_path / "state.db",
    ).collect()

    assert any(item.resource_id == "healthy" for item in result.resources)
    assert any(
        item.resource_id == "broken" and item.lifecycle_state.value == "degraded"
        for item in result.resources
    )
    diagnostic = next(
        item for item in result.diagnostics if item.code == "docker_container_inspection_failed"
    )
    assert "super-secret" not in diagnostic.message
    assert "/host/private" not in diagnostic.message
    assert result.incomplete is True
    assert mutations.calls == []


def test_pmoat_002_durable_repository_mismatch_is_foreign_ownership(tmp_path: Path) -> None:
    inventory = _inventory_module()
    connection = _db()
    _insert_workspace(connection, state="running")
    with connection:
        connection.execute("UPDATE workspace SET container_id = ? WHERE id = ?", ("c1", "ws"))

    mutations = _Mutations()
    container = _Container("c1", _container_labels("ws", repository_id="other"), mutations)
    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_Docker(containers=[container]),
        state_db_path=tmp_path / "state.db",
    ).collect()

    item = next(resource for resource in result.resources if resource.resource_id == "c1")
    assert item.lifecycle_state.value == "foreign"
    assert item.ownership.status is OwnershipStatus.FOREIGN
    assert mutations.calls == []


def test_pmoat_003_job_log_and_cleanup_bytes_are_accounted(tmp_path: Path) -> None:
    inventory = _inventory_module()
    connection = _db()
    _insert_workspace(connection, state="running")
    with connection:
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
            INSERT INTO job_log_cleanup (job_id, workspace_id, created_at, bytes_hint)
            VALUES (?, ?, ?, 41)
            """,
            ("job-debt", "ws", "2026-09-02T00:00:00Z"),
        )

    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_Docker(),
        state_db_path=tmp_path / "state.db",
    ).collect()

    job_log = next(item for item in result.resources if item.resource_id == "job-live")
    debt = next(item for item in result.resources if item.resource_id == "job-debt")
    assert job_log.kind is ResourceKind.JOB_LOG_DIRECTORY
    assert (job_log.bytes.value, job_log.bytes.provenance.value) == (30, "accounted")
    assert debt.kind is ResourceKind.JOB_LOG_CLEANUP
    assert (debt.bytes.value, debt.bytes.provenance.value) == (41, "accounted")


def test_pmoat_003_invalid_negative_accounting_is_unavailable_not_zero(tmp_path: Path) -> None:
    inventory = _inventory_module()
    connection = _db()
    _insert_workspace(connection, state="running")
    with connection:
        connection.execute(
            """
            INSERT INTO job
                (id, workspace_id, command, cwd, state, stdout_total_bytes, stderr_total_bytes)
            VALUES (?, ?, 'bad counters', '/workspace', 'failed', -2, 1)
            """,
            ("job-negative", "ws"),
        )

    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_Docker(),
        state_db_path=tmp_path / "state.db",
    ).collect()

    job_log = next(item for item in result.resources if item.resource_id == "job-negative")
    assert job_log.bytes.value is None
    assert job_log.bytes.provenance.value == "unavailable"
    assert job_log.bytes.reason == "negative accounted byte count is invalid"


def test_pmoat_003_unavailable_metrics_never_become_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _inventory_module()

    def unavailable(_path: Path | str) -> object:
        raise OSError(13, "permission denied at /secret/state")

    monkeypatch.setattr(inventory.os, "statvfs", unavailable)
    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_Docker(),
        state_db_path=tmp_path / "missing" / "state.db",
    ).collect()

    state = next(item for item in result.resources if item.kind is ResourceKind.STATE_STORAGE)
    observations = [state.bytes, *state.metrics.values()]
    missing = [item for item in observations if item.provenance.value == "unavailable"]
    assert missing
    assert all(item.value is None for item in missing)
    assert all(item.value != 0 for item in missing)
    assert result.incomplete is True
    assert all("/secret/state" not in item.message for item in result.diagnostics)


def test_pmoat_002_inventory_includes_cache_ssh_and_state(tmp_path: Path) -> None:
    inventory = _inventory_module()
    connection = _db()
    _insert_workspace(connection, state="stopped")
    with connection:
        connection.execute(
            """
            INSERT INTO ssh_agent (workspace_id, pid, socket_path, created_at)
            VALUES (?, 1234, '/tmp/agent.sock', ?)
            """,
            ("ws", "2026-09-02T00:00:00Z"),
        )

    mutations = _Mutations()
    volume = _Volume("cache-owned", _cache_labels(), mutations)
    result = inventory.ResourceInventoryService(
        connection=connection,
        docker_client=_Docker(volumes=[volume]),
        state_db_path=tmp_path / "state.db",
    ).collect()

    kinds = {item.kind for item in result.resources}
    assert {ResourceKind.CACHE_VOLUME, ResourceKind.SSH_AGENT, ResourceKind.STATE_STORAGE} <= kinds
    assert mutations.calls == []


def test_pmoat_002_inventory_never_mutates_host_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _inventory_module()
    state_db_path = tmp_path / "state.db"
    state_db_path.write_bytes(b"inventory-state-sentinel")
    before = state_db_path.read_bytes()

    def reject(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only inventory attempted host filesystem mutation")

    for name in (
        "mkdir",
        "rmdir",
        "unlink",
        "touch",
        "write_bytes",
        "write_text",
        "rename",
        "replace",
    ):
        monkeypatch.setattr(inventory.Path, name, reject)
    for name in ("mkdir", "makedirs", "remove", "removedirs", "rmdir", "unlink"):
        monkeypatch.setattr(inventory.os, name, reject)

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=_Docker(),
        state_db_path=state_db_path,
    ).collect()

    state = next(item for item in result.resources if item.kind is ResourceKind.STATE_STORAGE)
    assert state.bytes.value == len(before)
    assert state.bytes.provenance.value == "measured"
    assert state_db_path.read_bytes() == before
