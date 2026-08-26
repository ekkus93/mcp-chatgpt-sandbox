"""S6: Docker workspace lifecycle, against a real Docker daemon (§8.2, §69).

Every test in this file creates real containers; `conftest.py`'s
`docker_client` fixture force-removes everything carrying the §50
"managed" label on teardown, regardless of test outcome.
"""

import sqlite3

import docker
import pytest

from dev_workspace_mcp.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceState
from dev_workspace_mcp.workspace.naming import (
    LABEL_REPOSITORY_ID,
    LABEL_WORKSPACE_ID,
    container_labels,
    container_name,
    persistent_workspace_id,
)
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def test_ensure_workspace_creates_a_running_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert record.state == WorkspaceState.RUNNING
    assert record.id == persistent_workspace_id(repository.id)
    assert record.container_name == container_name(record.id)
    assert record.container_id is not None

    container = docker_client.containers.get(record.container_id)
    assert container.status == "running"
    assert container.labels[LABEL_WORKSPACE_ID] == record.id
    assert container.labels[LABEL_REPOSITORY_ID] == repository.id


def test_ensure_workspace_reuses_the_persistent_workspace(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    first = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    second = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert first.id == second.id
    assert first.container_id == second.container_id


def test_ensure_workspace_ephemeral_mode_creates_distinct_workspaces(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    first = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, mode="ephemeral", run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    second = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, mode="ephemeral", run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert first.id != second.id
    assert first.container_id != second.container_id


def test_stop_stops_the_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    stopped = manager.stop(record.id)
    assert stopped.state == WorkspaceState.STOPPED
    container = docker_client.containers.get(record.container_id)  # type: ignore[arg-type]
    container.reload()
    assert container.status != "running"


def test_ensure_workspace_restarts_a_stopped_persistent_workspace(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    manager.stop(record.id)

    reopened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert reopened.id == record.id
    assert reopened.container_id == record.container_id  # same container, restarted
    assert reopened.state == WorkspaceState.RUNNING


def test_restart_keeps_the_same_container(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    restarted = manager.restart(record.id)
    assert restarted.container_id == record.container_id
    assert restarted.state == WorkspaceState.RUNNING


def test_delete_removes_container_and_db_row(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    container_id = record.container_id
    manager.delete(record.id)

    assert db.execute("SELECT * FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(container_id)  # type: ignore[arg-type]


def test_delete_does_not_touch_the_host_repository(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    """§8.4: SHALL NOT delete the host repository."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    manager.delete(record.id)
    assert repository.canonical_path.exists()


def test_rebuild_replaces_the_container_but_keeps_the_workspace_id(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    original = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    rebuilt = manager.rebuild(original.id, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS)

    assert rebuilt.id == original.id
    assert rebuilt.container_id != original.container_id
    assert rebuilt.state == WorkspaceState.RUNNING
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(original.container_id)  # type: ignore[arg-type]


def test_reset_replaces_the_container_but_keeps_the_workspace_id(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    original = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    reset = manager.reset(original.id, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS)
    assert reset.id == original.id
    assert reset.container_id != original.container_id
    assert reset.state == WorkspaceState.RUNNING


# --- unknown workspace -----------------------------------------------------------


def test_operations_on_an_unknown_workspace_fail_closed(manager: DockerWorkspaceManager) -> None:
    for op in (manager.stop, manager.restart, manager.delete, manager.reconcile):
        with pytest.raises(WorkspaceNotFoundError):
            op("ws_does-not-exist")


# --- creation failure --------------------------------------------------------------


def test_ensure_workspace_reports_a_failed_image_pull_as_container_create_failed(
    manager: DockerWorkspaceManager, repository: RepositoryRecord
) -> None:
    with pytest.raises(ContainerCreateFailedError):
        manager.ensure_workspace(repository=repository, image="this-image-does-not-exist:latest")


# --- reconciliation (§69) -----------------------------------------------------------


def test_reconcile_marks_workspace_absent_when_container_is_gone(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    docker_client.containers.get(record.container_id).remove(force=True)  # type: ignore[arg-type]

    reconciled = manager.reconcile(record.id)
    assert reconciled.state == WorkspaceState.ABSENT
    assert reconciled.container_id is None


def test_reconcile_adopts_an_unrecorded_labeled_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    # Simulate the daemon having lost track of the container_id (e.g. a
    # crash between creating it and persisting the id) while the container
    # itself, correctly labeled, is still there.
    db.execute("UPDATE workspace SET container_id = NULL WHERE id = ?", (record.id,))
    db.commit()

    reconciled = manager.reconcile(record.id)
    assert reconciled.container_id == record.container_id
    assert reconciled.state == WorkspaceState.RUNNING


def test_reconcile_fails_closed_on_multiple_labeled_containers(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    # A second container carrying the same workspace-id label — the
    # scenario §69 says requires an operator, not a guess.
    docker_client.containers.run(
        TEST_IMAGE,
        detach=True,
        labels=container_labels(workspace_id=record.id, repository_id=repository.id),
        **KEEP_ALIVE_RUN_KWARGS,
    )

    with pytest.raises(ContainerRuntimeFailedError) as excinfo:
        manager.reconcile(record.id)
    assert excinfo.value.recoverable is False
