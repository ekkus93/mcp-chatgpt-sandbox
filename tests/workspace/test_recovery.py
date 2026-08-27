"""S18: recovery and reconciliation (§44, §69), against a real Docker
daemon. Every test in this file creates real containers;
`conftest.py`'s `docker_client` fixture force-removes everything
carrying the §50 "managed" label on teardown, regardless of test
outcome.
"""

import sqlite3
from pathlib import Path

import pytest

import docker
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
    # A second container claiming ws_b's label — §69's fail-closed case.
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


# --- HR-017/HAT-014: a workspace recovers from any Docker state -------------------


def test_at_hat014_container_removed_out_of_band_recovers_on_reopen(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """§69's `reconcile` correctly notices the container is gone and
    marks the row `ABSENT` — the regression was `ensure_workspace` then
    unconditionally calling `start()`, which only ever starts an
    *existing* container and so failed forever after."""
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
    """A `containers.run` failure (here: an image that doesn't exist,
    standing in for any transient Docker error) leaves the row `FAILED`
    with no container at all — not merely a container that failed to
    *start*. The regression: `ensure_workspace`'s next call still routed
    into `start()`, which has no container to start and fails the exact
    same way forever, even once the underlying cause (a bad image name,
    a transient daemon hiccup) is gone."""
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


def test_at_hat014_an_orphaned_container_at_the_deterministic_name_is_removed(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """HR-017's "adopt-or-remove" — the *remove* half: a container
    already occupying the deterministic name (§51), but carrying no
    workspace_id label at all, can only be a stale leftover from a
    previous incarnation of this same logical workspace (the name is
    deterministic per workspace_id) — not something a fresh
    `workspace_open` should ever have to give up in front of. Before
    this fix, `containers.run`'s own 409 on the name collision wedged
    the workspace permanently, since the same name collides again on
    every subsequent attempt."""
    name = container_name(persistent_workspace_id(repository.id))
    orphan = docker_client.containers.run(
        TEST_IMAGE, name=name, detach=True, **KEEP_ALIVE_RUN_KWARGS
    )

    opened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert opened.state == WorkspaceState.RUNNING
    assert opened.container_id != orphan.id
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(orphan.id)  # the orphan was removed, not merely ignored


def test_at_hat014_a_correctly_labeled_container_at_the_name_is_adopted(
    manager: DockerWorkspaceManager,
    db: sqlite3.Connection,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """HR-017's "adopt-or-remove" — the *adopt* half. Deliberately
    deletes the `workspace` row entirely, not merely `container_id`:
    clearing just the column still leaves a row `reconcile()`'s own
    *existing*, pre-H15 label-based adoption already covers on its own
    (`ensure_workspace` calls `reconcile()` whenever a row exists at
    all) — that path doesn't distinguish this task's fix from its
    absence. A genuinely missing row (its own earlier `containers.run`
    plausibly completed, but the row was lost before ever being written —
    e.g. this process crashed between the call returning and the
    `INSERT`, or the state DB itself was reset) routes through `_create`
    instead, which never calls `reconcile()` at all — `_run_container`'s
    own adopt-or-remove check is the only thing that can save this case,
    since `containers.run` would otherwise 409 against the still-running,
    still-correctly-labeled container."""
    opened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    real_container_id = opened.container_id
    with db:
        db.execute("DELETE FROM workspace WHERE id = ?", (opened.id,))

    reopened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert reopened.container_id == real_container_id  # adopted, not recreated
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
