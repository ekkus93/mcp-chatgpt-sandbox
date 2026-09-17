"""O3 public-boundary hardening acceptance for ``resource_inventory``."""

from __future__ import annotations

from typing import Any

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.operations.inventory import (
    ByteObservation,
    InventoryDiagnostic,
    InventoryResource,
    ResourceInventoryResult,
)
from mcp_chatgpt_sandbox.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools import operations as operations_tools
from mcp_chatgpt_sandbox.workspace.naming import LABEL_REPOSITORY_ID, LABEL_WORKSPACE_ID


def _result(
    *,
    resources: tuple[InventoryResource, ...],
    diagnostics: tuple[InventoryDiagnostic, ...] = (),
    incomplete: bool = False,
) -> ResourceInventoryResult:
    return ResourceInventoryResult(
        resources=resources,
        diagnostics=diagnostics,
        inspected_count=len(resources),
        result_limit=256,
        inspection_limit=256,
        truncated=False,
        incomplete=incomplete,
        diagnostics_truncated=False,
    )


def _owned_resource(
    resource_id: str,
    *,
    repository_id: str | None = None,
    workspace_id: str | None = None,
    kind: ResourceKind = ResourceKind.WORKSPACE_CONTAINER,
    bytes_observation: ByteObservation | None = None,
    evidence: tuple[str, ...] = ("durable owned fixture",),
) -> InventoryResource:
    lifecycle_state = (
        ResourceLifecycleState.CLEANUP_PENDING
        if kind in {ResourceKind.JOB_LOG_CLEANUP, ResourceKind.SSH_AGENT_CLEANUP}
        else ResourceLifecycleState.RETAINED
    )
    return InventoryResource(
        kind=kind,
        resource_id=resource_id,
        lifecycle_state=lifecycle_state,
        ownership=OwnershipProof(
            status=OwnershipStatus.OWNED,
            evidence=evidence,
            reason="positive fixture ownership proof",
        ),
        bytes=bytes_observation or ByteObservation.unavailable("not measured"),
        repository_id=repository_id,
        workspace_id=workspace_id,
    )


async def test_o3_scoped_inventory_omits_out_of_scope_resource_diagnostics(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    alpha = _owned_resource("container-alpha", repository_id="alpha", workspace_id="ws_alpha")
    beta = _owned_resource("container-beta", repository_id="beta", workspace_id="ws_beta")
    snapshot = _result(
        resources=(alpha, beta),
        diagnostics=(
            InventoryDiagnostic(
                code="docker_container_inspection_failed",
                message="DockerException",
                resource_kind=ResourceKind.WORKSPACE_CONTAINER,
                resource_id="container-beta",
            ),
        ),
        incomplete=True,
    )
    monkeypatch.setattr(
        operations_tools.ResourceInventoryService, "collect", lambda _self: snapshot
    )

    result = await create_server(minimal_config).call_tool(
        "resource_inventory", {"repository_id": "alpha"}
    )
    payload = result.structured_content

    assert [item["resource_id"] for item in payload["resources"]] == ["container-alpha"]
    assert payload["diagnostics"] == []
    assert payload["status"] == "complete"
    assert payload["incomplete"] is False


async def test_o3_cleanup_debt_never_coerces_unavailable_bytes_to_zero(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    debt = _owned_resource(
        "cleanup-job",
        kind=ResourceKind.JOB_LOG_CLEANUP,
        bytes_observation=ByteObservation.unavailable("cleanup byte hint unavailable"),
    )
    snapshot = _result(resources=(debt,))
    monkeypatch.setattr(
        operations_tools.ResourceInventoryService, "collect", lambda _self: snapshot
    )

    result = await create_server(minimal_config).call_tool("resource_inventory", {})
    payload = result.structured_content

    assert payload["cleanup_debt"] == {
        "status": "partial",
        "count": 1,
        "bytes": {
            "value": None,
            "provenance": "unavailable",
            "reason": "one or more cleanup-debt byte observations are unavailable",
        },
    }


async def test_o3_public_boundary_does_not_echo_path_shaped_docker_identity_evidence(
    minimal_config: ServerConfig,
    monkeypatch: Any,
) -> None:
    resource = _owned_resource(
        "hostile-resource",
        repository_id="/host/private/repository",
        workspace_id="../host-workspace",
        evidence=(
            f"{LABEL_REPOSITORY_ID}=/host/private/repository",
            f"{LABEL_WORKSPACE_ID}=../host-workspace",
        ),
    )
    snapshot = _result(resources=(resource,))
    monkeypatch.setattr(
        operations_tools.ResourceInventoryService, "collect", lambda _self: snapshot
    )

    result = await create_server(minimal_config).call_tool("resource_inventory", {})
    payload = result.structured_content
    item = payload["resources"][0]

    assert item["repository_id"] is None
    assert item["workspace_id"] is None
    assert item["ownership"]["evidence"] == [
        f"{LABEL_REPOSITORY_ID}=<opaque-id>",
        f"{LABEL_WORKSPACE_ID}=<opaque-id>",
    ]
    serialized = str(payload)
    assert "/host/private" not in serialized
    assert "../host-workspace" not in serialized
