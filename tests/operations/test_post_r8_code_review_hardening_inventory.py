"""Post-R8 code-review hardening coverage for inventory type hygiene."""

from __future__ import annotations

import pytest

from mcp_chatgpt_sandbox.operations import inventory


@pytest.mark.parametrize("size", [True, False])
def test_crhv_001_boolean_docker_volume_sizes_are_unavailable(size: bool) -> None:
    """CRHV-001: bool is not a valid Docker byte measurement."""

    observation = inventory.ResourceInventoryService._volume_size({"UsageData": {"Size": size}})

    assert observation.value is None
    assert observation.provenance is inventory.ByteProvenance.UNAVAILABLE
    assert observation.reason == (
        "bounded Docker volume metadata did not provide a size measurement"
    )


@pytest.mark.parametrize("size", [-1, "1024", 1024.0, None])
def test_crhv_001_malformed_docker_volume_sizes_are_unavailable(
    size: object,
) -> None:
    """CRHV-001: malformed Docker volume sizes must not become byte counts."""

    observation = inventory.ResourceInventoryService._volume_size({"UsageData": {"Size": size}})

    assert observation.value is None
    assert observation.provenance is inventory.ByteProvenance.UNAVAILABLE


@pytest.mark.parametrize("attrs", [{}, {"UsageData": {}}, {"UsageData": None}])
def test_crhv_001_missing_docker_volume_sizes_are_unavailable(
    attrs: dict[str, object],
) -> None:
    """CRHV-001: missing Docker volume size metadata remains unavailable."""

    observation = inventory.ResourceInventoryService._volume_size(attrs)

    assert observation.value is None
    assert observation.provenance is inventory.ByteProvenance.UNAVAILABLE


@pytest.mark.parametrize("size", [0, 1, 4096])
def test_crhv_001_non_negative_integer_docker_volume_sizes_are_measured(
    size: int,
) -> None:
    """CRHV-001: valid non-negative integer Docker byte counts still work."""

    observation = inventory.ResourceInventoryService._volume_size({"UsageData": {"Size": size}})

    assert observation.value == size
    assert observation.provenance is inventory.ByteProvenance.MEASURED
    assert observation.reason is None
