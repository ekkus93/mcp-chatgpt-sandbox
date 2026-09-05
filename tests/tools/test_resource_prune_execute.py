"""Post-MVP Operations O8 / PMOAT-008 public prune-execution acceptance."""

from __future__ import annotations

import json
from typing import Any

from docker.errors import DockerException, NotFound

from dev_workspace_mcp.config.models import ServerConfig
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
        self.removed = False

    def reload(self) -> None:
        if self.removed:
            raise NotFound("container absent")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        if self.removed:
            raise NotFound("container absent")
        self._mutations.calls.append(f"container.remove:{self.id}")
        self.removed = True

    def stop(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("O8 executor must not stop candidates before exact removal")

    def kill(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("O8 executor must not kill candidates before exact removal")


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
        self.removed = False

    def reload(self) -> None:
        if self.removed:
            raise NotFound("volume absent")

    def remove(self, *_args: object, **_kwargs: object) -> None:
        if self.removed:
            raise NotFound("volume absent")
        self._mutations.calls.append(f"volume.remove:{self.name}")
        self.removed = True


class _Collection:
    def __init__(self, items: list[Any], *, identity: str) -> None:
        self._items = items
        self._identity = identity
        self.fail_get_for: set[str] = set()

    @staticmethod
    def _labels(item: Any) -> dict[str, str]:
        direct = getattr(item, "labels", None)
        if isinstance(direct, dict):
            return direct
        attrs = getattr(item, "attrs", {})
        labels = attrs.get("Labels", {}) if isinstance(attrs, dict) else {}
        return labels if isinstance(labels, dict) else {}

    def list(self, *_args: object, **kwargs: object) -> list[Any]:
        result = [item for item in self._items if not item.removed]
        raw_labels = (kwargs.get("filters") or {}).get("label", [])
        labels = [raw_labels] if isinstance(raw_labels, str) else list(raw_labels)
        for requirement in labels:
            key, _, value = str(requirement).partition("=")
            result = [item for item in result if self._labels(item).get(key) == value]
        limit = kwargs.get("limit")
        return result[:limit] if isinstance(limit, int) else result

    def get(self, resource_id: str) -> Any:
        if resource_id in self.fail_get_for:
            raise DockerException("forced exact-identity probe failure")
        for item in self._items:
            if item.removed:
                continue
            if str(getattr(item, self._identity)) == resource_id:
                return item
        raise NotFound("resource absent")

    def prune(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("O8 executor must never call collection.prune")


class _Docker:
    def __init__(
        self,
        *,
        containers: list[_Container],
        volumes: list[_Volume],
    ) -> None:
        self.containers = _Collection(containers, identity="id")
        self.volumes = _Collection(volumes, identity="name")

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


def _docker_fixture(mutations: _Mutations) -> tuple[_Docker, _Container, _Volume]:
    container = _Container(
        "container-o8-owned",
        _container_labels("workspace-o8-owned", "repo-o8-owned"),
        mutations,
    )
    volume = _Volume("cache-o8-owned", _volume_labels("repo-o8-owned"), mutations)
    return _Docker(containers=[container], volumes=[volume]), container, volume


async def _plan(server: Any, *, kinds: list[str] | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"repository_id": "repo-o8-owned"}
    if kinds is not None:
        arguments["resource_kinds"] = kinds
    return (await server.call_tool("resource_prune_plan", arguments)).structured_content


async def test_o8_registers_execute_as_a_separate_explicit_confirmation_surface(
    minimal_config: ServerConfig,
) -> None:
    server = create_server(minimal_config)
    tool = next(tool for tool in await server.list_tools() if tool.name == "resource_prune_execute")

    assert set(tool.input_schema["properties"]) == {"plan_token", "confirm"}
    assert tool.input_schema["properties"]["confirm"].get("default") is False
    assert "plan_token" in tool.input_schema.get("required", [])
    serialized = json.dumps(tool.input_schema, sort_keys=True)
    for forbidden in ("repository_id", "workspace_id", "resource_kinds", "path", "docker_filter"):
        assert forbidden not in serialized


async def test_pmoat_008_omit_or_false_confirmation_is_non_destructive_then_true_executes(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client, container, volume = _docker_fixture(mutations)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    plan = await _plan(server)

    omitted = (
        await server.call_tool("resource_prune_execute", {"plan_token": plan["plan_token"]})
    ).structured_content
    false_set = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": False},
        )
    ).structured_content

    for result in (omitted, false_set):
        assert result["confirmed"] is False
        assert result["executed"] is False
        assert result["candidate_count"] == 2
        assert {item["status"] for item in result["outcomes"]} == {"not_confirmed"}
    assert mutations.calls == []
    assert container.removed is False
    assert volume.removed is False

    executed = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content

    assert executed["confirmed"] is True
    assert executed["executed"] is True
    assert executed["candidate_count"] == 2
    assert executed["reclaimed_count"] == 2
    assert executed["already_absent_count"] == 0
    assert executed["stale_count"] == 0
    assert executed["failed_count"] == 0
    assert {item["status"] for item in executed["outcomes"]} == {"reclaimed"}
    assert set(mutations.calls) == {
        "container.remove:container-o8-owned",
        "volume.remove:cache-o8-owned",
    }
    assert container.removed is True
    assert volume.removed is True


async def test_o8_revalidates_each_candidate_and_does_not_delete_changed_identity(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client, container, volume = _docker_fixture(mutations)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    plan = await _plan(server)

    # The exact planned container still exists, but its material ownership
    # identity changes before execution. The independent volume remains valid.
    container.labels[LABEL_REPOSITORY_ID] = "repo-o8-substituted"

    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content
    by_kind = {item["kind"]: item for item in result["outcomes"]}

    assert by_kind["workspace_container"]["status"] == "stale"
    assert by_kind["cache_volume"]["status"] == "reclaimed"
    assert result["reclaimed_count"] == 1
    assert result["stale_count"] == 1
    assert container.removed is False
    assert volume.removed is True
    assert mutations.calls == ["volume.remove:cache-o8-owned"]


async def test_o8_complete_exact_absence_is_idempotent_success_noop(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client, _container, volume = _docker_fixture(mutations)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    plan = await _plan(server, kinds=["cache_volume"])

    # Simulate another actor removing the exact target after planning.
    volume.removed = True
    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content

    assert result["reclaimed_count"] == 0
    assert result["already_absent_count"] == 1
    assert result["stale_count"] == 0
    assert result["failed_count"] == 0
    assert result["outcomes"][0]["status"] == "already_absent"
    assert mutations.calls == []


async def test_o8_unprovable_exact_identity_is_failure_not_success(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    mutations = _Mutations()
    docker_client, _container, volume = _docker_fixture(mutations)
    monkeypatch.setattr("dev_workspace_mcp.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    plan = await _plan(server, kinds=["cache_volume"])
    docker_client.volumes.fail_get_for.add(volume.name)

    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content

    assert result["reclaimed_count"] == 0
    assert result["already_absent_count"] == 0
    assert result["failed_count"] == 1
    assert result["outcomes"][0]["status"] == "failed"
    assert result["outcomes"][0]["reason"] == "identity_probe_failed"
    assert volume.removed is False
    assert mutations.calls == []
