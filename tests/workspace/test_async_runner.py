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

#: RR-022's own empirical setup (`docs/REMEDIATION_TODO.md`'s R16
#: write-up) found `Pid: 0` on a single, unretried `exec_inspect` in 6 of
#: 30 (20%) trials. Repeating the same number of trials here means this
#: test fails with overwhelming probability on the unfixed code
#: (1 - 0.8**30 ≈ 99%) rather than merely "sometimes" — a real,
#: non-deterministic race can't be pinned to a single deterministic
#: repro the way a byte-boundary or line-count defect can.
_PID_RACE_TRIALS = 30


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


async def test_at_h031_an_unexpected_drain_failure_is_logged_not_silent(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H31 (`docs/HARDENING_TODO.md`, HR-031): `_run`'s
    `except Exception: exit_code = None` used to swallow *any*
    unexpected drain-path failure with zero record of what happened —
    the exact gap HR-031 names. Forces `drain_stream` itself to raise
    (standing in for the "unforeseen `asyncio.wait` failure" the
    original comment describes, not the already-handled timeout/cancel
    paths) and confirms it reaches the log, not just the job's own
    `FAILED` state."""
    workspace = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    runner, job_store = _runner(docker_client, db)
    job_store.create(
        job_id="job_1",
        workspace_id=workspace.id,
        command="true",
        cwd="/",
        stdout_log_path=str(tmp_path / "out.log"),
        stderr_log_path=str(tmp_path / "err.log"),
    )

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated unexpected drain failure")

    runner._execution.drain_stream = _boom  # type: ignore[method-assign]

    with caplog.at_level("ERROR", logger="dev_workspace_mcp.execution.async_runner"):
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

        async def finished() -> bool:
            return job_store.get("job_1").state in (JobState.FAILED, JobState.SUCCEEDED)

        await _wait_until(finished, timeout=10.0)

    assert job_store.get("job_1").state == JobState.FAILED
    assert job_store.get("job_1").exit_code is None
    matching = [r for r in caplog.records if "job_1" in r.message and "drain failed" in r.message]
    assert matching, f"no matching log record; got: {[r.message for r in caplog.records]}"
    assert matching[0].levelname == "ERROR"
    assert matching[0].exc_info is not None  # the traceback was captured, not discarded


async def test_rr022_pid_of_reliably_reports_a_pid_immediately_after_exec_start(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
) -> None:
    """RR-022 (`docs/REMEDIATION_SPEC.md` §15): reproduces the exact race
    the fix targets — a fresh `exec_start` whose `exec_inspect` does not
    yet report a pid. See `_PID_RACE_TRIALS`'s comment for why 30 trials,
    all required to succeed, is the right bar for a non-deterministic
    race rather than a single deterministic reproduction."""
    workspace = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    execution = ExecutionService(docker_client)
    for _ in range(_PID_RACE_TRIALS):
        exec_id, stream, pid = await asyncio.to_thread(
            execution.start_and_open_stream,
            workspace.container_id,
            ["true"],
            cwd="/",
            user="",
            env=None,
        )
        assert pid is not None
        for _out, _err in stream:  # drain; `true` exits immediately
            pass
