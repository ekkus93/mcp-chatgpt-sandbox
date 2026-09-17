"""O8 / PMOAT-009 real-Docker foreign-sentinel prune execution acceptance."""

from __future__ import annotations

import uuid
from typing import Any

import docker
import pytest
from docker.errors import NotFound

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.operations.resources import (
    LABEL_RESOURCE_KIND,
    RESOURCE_KIND_CACHE_VOLUME,
    cache_volume_labels,
)
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
    container_labels,
    current_test_resource_namespace,
)
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def _foreign_container_labels(*, workspace_id: str, repository_id: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_REPOSITORY_ID: repository_id,
        LABEL_TEST_NAMESPACE: "foreign-o8-namespace",
    }


def _foreign_volume_labels(*, repository_id: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
        LABEL_REPOSITORY_ID: repository_id,
        LABEL_TEST_NAMESPACE: "foreign-o8-namespace",
    }


def _assert_absent(getter: Any, identity: str) -> None:
    with pytest.raises(NotFound):
        getter(identity)


async def test_pmoat_009_real_prune_reclaims_only_owned_plan_and_preserves_foreign_sentinels(
    minimal_config: ServerConfig,
    docker_client: docker.DockerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = current_test_resource_namespace()
    assert namespace is not None
    suffix = uuid.uuid4().hex[:12]
    owned_repo = f"o8-owned-{suffix}"
    owned_workspace = f"o8-owned-ws-{suffix}"

    owned_container = docker_client.containers.run(
        TEST_IMAGE,
        ["sh", "-c", "exit 0"],
        detach=True,
        name=f"mcp-dev-o8-owned-{suffix}",
        labels=container_labels(workspace_id=owned_workspace, repository_id=owned_repo),
    )
    wait_result = owned_container.wait(timeout=15)
    assert wait_result["StatusCode"] == 0
    owned_container.reload()
    assert owned_container.status == "exited"

    owned_volume = docker_client.volumes.create(
        name=f"mcp-cache-o8-owned-{suffix}",
        labels=cache_volume_labels(repository_id=owned_repo),
    )

    foreign_container = docker_client.containers.run(
        TEST_IMAGE,
        detach=True,
        name=f"mcp-dev-o8-foreign-{suffix}",
        labels=_foreign_container_labels(
            workspace_id=f"o8-foreign-ws-{suffix}",
            repository_id=f"o8-foreign-{suffix}",
        ),
        **KEEP_ALIVE_RUN_KWARGS,
    )
    foreign_volume = docker_client.volumes.create(
        name=f"mcp-cache-o8-foreign-{suffix}",
        labels=_foreign_volume_labels(repository_id=f"o8-foreign-{suffix}"),
    )
    foreign_container.reload()
    foreign_volume.reload()
    foreign_container_labels_before = dict(foreign_container.labels)
    foreign_volume_attrs_before = dict(foreign_volume.attrs)
    assert foreign_container.status == "running"

    monkeypatch.setattr("mcp_chatgpt_sandbox.server._try_docker_client", lambda: docker_client)
    server = create_server(minimal_config)
    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {
                "repository_id": owned_repo,
                "resource_kinds": ["workspace_container", "cache_volume"],
            },
        )
    ).structured_content
    assert plan["candidate_count"] == 2
    assert {candidate["resource_id"] for candidate in plan["candidates"]} == {
        owned_container.id,
        owned_volume.name,
    }

    result = (
        await server.call_tool(
            "resource_prune_execute",
            {"plan_token": plan["plan_token"], "confirm": True},
        )
    ).structured_content

    assert result["reclaimed_count"] == 2
    assert result["already_absent_count"] == 0
    assert result["stale_count"] == 0
    assert result["failed_count"] == 0
    assert {item["status"] for item in result["outcomes"]} == {"reclaimed"}
    _assert_absent(docker_client.containers.get, owned_container.id)
    _assert_absent(docker_client.volumes.get, owned_volume.name)

    foreign_container_after = docker_client.containers.get(foreign_container.id)
    foreign_volume_after = docker_client.volumes.get(foreign_volume.name)
    foreign_container_after.reload()
    foreign_volume_after.reload()
    assert foreign_container_after.status == "running"
    assert dict(foreign_container_after.labels) == foreign_container_labels_before
    assert foreign_volume_after.name == foreign_volume.name
    assert dict(foreign_volume_after.attrs) == foreign_volume_attrs_before
