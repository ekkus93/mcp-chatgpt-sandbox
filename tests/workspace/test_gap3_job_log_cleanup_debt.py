"""GAP3 Y7 / G3AT-007: failed job-log cleanup stays durable and retryable."""

from pathlib import Path

import pytest

from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace import manager_lifecycle
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def test_g3at_007_failed_delete_keeps_durable_job_log_cleanup_debt(
    manager: DockerWorkspaceManager,
    db,
    repository: RepositoryRecord,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting metadata may succeed, but failed log cleanup must remain linked."""
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

    real_rmtree = manager_lifecycle.shutil.rmtree

    def _fail_only_this_job(path: Path) -> None:
        if Path(path) == job_dir:
            raise PermissionError("synthetic job-log cleanup failure")
        real_rmtree(path)

    monkeypatch.setattr(manager_lifecycle.shutil, "rmtree", _fail_only_this_job)

    manager.delete(record.id, state_dir=tmp_path)  # workspace deletion itself SHALL succeed

    assert db.execute("SELECT 1 FROM workspace WHERE id = ?", (record.id,)).fetchone() is None
    assert db.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is None
    assert job_dir.exists()

    # Red proof for G3R-007: pre-Y7, the job row was the only durable
    # linkage. Once it is gone, the failed directory is warning-only and
    # retention can never discover it again. Y7 requires a tombstone/debt
    # row that survives the workspace/job-row deletion transaction.
    table = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'job_log_cleanup'"
    ).fetchone()
    assert table is not None
    debt = db.execute(
        "SELECT job_id, workspace_id FROM job_log_cleanup WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert debt is not None
    assert debt["workspace_id"] == record.id
