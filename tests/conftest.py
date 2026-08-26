"""Shared fixtures."""

from pathlib import Path
from typing import Any

import pytest

from dev_workspace_mcp.config.models import ServerConfig


@pytest.fixture
def minimal_config_dict(tmp_path: Path) -> dict[str, Any]:
    """The smallest config dict that satisfies every required field,
    rooted under `tmp_path` so filesystem-based invariant checks (§19.1,
    §53) have real, isolated paths to reason about."""
    return {
        "version": 1,
        "docker": {"default_image": "mcp-dev-ubuntu:24.04"},
        "ssh": {
            "keys_dir": str(tmp_path / "keys"),
            "known_hosts": str(tmp_path / "known_hosts"),
        },
        "audit": {"path": str(tmp_path / "audit.jsonl")},
    }


@pytest.fixture
def minimal_config(minimal_config_dict: dict[str, Any]) -> ServerConfig:
    """`minimal_config_dict`, already validated into a `ServerConfig`."""
    return ServerConfig.model_validate(minimal_config_dict)
