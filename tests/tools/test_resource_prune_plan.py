"""Post-MVP Operations O7: public ``resource_prune_plan`` MCP acceptance."""

from __future__ import annotations

import json
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
    LABEL_WORKSPACE_ID,
)


class _Mutations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reject(self, name: str) -> None:
        self.calls.append(name)
        raise AssertionError(f"resource_prune_plan attempted mutation: {name}")


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
    def __init__(
        self,
        name: str,
        labels: dict[str, str],
        mutations: _Mutations,
        *,
        size: int = 4096,
    ) -> None:
        self.name = name
        self.attrs: dict[str, Any] = {
            "Name": name,
            "Labels": labels,
            "UsageData": {"Size": size},
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
        containers: list[_Container],
        volumes: list[_Volume],
    ) -> None:
        self.containers = _Collection(containers, mutations)
        self.volumes = _Collection(volumes, mutations)

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


def _seed_retained_state(path: Path) -> sqlite3.Connection:
    db = open_db(path)
    with db:
        db.execute(
            "INSERT INTO repository (id, canonical_path) VALUES ('retained-repo', '/host/private/repo')"
        )
        db.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, container_id,
                 created_at, last_used_at)
            VALUES
                ('ws-retained', 'retained-repo', 'persistent', '/workspace', 'stopped',
                 'container-retained', ?, ?)
            """,
            ("2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z"),
        )
        db.execute(
            """
            INSERT INTO job_log_cleanup
                (job_id, workspace_id, created_at, last_error, bytes_hint)
            VALUES ('cleanup-retained', 'ws-retained', ?, 'secret=/host/private/token', 30)
            """,
            ("2026-09-04T00:00:00Z",),
        )
    return db


def _docker_fixture(mutations: _Mutations) -> _Docker:
    return _Docker(
        mutations,
        containers=[
            _Container(
                "container-orphan",
                _container_labels("ws-orphan", "orphan-repo"),
                mutations,
            ),
            _Container(
                "container-retained",
                _container_labels("ws-retained", "retained-repo"),
                mutations,
            ),
        ],
        volumes=[
            _Volume("cache-orphan", _volume_labels("orphan-repo"), mutations, size=4096),
            _Volume("cache-retained", _volume_labels("retained-repo"), mutations, size=8192),
        ],
    )


async def test_o7_registers_narrow_dry_run_only_prune_plan_schema(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tool = next(tool for tool in await server.list_tools() if tool.name == "resource_prune_plan")

    assert set(tool.input_schema["properties"]) == {
        "repository_id",
        "workspace_id",
        "resource_kinds",
    }
    serialized_input = json.dumps(tool.input_schema, sort_keys=True)
    for forbidden in ("path", "execute", "confirm", "image", "docker_filter"):
        assert forbidden not in serialized_input
    assert "workspace_container" in serialized_input
    assert "cache_volume" in serialized_input
    assert "job_log_cleanup" in serialized_input
    assert "ssh_agent" in serialized_input
    assert "job_log_directory" not in serialized_input
    assert "state_storage" not in serialized_input


async def test_pmoat_006_plan_through_real_mcp_surface_is_stable_and_non_destructive(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client = _docker_fixture(mutations)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    db = _seed_retained_state(minimal_config.state_db_path)
    db.close()

    first = (await server.call_tool("resource_prune_plan", {})).structured_content
    second = (await server.call_tool("resource_prune_plan", {})).structured_content

    assert first["dry_run"] is True
    assert first["plan_token"].startswith("pp_")
    assert len(first["plan_token"]) == 67
    assert second["plan_token"] == first["plan_token"]
    assert first["candidate_count"] == 3
    assert {candidate["kind"] for candidate in first["candidates"]} == {
        "workspace_container",
        "cache_volume",
        "job_log_cleanup",
    }
    assert {candidate["resource_id"] for candidate in first["candidates"]} == {
        "container-orphan",
        "cache-orphan",
        "cleanup-retained",
    }
    assert "container-retained" not in str(first)
    assert "cache-retained" not in str(first)
    assert mutations.calls == []
    assert {item.id for item in docker_client.containers._items} == {
        "container-orphan",
        "container-retained",
    }
    assert {item.name for item in docker_client.volumes._items} == {
        "cache-orphan",
        "cache-retained",
    }


async def test_o7_plan_filters_are_narrow_and_never_expand_default_eligibility(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    monkeypatch.setattr(
        "dev_workspace_mcp.server._try_docker_client",
        lambda: _docker_fixture(mutations),
    )
    server = create_server(minimal_config)
    db = _seed_retained_state(minimal_config.state_db_path)
    db.close()

    repository = (
        await server.call_tool("resource_prune_plan", {"repository_id": "orphan-repo"})
    ).structured_content
    assert {candidate["kind"] for candidate in repository["candidates"]} == {
        "workspace_container",
        "cache_volume",
    }

    cache_only = (
        await server.call_tool(
            "resource_prune_plan",
            {"repository_id": "orphan-repo", "resource_kinds": ["cache_volume"]},
        )
    ).structured_content
    assert [candidate["kind"] for candidate in cache_only["candidates"]] == ["cache_volume"]
    assert [candidate["resource_id"] for candidate in cache_only["candidates"]] == ["cache-orphan"]

    workspace = (
        await server.call_tool("resource_prune_plan", {"workspace_id": "ws-orphan"})
    ).structured_content
    assert [candidate["kind"] for candidate in workspace["candidates"]] == ["workspace_container"]
    assert mutations.calls == []


async def test_o7_candidate_projection_preserves_bytes_and_safe_preconditions(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    monkeypatch.setattr(
        "dev_workspace_mcp.server._try_docker_client",
        lambda: _docker_fixture(mutations),
    )
    server = create_server(minimal_config)
    db = _seed_retained_state(minimal_config.state_db_path)
    db.close()

    payload = (await server.call_tool("resource_prune_plan", {})).structured_content
    by_kind = {candidate["kind"]: candidate for candidate in payload["candidates"]}

    container = by_kind["workspace_container"]
    assert container["bytes"] == {
        "value": None,
        "provenance": "unavailable",
        "reason": "container size not measured by O2",
    }
    assert container["preconditions"] == {
        "docker_status": "exited",
        "ssh_agent_pid": None,
        "ssh_agent_boot_id_bound": False,
        "ssh_agent_process_start_time_bound": False,
    }

    volume = by_kind["cache_volume"]
    assert volume["bytes"] == {"value": 4096, "provenance": "measured", "reason": None}
    cleanup = by_kind["job_log_cleanup"]
    assert cleanup["bytes"] == {"value": 30, "provenance": "accounted", "reason": None}

    assert payload["projected_bytes"] == {
        "known_bytes": 4126,
        "unknown_candidate_count": 1,
        "complete": False,
    }
    for candidate in payload["candidates"]:
        assert candidate["candidate_id"].startswith("pc_")
        assert candidate["reason"]
        assert candidate["ownership"]["status"] == "owned"
        assert candidate["required_lifecycle_state"] in {"orphaned_owned", "cleanup_pending"}
        assert candidate["action"]
    assert mutations.calls == []


async def test_o7_plan_creation_audit_uses_only_opaque_candidate_identity(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    monkeypatch.setattr(
        "dev_workspace_mcp.server._try_docker_client",
        lambda: _docker_fixture(mutations),
    )
    server = create_server(minimal_config)
    db = _seed_retained_state(minimal_config.state_db_path)
    db.close()

    payload = (
        await server.call_tool("resource_prune_plan", {"repository_id": "orphan-repo"})
    ).structured_content
    records = [json.loads(line) for line in minimal_config.audit.path.read_text().splitlines()]
    record = records[-1]

    assert record["operation"] == "resource_prune_plan"
    assert record["result"] == "success"
    assert record["repository_id"] == "orphan-repo"
    assert record["workspace_id"] is None
    assert record["metadata"]["plan_token"] == payload["plan_token"]
    assert record["metadata"]["candidate_count"] == 2
    assert {candidate["candidate_id"] for candidate in record["metadata"]["candidates"]} == {
        candidate["candidate_id"] for candidate in payload["candidates"]
    }
    serialized = json.dumps(record, sort_keys=True)
    assert "container-orphan" not in serialized
    assert "cache-orphan" not in serialized
    assert "/host/private" not in serialized
    assert "secret=" not in serialized
    assert mutations.calls == []
