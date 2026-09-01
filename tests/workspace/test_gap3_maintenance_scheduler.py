"""G3AT-014: maintenance runs from server lifecycle without client traffic."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path

import docker
import pytest

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import ContainerRuntimeFailedError
from dev_workspace_mcp.jobs.retention import get_job_log_cleanup_debt
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceState


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


def _backdate_workspace(db: sqlite3.Connection, workspace_id: str) -> None:
    with db:
        db.execute(
            "UPDATE workspace SET last_used_at = datetime('now', '-10 minutes') || '+00:00' "
            "WHERE id = ?",
            (workspace_id,),
        )


def _configured_server(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "workspace": minimal_config.workspace.model_copy(update={"idle_stop_after_seconds": 1}),
            "execution": minimal_config.execution.model_copy(update={"job_retention_days": 1}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def test_g3at_014_server_lifespan_runs_and_recovers_maintenance_without_client_traffic(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _configured_server(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    # Setup traffic is allowed: G3AT-014's point is that after maintenance
    # becomes due, no unrelated future MCP request is needed to trigger it.
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    _backdate_workspace(db, workspace_id)
    store = JobStore(db)
    jobs_dir = config.state_db_path.parent / "jobs"

    expired_dir = jobs_dir / "job_expired"
    expired_dir.mkdir(parents=True)
    expired_stdout = expired_dir / "stdout.log"
    expired_stderr = expired_dir / "stderr.log"
    expired_stdout.write_text("old stdout\n")
    expired_stderr.write_text("old stderr\n")
    store.create(
        job_id="job_expired",
        workspace_id=workspace_id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(expired_stdout),
        stderr_log_path=str(expired_stderr),
    )
    store.mark_running("job_expired", pid=1)
    store.mark_finished(
        "job_expired",
        state=JobState.SUCCEEDED,
        exit_code=0,
        stdout_total_bytes=expired_stdout.stat().st_size,
        stderr_total_bytes=expired_stderr.stat().st_size,
        output_truncated=False,
    )
    with db:
        db.execute(
            "UPDATE job SET finished_at = datetime('now', '-10 days') || '+00:00' WHERE id = ?",
            ("job_expired",),
        )

    debt_dir = jobs_dir / "job_cleanup_debt"
    debt_dir.mkdir(parents=True)
    debt_stdout = debt_dir / "stdout.log"
    debt_stderr = debt_dir / "stderr.log"
    debt_stdout.write_text("orphan stdout\n")
    debt_stderr.write_text("orphan stderr\n")
    store.create(
        job_id="job_cleanup_debt",
        workspace_id=workspace_id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(debt_stdout),
        stderr_log_path=str(debt_stderr),
    )
    with db:
        db.execute("DELETE FROM job WHERE id = ?", ("job_cleanup_debt",))
    assert get_job_log_cleanup_debt(db, "job_cleanup_debt") is not None

    lifespan = getattr(server.settings, "lifespan", None)
    assert lifespan is not None, "G3AT-014: server has no lifecycle-owned maintenance scheduler"

    async with lifespan(server) as scheduler:
        # `create_task` does not run until this test yields control, so the
        # interval and first-stop failure are installed before cycle one.
        scheduler._interval_seconds = 0.2
        assert scheduler._manager is not None
        real_stop = scheduler._manager.stop
        failed_once = False

        def fail_first_idle_stop(target_workspace_id: str):  # type: ignore[no-untyped-def]
            nonlocal failed_once
            if target_workspace_id == workspace_id and not failed_once:
                failed_once = True
                raise ContainerRuntimeFailedError("G3AT-014 simulated idle-stop failure")
            return real_stop(target_workspace_id)

        monkeypatch.setattr(scheduler._manager, "stop", fail_first_idle_stop)

        # No MCP calls occur after entering the lifespan. The first cycle's
        # idle-stop failure must not prevent retention or Y7 debt retry.
        await _wait_until(
            lambda: (
                not expired_stdout.exists()
                and not expired_stderr.exists()
                and not debt_dir.exists()
                and scheduler.health().status == "degraded"
            )
        )
        assert manager.get(workspace_id).state == WorkspaceState.RUNNING
        assert get_job_log_cleanup_debt(db, "job_cleanup_debt") is None
        assert store.get("job_expired").logs_pruned is True

        first_failure = scheduler.health()
        assert first_failure.running is True
        assert first_failure.last_failure_at is not None
        assert first_failure.last_failure_task == "idle_workspace_sweep"
        assert first_failure.last_failure_item == workspace_id
        assert "ContainerRuntimeFailedError" in (first_failure.last_failure_reason or "")

        # A later cycle must still run, retry the failed workspace item, and
        # recover the current maintenance health to healthy.
        await _wait_until(
            lambda: (
                manager.get(workspace_id).state == WorkspaceState.STOPPED
                and scheduler.health().status == "healthy"
            )
        )
        recovered = scheduler.health()
        assert recovered.running is True
        assert recovered.last_cycle_at is not None
        assert recovered.last_failure_at == first_failure.last_failure_at

    assert scheduler.health().running is False
