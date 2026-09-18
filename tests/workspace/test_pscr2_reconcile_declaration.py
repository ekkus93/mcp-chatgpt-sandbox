"""PSCR2-011 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`): pin that
`_CoreMixin` only *declares* `reconcile()`.

`_CoreMixin.reconcile()` used to hold a complete, 77-line §69 implementation
with its own namespace filter. `DockerWorkspaceManager` overrode it and nothing
called `super().reconcile()`, so it never ran — but it read as the live path,
and it misled the PSCR2 review into citing it as the production protection. A
discrimination run against it "passed" because the code under test never
executed.

These tests keep that trap from quietly returning: the concrete class must
provide its own `reconcile()`, and the mixin's declaration must fail loudly
rather than contain logic someone could mistake for the real thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.repositories import RepositoryRecord
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager, WorkspaceState
from mcp_chatgpt_sandbox.workspace.manager_core import _CoreMixin
from mcp_chatgpt_sandbox.workspace.records import WorkspaceRecord


def test_pscr2v_011_the_concrete_manager_overrides_the_declared_reconcile() -> None:
    assert "reconcile" in vars(DockerWorkspaceManager)
    assert DockerWorkspaceManager.reconcile is not _CoreMixin.reconcile


def test_pscr2v_011_the_mixin_declaration_holds_no_implementation() -> None:
    with pytest.raises(NotImplementedError, match=r"workspace/manager\.py"):
        _CoreMixin.reconcile(object.__new__(_CoreMixin), "ws_example")


# --- PSCR3-006: the startup sweep's dispatch ---------------------------------


class _RecordingContainers:
    """A Docker `containers` collection that finds nothing and records each `list()`."""

    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> list[object]:
        self.list_calls.append(kwargs)
        return []


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _RecordingContainers()


def _manager_with_one_workspace(tmp_path: Path) -> tuple[DockerWorkspaceManager, _FakeDocker]:
    client = _FakeDocker()
    manager = DockerWorkspaceManager(client, open_db(":memory:"))  # type: ignore[arg-type]
    manager._upsert_repository(
        RepositoryRecord(id="example", canonical_path=tmp_path, github="owner/example")
    )
    manager._insert(
        WorkspaceRecord(
            id="ws_example",
            repository_id="example",
            mode="persistent",
            container_id=None,
            container_name="unused",
            image="unused:latest",
            workspace_path="/workspace",
            state=WorkspaceState.ABSENT,
            created_at="2026-09-18T00:00:00Z",
            last_used_at="2026-09-18T00:00:00Z",
        )
    )
    return manager, client


def test_pscr3v_006_reconcile_all_dispatches_to_the_concrete_override(tmp_path: Path) -> None:
    """PSCR3-006 (`docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`): the §44.1 startup
    sweep lives on `_CoreMixin`, which only *declares* `reconcile()`. It must reach
    `DockerWorkspaceManager`'s implementation, and before this test only server
    startup would have revealed that it did not.

    The sweep returning the workspace shows the declaration was never reached, since
    that raises. The recorded Docker query shows *which* body ran: only the concrete
    `reconcile()` lists containers by workspace label.
    """

    manager, client = _manager_with_one_workspace(tmp_path)

    reconciled = manager.reconcile_all()

    assert [record.id for record in reconciled] == ["ws_example"]
    (call,) = client.containers.list_calls
    assert call["filters"] == {"label": "mcp-chatgpt-sandbox.workspace-id=ws_example"}


def test_pscr3v_006_a_lost_override_fails_the_sweep_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the override the sweep must raise, not silently skip the workspace.

    `reconcile_all()` catches only `ContainerRuntimeFailedError`, so the
    declaration's `NotImplementedError` propagates. This pins that: were the sweep
    ever to catch more broadly, a lost override would turn every workspace into a
    quietly "skipped" one at startup.
    """

    manager, _client = _manager_with_one_workspace(tmp_path)
    monkeypatch.delattr(DockerWorkspaceManager, "reconcile")

    with pytest.raises(NotImplementedError, match=r"workspace/manager\.py"):
        manager.reconcile_all()
