"""S18: recovery and reconciliation (§44, §69), against a real Docker
daemon. Every test in this file creates real containers; `conftest.py`
tracks the exact container/volume resources created by the pytest process
and removes only those exact resources on teardown.
"""

import sqlite3
from pathlib import Path

import docker
import pytest

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    _state_from_docker_container,
)
from dev_workspace_mcp.workspace.naming import (
    LABEL_WORKSPACE_ID,
    container_labels,
    container_name,
    persistent_workspace_id,
)
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE

# --- _state_from_docker_container (§44.2): pure classification logic --------------
#
# Tested against fake container objects, not real Docker: the interesting
# cases (OOM kill, a Docker-level runtime error) are the API's own crash
# signals, not something worth reproducing for real (an OOM kill in
# particular is slow and flaky across kernels/cgroup configs). `stop`/
# `reconcile` against real containers are covered further down.


class _FakeContainer:
    def __init__(self, status: str, attrs: dict[str, object] | None = None) -> None:
        self.status = status
        self.attrs = attrs or {}


def test_running_container_is_running() -> None:
    assert _state_from_docker_container(_FakeContainer("running")) == WorkspaceState.RUNNING


def test_dead_container_is_failed() -> None:
    assert _state_from_docker_container(_FakeContainer("dead")) == WorkspaceState.FAILED


def test_oom_killed_container_is_failed() -> None:
    container = _FakeContainer("exited", {"State": {"OOMKilled": True, "ExitCode": 137}})
    assert _state_from_docker_container(container) == WorkspaceState.FAILED


def test_docker_runtime_error_is_failed() -> None:
    container = _FakeContainer("exited", {"State": {"Error": "OCI runtime error", "ExitCode": 1}})
    assert _state_from_docker_container(container) == WorkspaceState.FAILED


def test_exited_with_nonzero_code_but_no_oom_or_error_is_stopped() -> None:
    """A plain nonzero exit code alone is not a crash signal: an
    intentional `stop()` sends SIGTERM, and a process with no handler
    for it (e.g. `sleep`, and plenty of real long-running processes)
    legitimately exits 128+15=143 — a container that was deliberately
    stopped, not one that crashed."""
    container = _FakeContainer("exited", {"State": {"ExitCode": 143}})
    assert _state_from_docker_container(container) == WorkspaceState.STOPPED


def test_exited_cleanly_is_stopped() -> None:
    container = _FakeContainer("exited", {"State": {"ExitCode": 0}})
    assert _state_from_docker_container(container) == WorkspaceState.STOPPED


def test_created_but_never_started_is_stopped() -> None:
    container = _FakeContainer("created", {"State": {}})
    assert _state_from_docker_container(container) == WorkspaceState.STOPPED


# --- reconcile_all (§44.1 steps 1-3) -----------------------------------------------


