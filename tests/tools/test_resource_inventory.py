"""Post-MVP Operations O3: public ``resource_inventory`` MCP acceptance."""

from __future__ import annotations

import errno
import sqlite3
from pathlib import Path
from typing import Any

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.operations.resources import LABEL_RESOURCE_KIND, RESOURCE_KIND_CACHE_VOLUME
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
)


class _Mutations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reject(self, name: str) -> None:
        self.calls.append(name)
        raise AssertionError(f"resource_inventory attempted mutation: {name}")


class _Container:
    def __init__(
        self,
        container_id: str,
        labels: dict[str, str],
        mutations: _Mutations,
        *,
        status: str = "exited",
    ) -> None:
        self.id = container_id
        self.labels = labels
        self.status = status
        self._mutations = mutations

    def reload(self) -> None:
        return None

    def stop(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.stop")

    def kill(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.kill")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("container.remove")


class _Volume:
    def __init__(self, name: str, labels: dict[str, str], mutations: _Mutations) -> None:
        self.name = name
        self.attrs: dict[str, Any] = {
            "Name": name,
            "Labels": labels,
            "UsageData": {"Size": 4096},
        }
        self._mutations = mutations

    def reload(self) -> None:
        return None

    def remove(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("volume.remove")


class _Collection:
    def __init__(self, items: list[Any], mutations: _Mutations) -> None:
        self._items = items
        self._mutations = mutations

    @staticmethod
    def _labels(item: Any) -> dict[str, str]:
        direct = getattr(item, "labels", None)
        if isinstance(direct, dict):
            return direct
        attrs = getattr(item, "attrs", {})
        labels = attrs.get("Labels", {}) if isinstance(attrs, dict) else {}
        return labels if isinstance(labels, dict) else {}

    def list(self, *_args: object, **kwargs: object) -> list[Any]:
        result = list(self._items)
        raw_labels = (kwargs.get("filters") or {}).get("label", [])
        labels = [raw_labels] if isinstance(raw_labels, str) else list(raw_labels)
        for requirement in labels:
            key, _, value = str(requirement).partition("=")
            result = [item for item in result if self._labels(item).get(key) == value]
        limit = kwargs.get("limit")
        return result[:limit] if isinstance(limit, int) else result

    def prune(self, *_args: object, **_kwargs: object) -> None:
        self._mutations.reject("collection.prune")


class _Docker:
    def __init__(
        self,
        mutations: _Mutations,
        *,
        containers: list[_Container] | None = None,
        volumes: list[_Volume] | None = None,
    ) -> None:
        self.containers = _Collection(list(containers or []), mutations)
        self.volumes = _Collection(list(volumes or []), mutations)

    def ping(self) -> bool:
        return True

    def version(self) -> dict[str, str]:
        return {"Version": "test"}


def _container_labels(workspace_id: str, repository_id: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_REPOSITORY_ID: repository_id,
    }


def _volume_labels(repository_id: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
        LABEL_REPOSITORY_ID: repository_id,
    }


def _seed_state(path: Path) -> sqlite3.Connection:
    db = open_db(path)
    with db:
        for repository_id in ("alpha", "beta"):
            db.execute(
                "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
                (repository_id, f"/not-exposed/{repository_id}"),
            )
            workspace_id = f"ws_{repository_id}"
            db.execute(
                """
                INSERT INTO workspace
                    (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
                VALUES (?, ?, 'persistent', '/workspace', 'stopped', ?, ?)
                """,
                (
                    workspace_id,
                    repository_id,
                    "2026-09-02T00:00:00Z",
                    "2026-09-02T00:00:00Z",
                ),
            )
        db.execute(
            """
            INSERT INTO job
                (id, workspace_id, command, cwd, state, stdout_total_bytes, stderr_total_bytes)
            VALUES ('job_alpha', 'ws_alpha', 'true', '/workspace', 'succeeded', 10, 20)
            """
        )
        db.execute(
            """
            INSERT INTO job_log_cleanup
                (job_id, workspace_id, created_at, last_error, bytes_hint)
            VALUES ('cleanup_alpha', 'ws_alpha', ?, 'secret=/host/private', 30)
            """,
            ("2026-09-02T00:00:00Z",),
        )
    return db


async def test_o3_registers_bounded_path_free_resource_inventory_schema(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tool = next(tool for tool in await server.list_tools() if tool.name == "resource_inventory")

    assert "path" not in tool.input_schema["properties"]
    assert set(tool.input_schema["properties"]) == {
        "repository_id",
        "workspace_id",
        "max_resources",
        "max_inspections",
        "max_diagnostics",
    }
    assert tool.input_schema["properties"]["max_resources"]["maximum"] == 256
    assert tool.input_schema["properties"]["max_inspections"]["maximum"] == 256
    assert tool.input_schema["properties"]["max_diagnostics"]["maximum"] == 64
    serialized_schema = str(tool.output_schema)
    assert "canonical_path" not in serialized_schema
    assert "state_db_path" not in serialized_schema


async def test_pmoat_001_to_003_inventory_through_real_mcp_surface_is_scoped_and_read_only(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client = _Docker(
        mutations,
        containers=[
            _Container("container-alpha", _container_labels("ws_alpha", "alpha"), mutations),
            _Container("container-beta", _container_labels("ws_beta", "beta"), mutations),
            _Container(
                "foreign-alpha",
                {
                    **_container_labels("ws_alpha", "alpha"),
                    LABEL_TEST_NAMESPACE: "foreign-namespace",
                },
                mutations,
            ),
        ],
        volumes=[
            _Volume("cache-alpha", _volume_labels("alpha"), mutations),
            _Volume("cache-beta", _volume_labels("beta"), mutations),
        ],
    )
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    state_db = _seed_state(minimal_config.state_db_path)
    state_db.close()

    result = await server.call_tool("resource_inventory", {"repository_id": "alpha"})
    payload = result.structured_content

    assert payload["scope"] == {"repository_id": "alpha", "workspace_id": None}
    assert {item["repository_id"] for item in payload["resources"]} == {"alpha"}
    assert {item["kind"] for item in payload["resources"]} >= {
        "workspace_container",
        "cache_volume",
        "job_log_directory",
        "job_log_cleanup",
    }
    foreign = next(item for item in payload["resources"] if item["resource_id"] == "foreign-alpha")
    assert foreign["ownership"]["status"] == "foreign"
    assert foreign["lifecycle_state"] == "foreign"
    assert payload["cleanup_debt"] == {
        "status": "complete",
        "count": 1,
        "bytes": {"value": 30, "provenance": "accounted", "reason": None},
    }
    assert mutations.calls == []
    serialized = str(payload)
    assert "/not-exposed/" not in serialized
    assert "/host/private" not in serialized
    assert "secret=" not in serialized


async def test_o3_workspace_scope_does_not_guess_repository_scoped_cache_into_workspace(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client = _Docker(
        mutations,
        containers=[
            _Container("container-alpha", _container_labels("ws_alpha", "alpha"), mutations),
        ],
        volumes=[_Volume("cache-alpha", _volume_labels("alpha"), mutations)],
    )
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    state_db = _seed_state(minimal_config.state_db_path)
    state_db.close()

    result = await server.call_tool("resource_inventory", {"workspace_id": "ws_alpha"})
    payload = result.structured_content

    assert all(item["workspace_id"] == "ws_alpha" for item in payload["resources"])
    assert "cache_volume" not in {item["kind"] for item in payload["resources"]}
    assert mutations.calls == []


async def test_o3_docker_unavailable_is_explicitly_degraded_not_fabricated_zero(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    def unavailable_statvfs(_path: object) -> object:
        raise OSError(errno.EIO, "host detail must not leak")

    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: None)
    monkeypatch.setattr("dev_workspace_mcp.operations.inventory.os.statvfs", unavailable_statvfs)
    server = create_server(minimal_config)

    result = await server.call_tool("resource_inventory", {})
    payload = result.structured_content

    assert payload["status"] == "degraded"
    assert payload["incomplete"] is True
    docker_diagnostics = {
        item["code"]: item["message"]
        for item in payload["diagnostics"]
        if item["code"].startswith("docker_")
    }
    assert docker_diagnostics == {
        "docker_container_list_failed": "DockerException",
        "docker_volume_list_failed": "DockerException",
    }
    state = next(item for item in payload["resources"] if item["kind"] == "state_storage")
    for key in ("filesystem_capacity_bytes", "filesystem_free_bytes"):
        metric = state["metrics"][key]
        assert metric["provenance"] == "unavailable"
        assert metric["value"] is None
        assert "host detail" not in metric["reason"]
