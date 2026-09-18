"""PSCR2-009 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`): demonstrate that a
live, production-shaped `serve` and a test context on the same Docker daemon
cannot adopt each other's workspace containers.

Until this file, `CLAUDE.md`, `docs/DEVELOPMENT.md`, and both test skills said
that case was "protected by design, but not demonstrated": the concurrency proof
behind PSCR-010 covered two *test* contexts, never a test context beside a
production-shaped manager. The protection is real — but it is not where it
first appeared to be. `DockerWorkspaceManager.reconcile()` (`workspace/manager.py`)
adopts a container only when PMOR-001's `classify_workspace_container()`
(`operations/resources.py`) proves positive ownership, and that proof's first
check, `_namespace_proof()`, marks any container whose `test-namespace` label
differs from this process's namespace as `FOREIGN`. `_CoreMixin.reconcile()` in
`workspace/manager_core.py` also filters on the namespace, but it is shadowed by
that override and never runs: removing its filter left both tests passing, and
neutering `_namespace_proof()` is what made both fail.

Each test switches the namespace between creating a container and reconciling,
so the same manager plays both roles on a real daemon. Each also runs a control
reconcile under the *original* shape, proving the container was findable and
that the exclusion is caused by the namespace boundary rather than by some
unrelated failure to see it.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import docker
import pytest

from mcp_chatgpt_sandbox.repositories import RepositoryRecord
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager
from mcp_chatgpt_sandbox.workspace.naming import TEST_RESOURCE_NAMESPACE_ENV
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def _forget_container(db: sqlite3.Connection, workspace_id: str) -> None:
    """Simulate a daemon that lost its container id, so reconcile must adopt by label."""

    db.execute("UPDATE workspace SET container_id = NULL WHERE id = ?", (workspace_id,))
    db.commit()


def test_pscr2v_009_production_shaped_reconcile_ignores_a_test_context_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live `serve` (no namespace) must not adopt a running test's container."""

    del docker_client  # establishes this context's namespace and exact-resource teardown
    this_context = os.environ[TEST_RESOURCE_NAMESPACE_ENV]
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert record.container_id is not None
    test_container = record.container_id
    _forget_container(db, record.id)

    # Production shape: `serve` leaves the variable unset.
    monkeypatch.delenv(TEST_RESOURCE_NAMESPACE_ENV)
    assert manager.reconcile(record.id).container_id is None

    # Control: under its own namespace the same container is found and adopted,
    # so the exclusion above is the boundary working, not the container missing.
    monkeypatch.setenv(TEST_RESOURCE_NAMESPACE_ENV, this_context)
    assert manager.reconcile(record.id).container_id == test_container


def test_pscr2v_009_test_context_reconcile_ignores_a_production_shaped_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test context must not adopt a live `serve`'s container.

    The production-shaped container here carries no namespace, so its name is the
    deterministic production name. A per-run repository id keeps that name unique:
    with the shared `example` id, two suites running this test at once would collide
    on it, and this test would reintroduce exactly the concurrency hazard PSCR-010
    removed from the rest of the suite.
    """

    del docker_client  # establishes exact-resource teardown for anything created here
    this_context = os.environ[TEST_RESOURCE_NAMESPACE_ENV]
    production_repo = RepositoryRecord(
        id=f"pscr2-{uuid.uuid4().hex[:12]}", canonical_path=tmp_path, github="owner/example"
    )

    monkeypatch.delenv(TEST_RESOURCE_NAMESPACE_ENV)
    record = manager.ensure_workspace(
        repository=production_repo, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert record.container_id is not None
    production_container = record.container_id
    _forget_container(db, record.id)

    # A test context, namespaced as every Docker-using pytest context is.
    monkeypatch.setenv(TEST_RESOURCE_NAMESPACE_ENV, this_context)
    assert manager.reconcile(record.id).container_id is None

    # Control: in production shape the same container is found and adopted.
    monkeypatch.delenv(TEST_RESOURCE_NAMESPACE_ENV)
    assert manager.reconcile(record.id).container_id == production_container
