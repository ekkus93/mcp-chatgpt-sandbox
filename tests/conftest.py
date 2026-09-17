"""Shared fixtures."""

import logging
from pathlib import Path
from typing import Any

import pytest

from mcp_chatgpt_sandbox.config.models import ServerConfig
from tests._skip_policy import pytest_runtest_logreport, pytest_sessionfinish

__all__ = ["pytest_runtest_logreport", "pytest_sessionfinish"]

# HR-029: lets `test_skip_policy.py` use the `pytester` fixture at all —
# pytest only enables that (internal, off-by-default) plugin when a
# top-level conftest opts in.
pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _restore_root_logging_state() -> Any:
    """GR-011 (`docs/GAP_SPEC.md`): `configure_logging`
    (`logging_config.py`) is meant to run once, at daemon startup, and
    calls `logging.basicConfig(level=…, force=True)` on the *root*
    logger — `force=True` is correct there (see that function's own
    docstring). A real caller reachable from *inside this suite* is
    `tests/test_cli.py`'s `test_serve_*` tests, which exercise the CLI's
    `serve` command end to end; without this fixture, whichever
    level/handlers that leaves root in persists for every test that
    happens to run afterward in the same session.

    That makes any `caplog`-based assertion elsewhere silently
    order-dependent, whether it names a specific logger (whose
    *inherited* effective level still bottoms out at root once nothing
    closer sets one explicitly) or relies on the ambient level directly.
    G9 (`docs/GAP_TODO.md`) reproduced this concretely:
    `tests/workspace/test_manager.py`'s H31 assertion named the wrong
    logger entirely (a sibling of the one actually emitting) and should
    have failed *every* run — but passed whenever `tests/test_cli.py`
    happened to collect first, because root's leaked level (`INFO`,
    `minimal_config_dict`'s default) was low enough to let the
    misdirected record through anyway. `pytest`'s own default
    (alphabetical) collection order guarantees `tests/test_cli.py`
    collects before `tests/workspace/`, so this was not occasional
    flakiness — it was a permanent, silent false pass.

    Snapshotting/restoring here, not by changing `configure_logging`
    itself: the defect is a test calling a process-global configuration
    function without cleaning up, not that the function is wrong for its
    actual (once-per-process) caller."""
    root = logging.getLogger()
    level = root.level
    handlers = list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@pytest.fixture
def minimal_config_dict(tmp_path: Path) -> dict[str, Any]:
    """The smallest config dict that satisfies every required field,
    rooted under `tmp_path` so filesystem-based invariant checks (§19.1,
    §53) have real, isolated paths to reason about."""
    return {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
        "docker": {"default_image": "mcp-chatgpt-sandbox-ubuntu:24.04"},
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
