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

import pytest

from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager
from mcp_chatgpt_sandbox.workspace.manager_core import _CoreMixin


def test_pscr2v_011_the_concrete_manager_overrides_the_declared_reconcile() -> None:
    assert "reconcile" in vars(DockerWorkspaceManager)
    assert DockerWorkspaceManager.reconcile is not _CoreMixin.reconcile


def test_pscr2v_011_the_mixin_declaration_holds_no_implementation() -> None:
    with pytest.raises(NotImplementedError, match=r"workspace/manager\.py"):
        _CoreMixin.reconcile(object.__new__(_CoreMixin), "ws_example")
