"""Shared fixtures for config tests."""

from pathlib import Path
from typing import Any

import pytest


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
