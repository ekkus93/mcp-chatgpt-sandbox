"""S15: `AsyncJobRunner`, direct object-level tests against a real Docker
daemon (§15.3) — HR-021/HR-020 (`docs/HARDENING_SPEC.md` §9) cases that
don't need the full MCP tool layer, mirroring the direct-object pattern
`test_manager.py`/`test_recovery.py` already use for `DockerWorkspaceManager`.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.errors import ContainerRuntimeFailedError
from dev_workspace_mcp.execution.async_runner import AsyncJobRunner
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def _runner(
    docker_client: docker.DockerClient, db: sqlite3.Connection
) -> tuple[AsyncJobRunner, JobStore]:
    job_store = JobStore(db)
    return AsyncJobRunner(ExecutionService(docker_client), job_store), job_store


async def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if await predicate():
            return
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(interval)


async def test_at_hat017_a_failed_exec_start_marks_the_job_terminal(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    """HR-021: a job whose exec start fails used to stay in `starting`
    forever — a state that counts as active and pins the workspace
    against idle stop until the next daemon restart. A real workspace
    exists (for `job.workspace_id`'s foreign key) but the `container_id`
    handed to `start_job` does not, forcing `start_and_open_stream`
    itself to fail exactly the way a container removed out from under a
    queued job would."""
    workspace = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    runner, job_store = _runner(docker_client, db)
    job_store.create(
        job_id="job_1",
        workspace_id=workspace.id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(tmp_path / "out.log"),
        stderr_log_path=str(tmp_path / "err.log"),
    )

    with pytest.raises(ContainerRuntimeFailedError):
        await runner.start_job(
            "job_1",
            container_id="this-container-does-not-exist",
            cmd=["true"],
            cwd="/workspace",
            user="",
            env=None,
            timeout_seconds=None,
            stdout_log_path=tmp_path / "out.log",
            stderr_log_path=tmp_path / "err.log",
            max_output_bytes=1024,
            tail_keep_bytes=1024,
        )

    record = job_store.get("job_1")
    assert record.state == JobState.FAILED  # not stuck in `starting`


async def test_at_hat017_live_job_entry_is_evicted_after_completion(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    """`_live` held every job ever started for the rest of the process's
    lifetime — unbounded growth over a long-running daemon's uptime."""
    workspace = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    runner, job_store = _runner(docker_client, db)
    job_store.create(
        job_id="job_1",
        workspace_id=workspace.id,
        command="true",
        cwd="/",  # a bare `manager.ensure_workspace` container has no /workspace dir
        stdout_log_path=str(tmp_path / "out.log"),
        stderr_log_path=str(tmp_path / "err.log"),
    )

    await runner.start_job(
        "job_1",
        container_id=workspace.container_id,
        cmd=["true"],
        cwd="/",
        user="",
        env=None,
        timeout_seconds=None,
        stdout_log_path=tmp_path / "out.log",
        stderr_log_path=tmp_path / "err.log",
        max_output_bytes=1024,
        tail_keep_bytes=1024,
    )
    assert runner.live_job("job_1") is not None  # tracked while running

    async def evicted() -> bool:
        return runner.live_job("job_1") is None

    await _wait_until(evicted, timeout=10.0)
    assert job_store.get("job_1").state == JobState.SUCCEEDED  # the durable record still exists
