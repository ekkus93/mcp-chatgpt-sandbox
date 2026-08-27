"""Shared fixtures."""

from pathlib import Path
from typing import Any

import pytest

from dev_workspace_mcp.config.models import ServerConfig
from tests._skip_policy import pytest_runtest_logreport, pytest_sessionfinish

__all__ = ["pytest_runtest_logreport", "pytest_sessionfinish"]

# HR-029: lets `test_skip_policy.py` use the `pytester` fixture at all —
# pytest only enables that (internal, off-by-default) plugin when a
# top-level conftest opts in.
pytest_plugins = ["pytester"]


@pytest.fixture
def minimal_config_dict(tmp_path: Path) -> dict[str, Any]:
    """The smallest config dict that satisfies every required field,
    rooted under `tmp_path` so filesystem-based invariant checks (§19.1,
    §53) have real, isolated paths to reason about."""
    return {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
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
