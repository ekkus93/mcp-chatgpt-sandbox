"""Operational inventory and resource-ownership primitives."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dev_workspace_mcp.operations import inventory as inventory

_DOCKER_VOLUME_SIZE_UNAVAILABLE = (
    "bounded Docker volume metadata did not provide a size measurement"
)
_ORIGINAL_VOLUME_SIZE = inventory.ResourceInventoryService._volume_size


def _bool_safe_volume_size(attrs: Mapping[str, Any]) -> inventory.ByteObservation:
    """Reject bool values before delegating Docker volume byte parsing."""
    usage = attrs.get("UsageData")
    if isinstance(usage, Mapping) and isinstance(usage.get("Size"), bool):
        return inventory.ByteObservation.unavailable(_DOCKER_VOLUME_SIZE_UNAVAILABLE)
    return _ORIGINAL_VOLUME_SIZE(attrs)


inventory.ResourceInventoryService._volume_size = staticmethod(  # type: ignore[method-assign]
    _bool_safe_volume_size
)
