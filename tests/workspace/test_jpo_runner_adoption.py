"""JPO-004 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.2):
`AsyncJobRunner.adopt_running` finishes an exec that is already running
and already being drained, as an ordinary job (I6) — against a real
container, because what is under test is the single-consumer Docker exec
stream being read by exactly one reader from start to finish."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import docker

from mcp_chatgpt_sandbox.execution.async_runner import AsyncJobRunner
from mcp_chatgpt_sandbox.execution.promotable_sink import PromotableSink
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.jobs.store import JobState, JobStore
from mcp_chatgpt_sandbox.repositories import RepositoryRecord
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


async def _adopt(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
    *,
    shell: str,
    timeout_seconds: float | None,
    started_ago_seconds: float = 0.0,
) -> tuple[AsyncJobRunner, JobStore, Path, str]:
    """Does by hand what JPO-006's coordinator will: start a command
    through a `PromotableSink`, let it produce output while still
    "synchronous", then promote and hand it to the runner."""
    workspace = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    service = ExecutionService(docker_client)
    job_store = JobStore(db)
    runner = AsyncJobRunner(service, job_store)
    started_monotonic = time.monotonic() - started_ago_seconds
    started_at = (datetime.now(UTC) - timedelta(seconds=started_ago_seconds)).isoformat()

    exec_id, stream, pid = await asyncio.to_thread(
        service.start_and_open_stream,
        workspace.container_id,
        ["sh", "-c", shell],
        cwd="/",
        # The daemon signals the process from the host: it has to be one this
        # uid may signal, as it is in production where exec runs as the host uid.
        user=f"{os.getuid()}:{os.getgid()}",
        env=None,
    )
    sink = PromotableSink(max_output_bytes=1024 * 1024, tail_keep_bytes=1024)
    drain = asyncio.ensure_future(
        asyncio.to_thread(
            service.drain_stream,
            exec_id,
            stream,
            on_stdout=sink.on_stdout,
            on_stderr=sink.on_stderr,
        )
    )
    await asyncio.sleep(1.0)  # the "synchronous" phase: `before` is produced here
    assert not drain.done()

    out = tmp_path / "job" / "stdout.log"
    job_store.create(
        job_id="job_1",
        workspace_id=workspace.id,
        command=shell,
        cwd="/",
        stdout_log_path=str(out),
        stderr_log_path=str(tmp_path / "job" / "stderr.log"),
    )
    sink.promote(stdout_log_path=out, stderr_log_path=tmp_path / "job" / "stderr.log")
    runner.adopt_running(
        "job_1",
        exec_id=exec_id,
        stream=stream,
        drain=drain,
        stdout_capture=sink.stdout_capture,
        stderr_capture=sink.stderr_capture,
        pid=pid,
        started_at=started_at,
        started_monotonic=started_monotonic,
        timeout_seconds=timeout_seconds,
    )
    return runner, job_store, out, started_at


async def _wait_finished(runner: AsyncJobRunner, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while runner.live_job("job_1") is not None:
        assert time.monotonic() < deadline, "adopted job never finished"
        await asyncio.sleep(0.1)


async def test_jpo004_an_adopted_job_succeeds_with_all_of_its_output(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    runner, job_store, out, started_at = await _adopt(
        manager,
        docker_client,
        db,
        repository,
        tmp_path,
        shell="echo before; sleep 2; echo after",
        timeout_seconds=60,
        started_ago_seconds=5.0,
    )
    adopted = job_store.get("job_1")
    assert adopted.state == JobState.RUNNING
    assert adopted.started_at == started_at  # D4-A: the process's start, not the adoption's
    assert runner.live_job("job_1") is not None

    await _wait_finished(runner)

    finished = job_store.get("job_1")
    assert (finished.state, finished.exit_code) == (JobState.SUCCEEDED, 0)
    assert out.read_bytes() == b"before\nafter\n"
    assert finished.stdout_total_bytes == len(b"before\nafter\n")


async def test_jpo004_an_adopted_job_reports_a_non_zero_exit_as_failed(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    runner, job_store, _out, _ = await _adopt(
        manager,
        docker_client,
        db,
        repository,
        tmp_path,
        shell="sleep 2; exit 7",
        timeout_seconds=60,
    )
    await _wait_finished(runner)

    finished = job_store.get("job_1")
    assert (finished.state, finished.exit_code) == (JobState.FAILED, 7)


async def test_jpo004_the_deadline_counts_from_the_original_start(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    """D4-A. The command would run 300s and the job allowance is 100s, of
    which 99s are reported as already spent before adoption: it must be
    timed out about a second after adoption, not 100s after. The
    `_wait_finished` bound (30s, covering TERM→grace→KILL) is what fails
    if adoption hands out a fresh 100s clock."""
    runner, job_store, _out, _ = await _adopt(
        manager,
        docker_client,
        db,
        repository,
        tmp_path,
        shell="echo before; sleep 300",
        timeout_seconds=100,
        started_ago_seconds=98.0,  # + the 1s synchronous phase inside `_adopt`
    )
    await _wait_finished(runner)

    assert job_store.get("job_1").state == JobState.TIMED_OUT


async def test_jpo004_an_adopted_job_is_cancellable(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
) -> None:
    runner, job_store, out, _ = await _adopt(
        manager,
        docker_client,
        db,
        repository,
        tmp_path,
        shell="echo before; sleep 300",
        timeout_seconds=600,
    )
    await runner.cancel_job("job_1")
    await _wait_finished(runner)

    assert job_store.get("job_1").state == JobState.CANCELED
    assert out.read_bytes() == b"before\n"
