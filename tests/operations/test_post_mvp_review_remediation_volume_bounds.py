"""R7 acceptance coverage for bounded Docker-volume inventory work."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from dev_workspace_mcp.db.schema import apply_migrations
from dev_workspace_mcp.operations import inventory
from dev_workspace_mcp.operations.resources import (
    LABEL_RESOURCE_KIND,
    RESOURCE_KIND_CACHE_VOLUME,
    ResourceKind,
)
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED, LABEL_REPOSITORY_ID


class _Volume:
    def __init__(self, name: str) -> None:
        self.name = name
        self.reload_count = 0
        self.attrs: dict[str, Any] = {
            "Name": name,
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
                LABEL_REPOSITORY_ID: "repo",
            },
        }

    def reload(self) -> None:
        self.reload_count += 1


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


class _Docker:
    def __init__(self, volumes: list[_Volume]) -> None:
        self.containers = _Collection([])
        self.volumes = _Collection(volumes)


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    apply_migrations(connection)
    return connection


# Covers: PMORRAT-007
def test_r7_docker_volume_post_list_work_is_bounded_and_limitation_is_documented(
    tmp_path: Path,
) -> None:
    volumes = [_Volume(f"cache-{index}") for index in range(5)]
    docker_client = _Docker(volumes)

    result = inventory.ResourceInventoryService(
        connection=_db(),
        docker_client=docker_client,
        state_db_path=tmp_path / "state.db",
        limits=inventory.InventoryLimits(
            max_resources=2,
            max_inspections=2,
            max_diagnostics=2,
        ),
    ).collect()

    assert docker_client.volumes.list_calls == [{"filters": {"label": f"{LABEL_MANAGED}=true"}}]
    assert "limit" not in docker_client.volumes.list_calls[0]
    assert [volume.reload_count for volume in volumes] == [1, 1, 0, 0, 0]

    cache_resources = [
        resource for resource in result.resources if resource.kind is ResourceKind.CACHE_VOLUME
    ]
    assert [resource.resource_id for resource in cache_resources] == ["cache-0", "cache-1"]
    assert result.inspected_count == 2
    assert result.truncated is True
    assert result.incomplete is True
    assert all(resource.bytes.value is None for resource in cache_resources)
    assert all(resource.bytes.provenance.value == "unavailable" for resource in cache_resources)

    tools_doc = (Path(__file__).resolve().parents[2] / "docs" / "TOOLS.md").read_text(
        encoding="utf-8"
    )
    assert "Docker volume listing has no server-side limit or pagination" in tools_doc, (
        "R7 RED: docs/TOOLS.md must explicitly document the Docker volume-list API "
        "limitation while the implementation bounds post-list work locally"
    )
