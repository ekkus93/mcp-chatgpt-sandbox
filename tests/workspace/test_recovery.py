"""S18: recovery and reconciliation (§44, §69), against a real Docker
daemon. Every test in this file creates real containers;
`conftest.py`'s `docker_client` fixture force-removes everything
carrying the §50 "managed" label on teardown, regardless of test
outcome.
"""

from pathlib import Path

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceState,
    _state_from_docker_container,
)
from dev_workspace_mcp.workspace.naming import LABEL_WORKSPACE_ID, container_labels
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
