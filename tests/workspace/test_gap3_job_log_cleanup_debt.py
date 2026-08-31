"""GAP3 Y7 / G3AT-007: failed job-log cleanup stays durable and retryable."""

import sqlite3
from pathlib import Path

import pytest

from dev_workspace_mcp.jobs import retention
from dev_workspace_mcp.jobs.retention import prune_job_logs
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def _create_finished_budget_job(
    db: sqlite3.Connection,
    *,
    state_dir: Path,
    repository_id: str,
) -> JobStore:
    workspace_id = "ws_budget"
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES (?, ?, 'persistent', '/workspace', 'running', ?, ?)
        """,
        (
            workspace_id,
            repository_id,
            "2026-08-31T00:00:00+00:00",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    db.commit()

    job_dir = state_dir / "jobs" / "job_budget"
    job_dir.mkdir(parents=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    stdout_path.write_bytes(b"y" * 100)
    stderr_path.write_bytes(b"")

    store = JobStore(db)
    store.create(
        job_id="job_budget",
        workspace_id=workspace_id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
    )
    store.mark_running("job_budget", pid=1)
    store.mark_finished(
        "job_budget",
        state=JobState.SUCCEEDED,
        exit_code=0,
        stdout_total_bytes=100,
        stderr_total_bytes=0,
        output_truncated=False,
    )
    # Keep age retention out of this assertion; only the total-size budget
    # should choose this job after pending cleanup debt is included.
    db.execute(
        "UPDATE job SET finished_at = ? WHERE id = 'job_budget'",
        ("2100-01-01T00:00:00+00:00",),
    )
    db.commit()
    return store


def test_g3at_007_failed_delete_is_durable_accounted_and_retried(
    manager: DockerWorkspaceManager,
    db: sqlite3.Connection,
    repository: RepositoryRecord,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the full Y7 failure -> debt -> accounting -> recovery loop."""
    record = manager.ensure_workspace(
        repository=repository,
        image=TEST_IMAGE,
        run_kwargs=KEEP_ALIVE_RUN_KWARGS,
    )
    job_id = "job_cleanup_debt"
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    stdout_path.write_bytes(b"x" * 100)
    stderr_path.write_bytes(b"")
    JobStore(db).create(
        job_id=job_id,
        workspace_id=record.id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
    )

    real_rmtree = retention.shutil.rmtree

    def _fail_only_cleanup_debt(path: Path) -> None:
        if Path(path) == job_dir:
            raise PermissionError("synthetic job-log cleanup failure")
        real_rmtree(path)

    monkeypatch.setattr(retention.shutil, "rmtree", _fail_only_cleanup_debt)

    manager.delete(record.id, state_dir=tmp_path)  # primary deletion SHALL succeed

    assert db.execute("SELECT 1 FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
    assert db.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is None
    assert job_dir.exists()

    debt = db.execute(
        """
        SELECT workspace_id, last_attempt_at, last_error, bytes_hint
        FROM job_log_cleanup
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    assert debt is not None
    assert debt["workspace_id"] == record.id
    assert debt["last_attempt_at"] is not None
    assert "PermissionError" in debt["last_error"]
    assert debt["bytes_hint"] == 100

    # While deletion still fails, pending debt must count toward the same
    # max-total-bytes budget as live job rows. 100 bytes of orphan debt +
    # 100 bytes of a still-queryable finished job exceeds a 150-byte cap;
    # the latter therefore has to be pruned. Pre-Y7 accounting saw only the
    # live 100 bytes and incorrectly considered the cap satisfied.
    store = _create_finished_budget_job(
        db,
        state_dir=tmp_path,
        repository_id=repository.id,
    )
    pruned = prune_job_logs(
        store,
        job_retention_days=36500,
        job_log_max_total_bytes=150,
        state_dir=tmp_path,
    )
    assert pruned == ["job_budget"]
    assert store.get("job_budget").logs_pruned is True
    assert job_dir.exists()
    assert db.execute(
        "SELECT 1 FROM job_log_cleanup WHERE job_id = ?", (job_id,)
    ).fetchone() is not None

    # Remove the injected failure and run the same on-demand maintenance
    # path again. The orphan is reclaimed and only then is the tombstone
    # cleared, proving the debt is retryable rather than warning-and-forget.
    monkeypatch.setattr(retention.shutil, "rmtree", real_rmtree)
    prune_job_logs(
        store,
        job_retention_days=36500,
        job_log_max_total_bytes=10**9,
        state_dir=tmp_path,
    )
    assert not job_dir.exists()
    assert db.execute(
        "SELECT 1 FROM job_log_cleanup WHERE job_id = ?", (job_id,)
    ).fetchone() is None