def test_reconcile_all_reconciles_every_persisted_workspace(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_a = RepositoryRecord(id="a", canonical_path=tmp_path / "a", github="owner/a")
    repo_b = RepositoryRecord(id="b", canonical_path=tmp_path / "b", github="owner/b")
    ws_a = manager.ensure_workspace(
        repository=repo_a, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    ws_b = manager.ensure_workspace(
        repository=repo_b, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    # Stop b's container out of band — simulating "the daemon restarted
    # and Docker's own state moved on without it", not via manager.stop().
    docker_client.containers.get(ws_b.container_id).stop()  # type: ignore[arg-type]

    reconciled = manager.reconcile_all()

    assert {r.id for r in reconciled} == {ws_a.id, ws_b.id}
    assert manager.get(ws_a.id).state == WorkspaceState.RUNNING
    assert manager.get(ws_b.id).state == WorkspaceState.STOPPED


def test_reconcile_all_skips_a_workspace_with_duplicate_containers(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_a = RepositoryRecord(id="a", canonical_path=tmp_path / "a", github="owner/a")
    repo_b = RepositoryRecord(id="b", canonical_path=tmp_path / "b", github="owner/b")
    ws_a = manager.ensure_workspace(
        repository=repo_a, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    ws_b = manager.ensure_workspace(
        repository=repo_b, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    # A second container claiming ws_b's full ownership labels — §69's
    # fail-closed duplicate-owned-container case.
    docker_client.containers.run(
        TEST_IMAGE,
        labels=container_labels(workspace_id=ws_b.id, repository_id=repo_b.id),
        detach=True,
        **KEEP_ALIVE_RUN_KWARGS,
    )

    reconciled = manager.reconcile_all()

    # ws_a still reconciles normally; ws_b is skipped, not fatal to the sweep.
    assert {r.id for r in reconciled} == {ws_a.id}
    assert manager.get(ws_a.id).state == WorkspaceState.RUNNING
    # ws_b's own DB row is untouched by the skipped reconcile.
    assert manager.get(ws_b.id).state == WorkspaceState.RUNNING


def test_reconcile_all_is_a_noop_with_no_workspaces(
    manager: DockerWorkspaceManager,
) -> None:
    assert manager.reconcile_all() == []


# --- HR-017/HAT-014 recovery, narrowed by PMOR-001 ownership proof ----------------


def test_at_hat014_container_removed_out_of_band_recovers_on_reopen(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """§69 notices a positively owned recorded container is gone and
    recreates it; PMOR-001 changes only unproven-resource handling."""
    opened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    original_container_id = opened.container_id
    docker_client.containers.get(original_container_id).remove(force=True)  # type: ignore[arg-type]

    reopened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert reopened.id == opened.id
    assert reopened.state == WorkspaceState.RUNNING
    assert reopened.container_id != original_container_id
    assert docker_client.containers.get(reopened.container_id).status == "running"  # type: ignore[arg-type]


def test_at_hat014_a_failed_create_recovers_once_the_cause_is_fixed(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """A `containers.run` failure leaves a recoverable workspace row;
    correcting the underlying create cause still permits a later open."""
    with pytest.raises(ContainerCreateFailedError):
        manager.ensure_workspace(
            repository=repository,
            image="this-image-does-not-exist-anywhere:latest",
            run_kwargs=KEEP_ALIVE_RUN_KWARGS,
        )
    failed = manager.get(persistent_workspace_id(repository.id))
    assert failed.state == WorkspaceState.FAILED
    assert failed.container_id is None

    recovered = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert recovered.state == WorkspaceState.RUNNING
    assert recovered.container_id is not None


def test_pmoat_001_unproven_container_at_deterministic_name_survives(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """PMOR-001 supersedes H15's old name-as-ownership removal fallback.

    A deterministic MCP-looking name is only an address. Without positive
    managed/workspace/repository/namespace labels, the occupant may be foreign,
    so open fails closed and leaves the resource untouched for the operator.
    """
    name = container_name(persistent_workspace_id(repository.id))
    foreign = docker_client.containers.run(
        TEST_IMAGE, name=name, detach=True, **KEEP_ALIVE_RUN_KWARGS
    )

    with pytest.raises(ContainerCreateFailedError, match="ownership"):
        manager.ensure_workspace(
            repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
        )

    foreign.reload()
    assert foreign.status == "running"
    assert docker_client.containers.get(foreign.id).id == foreign.id


def test_at_hat014_a_correctly_labeled_container_at_the_name_is_adopted(
    manager: DockerWorkspaceManager,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """HAT-014 recovery remains valid when the orphan has positive proof.

    Deliberately delete the state row while retaining the correctly labeled
    container. PMOR-001 permits adoption because the managed, workspace,
    repository, and Y15 namespace labels all agree with the requested state.
    """
    opened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    real_container_id = opened.container_id
    with db:
        db.execute("DELETE FROM workspace WHERE id = ?", (opened.id,))

    reopened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert reopened.container_id == real_container_id
    assert reopened.state == WorkspaceState.RUNNING


# --- AT-017: kill the container with changes present; changes survive restart -----


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def test_at_017_changes_survive_a_container_crash_and_restart(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": "echo from-before-crash > /workspace/survivor.txt"},
    )
    assert (repo_path / "survivor.txt").read_text().strip() == "from-before-crash"

    # Kill it (SIGKILL, not a graceful stop) — simulating a real crash,
    # not the exec/stop tools. "Restart" is simulated by a fresh
    # DockerWorkspaceManager over the same on-disk state DB, the way a
    # fresh daemon process restarting would see it.
    (container,) = docker_client.containers.list(
        all=True, filters={"label": f"{LABEL_WORKSPACE_ID}={workspace_id}"}
    )
    container.kill()

    fresh_db = open_db(config.state_db_path)
    fresh_manager = DockerWorkspaceManager(docker_client, fresh_db)
    reconciled = fresh_manager.reconcile_all()
    assert len(reconciled) == 1
    assert reconciled[0].state in (WorkspaceState.STOPPED, WorkspaceState.FAILED)

    # The repository is untouched on the host regardless — it was always
    # a bind mount, never the container's own writable layer.
    assert (repo_path / "survivor.txt").read_text().strip() == "from-before-crash"

    # workspace_open's mechanism (ensure_workspace) recovers it.
    reopened = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert reopened.structured_content["workspace_id"] == workspace_id
    assert reopened.structured_content["container_state"] == "running"

    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["cat", "/workspace/survivor.txt"]}
    )
    assert check.structured_content["stdout_tail"].strip() == "from-before-crash"
