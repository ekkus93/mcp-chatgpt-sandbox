"""S6: Docker workspace lifecycle, against a real Docker daemon (§8.2, §69).

Every test in this file creates real containers; `conftest.py`'s
`docker_client` fixture force-removes everything carrying the §50
"managed" label on teardown, regardless of test outcome.
"""

import sqlite3
import time
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.errors import (
    ContainerCreateFailedError,
    ContainerRuntimeFailedError,
    WorkspaceNotFoundError,
)
from dev_workspace_mcp.jobs.store import JobStore
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


def test_at_h031_container_lifecycle_transitions_are_logged(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H31 (`docs/HARDENING_TODO.md`, HR-031): container lifecycle
    transitions (create, delete) SHALL be logged — before this task,
    `getLogger` appeared nowhere in this module at all."""
    with caplog.at_level("INFO", logger="dev_workspace_mcp.workspace.manager"):
        record = manager.ensure_workspace(
            repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
        )
        manager.delete(record.id)

    messages = [r.message for r in caplog.records]
    assert any("created" in m and record.id in m for m in messages)
    assert any("deleted" in m and record.id in m for m in messages)


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


def test_rr012_delete_succeeds_and_leaves_no_row_when_volume_removal_fails(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    """RR-012 (`docs/REMEDIATION_SPEC.md` §10): reproduced end to end
    against a real Docker daemon before this fix — a cache volume held
    in use by another (real) container, the same cross-workspace sharing
    HR-009 exists to support, made `delete` raise `ContainerRuntimeFailedError`
    *between* container removal and row deletion: the container was
    confirmed gone, the row was confirmed stuck in `deleting`, and a
    retry hit the identical failure, forever — `delete` never succeeded
    again for that workspace. `delete` SHALL now succeed regardless: the
    workspace (container + rows) is fully gone either way, with the
    volume-removal failure only logged."""
    from docker.types import Mount

    from dev_workspace_mcp.workspace.caches import cache_volume_name

    volume_name = cache_volume_name(repository_id=repository.id, cache_name="cargo")
    mounts = [Mount(target="/root/.cargo", source=volume_name, type="volume", read_only=False)]
    record = manager.ensure_workspace(
        repository=repository,
        image=TEST_IMAGE,
        run_kwargs={**KEEP_ALIVE_RUN_KWARGS, "mounts": mounts},
    )
    container_id = record.container_id

    # A second, real container holds the volume in use.
    holder = docker_client.containers.run(
        TEST_IMAGE,
        "sleep 300",
        detach=True,
        volumes={volume_name: {"bind": "/data", "mode": "rw"}},
    )
    try:
        manager.delete(record.id, cache_volume_names=[volume_name])  # SHALL NOT raise

        assert db.execute("SELECT * FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(container_id)  # type: ignore[arg-type]
        # The volume itself genuinely survives (still in use) — the
        # failure was real, just no longer fatal to the delete call.
        docker_client.volumes.get(volume_name)
    finally:
        holder.remove(force=True)
        docker_client.volumes.get(volume_name).remove(force=True)


def test_rr012_delete_succeeds_when_ssh_agent_termination_fails(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    """The other half of the same fix, forced deterministically: whatever
    the reason (`SSHAgentManager.terminate`'s own docstring only claims
    the *signal itself* is best-effort, not every step around it), a
    real failure there SHALL NOT prevent `delete` from completing."""
    from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager

    class _AlwaysFailsTermination(SSHAgentManager):
        def terminate(self, workspace_id: str) -> None:
            raise RuntimeError("simulated ssh-agent termination failure")

    manager._ssh_agents = _AlwaysFailsTermination(db, runtime_dir=tmp_path)  # type: ignore[attr-defined]

    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    container_id = record.container_id

    manager.delete(record.id)  # SHALL NOT raise

    assert db.execute("SELECT * FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(container_id)  # type: ignore[arg-type]


def test_at_hr009_delete_reclaims_cache_volumes(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """HR-009 (`docs/HARDENING_SPEC.md` §4.2): a cache volume this
    module has no way to discover on its own (nothing links a Docker
    volume back to the `repository_id` that created it) is reclaimed
    when the caller (`cli.py`'s `delete` command) supplies the names it
    computed from the repository's own `.mcp-workspace.yml` — otherwise
    a repository id later reused for a different project would silently
    inherit the old one's cache volumes."""
    from docker.types import Mount

    from dev_workspace_mcp.workspace.caches import cache_volume_name

    volume_name = cache_volume_name(repository_id=repository.id, cache_name="cargo")
    mounts = [Mount(target="/root/.cargo", source=volume_name, type="volume", read_only=False)]
    record = manager.ensure_workspace(
        repository=repository,
        image=TEST_IMAGE,
        run_kwargs={**KEEP_ALIVE_RUN_KWARGS, "mounts": mounts},
    )
    docker_client.volumes.get(volume_name)  # sanity: it really was created

    manager.delete(record.id, cache_volume_names=[volume_name])

    with pytest.raises(docker.errors.NotFound):
        docker_client.volumes.get(volume_name)


def test_at_hr009_delete_tolerates_a_cache_volume_that_was_never_created(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
) -> None:
    """A declared-but-never-actually-mounted cache (or one whose name
    changed since the workspace was last opened) has no volume to
    remove — not an error."""
    from dev_workspace_mcp.workspace.caches import cache_volume_name

    never_created = cache_volume_name(repository_id=repository.id, cache_name="never-used")
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    manager.delete(record.id, cache_volume_names=[never_created])  # SHALL NOT raise


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


def test_rr014_reset_never_adopts_the_container_it_replaces(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    """RR-014 (`docs/REMEDIATION_SPEC.md` §11): H15 scenario #2 — the row
    loses track of `container_id` while a correctly-labeled container
    still occupies the deterministic name — reproduced empirically that
    `_recreate` used to reach this the same way `_create` legitimately
    does, and `_adopt_or_remove_by_name` couldn't tell the two apart:
    `reset` silently returned the *same* stale container instead of
    discarding its environment state, defeating the one thing `reset`
    promises to do."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    old_container_id = record.container_id
    db.execute("UPDATE workspace SET container_id = NULL WHERE id = ?", (record.id,))
    db.commit()

    result = manager.reset(record.id, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS)

    assert result.container_id != old_container_id
    assert result.state == WorkspaceState.RUNNING
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(old_container_id)  # type: ignore[arg-type]


def test_rr014_ensure_workspace_starts_a_stopped_adopted_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    """The other half of RR-014: adoption propagated a non-running state
    straight out of `ensure_workspace` with no `start()` — reproduced
    empirically that the first `workspace_open` after adopting a
    *stopped* orphan reported `stopped`, self-healing only on a second
    call. Reached through the fresh-create path (the row deleted, not
    merely losing its `container_id` — `reconcile()`'s own separate
    adoption logic, exercised by the sibling test above, already handles
    the "row still exists" case correctly; this is `_create`'s adoption
    path, taken when the row doesn't exist at all yet)."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    docker_client.containers.get(record.container_id).stop()  # type: ignore[arg-type]
    db.execute("DELETE FROM workspace WHERE id = ?", (record.id,))
    db.commit()

    result = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )

    assert result.container_id == record.container_id  # genuinely adopted, not recreated
    assert result.state == WorkspaceState.RUNNING


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


def test_at_hat015_delete_succeeds_for_a_workspace_that_ran_a_job(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    """HR-018 (`docs/HARDENING_SPEC.md` §9): `job.workspace_id` has no
    `ON DELETE CASCADE`, so a workspace that ran even one job used to
    make `delete()`'s final `DELETE FROM workspace` raise
    `sqlite3.IntegrityError` — after the container was already removed,
    wedging the row in `DELETING` forever."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    container_id = record.container_id
    JobStore(db).create(
        job_id="job_example",
        workspace_id=record.id,
        command="true",
        cwd="/workspace",
        stdout_log_path="/dev/null",
        stderr_log_path="/dev/null",
    )

    manager.delete(record.id)  # SHALL NOT raise

    assert db.execute("SELECT * FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
    assert db.execute("SELECT * FROM job WHERE workspace_id = ?", (record.id,)).fetchone() is None
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(container_id)  # type: ignore[arg-type]


def test_at_hat015_delete_of_a_workspace_with_multiple_jobs_removes_all_of_them(
    manager: DockerWorkspaceManager,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    store = JobStore(db)
    for i in range(3):
        store.create(
            job_id=f"job_{i}",
            workspace_id=record.id,
            command="true",
            cwd="/workspace",
            stdout_log_path="/dev/null",
            stderr_log_path="/dev/null",
        )

    manager.delete(record.id)

    remaining = db.execute("SELECT COUNT(*) AS n FROM job WHERE workspace_id = ?", (record.id,))
    assert remaining.fetchone()["n"] == 0


# --- H32: OOM reporting surface (`docs/HARDENING_SPEC.md` §9/§26 row) --------------


def test_at_h032_a_real_oom_kill_is_reported_distinctly_not_just_as_failed(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """A real cgroup OOM kill, not a simulated one: a 16 MiB memory
    ceiling and a shell command that holds 128 MiB of genuinely
    non-reclaimable (anonymous, not page-cache) memory in a variable —
    verified empirically (`docker run --memory=16m ...` in isolation,
    outside this test) to reliably trigger the kernel OOM killer within
    a few seconds, with `docker inspect` reporting `OOMKilled: true`."""
    oom_run_kwargs = {
        "command": [
            "sh",
            "-c",
            "a=$(head -c 134217728 /dev/zero | tr '\\0' 'a'); sleep 30",
        ],
        "mem_limit": "16m",
        "memswap_limit": "16m",
    }
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=oom_run_kwargs
    )
    container_id = record.container_id
    assert container_id is not None

    # Poll for the real kernel OOM kill to land, rather than a fixed
    # sleep — Docker's own accounting can take a moment after the kill.
    for _ in range(30):
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            break
        time.sleep(1)
    assert container.attrs["State"]["OOMKilled"] is True  # sanity: the real kill happened

    reconciled = manager.reconcile(record.id)

    assert reconciled.state == WorkspaceState.FAILED
    assert reconciled.oom_killed is True


def test_at_h032_a_workspace_that_exited_cleanly_is_not_reported_as_oom_killed(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """The false-positive guard for the test above: an ordinary,
    non-OOM exit (well within the memory limit) SHALL NOT be reported
    as `oom_killed`, even though it also lands in `state == FAILED`
    via `_state_from_docker_container`'s `state.get("Error")` branch."""
    record = manager.ensure_workspace(
        repository=repository,
        image=TEST_IMAGE,
        run_kwargs={"command": ["sh", "-c", "exit 1"], "mem_limit": "64m"},
    )
    container_id = record.container_id
    assert container_id is not None

    for _ in range(30):
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            break
        time.sleep(1)
    assert container.attrs["State"]["OOMKilled"] is False  # sanity: not an OOM exit

    reconciled = manager.reconcile(record.id)

    assert reconciled.oom_killed is False
