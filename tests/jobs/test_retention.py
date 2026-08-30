"""S16: job log retention (§15.6)."""

import sqlite3
from pathlib import Path

import pytest

from dev_workspace_mcp.jobs.retention import prune_job_logs
from dev_workspace_mcp.jobs.store import JobState, JobStore
from tests.jobs.conftest import WORKSPACE_ID


def _create_finished_job(
    job_store: JobStore,
    db: sqlite3.Connection,
    tmp_path: Path,
    *,
    job_id: str,
    finished_at: str,
    log_bytes: bytes = b"x" * 100,
    state: JobState = JobState.SUCCEEDED,
) -> None:
    stdout_path = tmp_path / job_id / "stdout.log"
    stderr_path = tmp_path / job_id / "stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_bytes(log_bytes)
    stderr_path.write_bytes(b"")

    job_store.create(
        job_id=job_id,
        workspace_id=WORKSPACE_ID,
        command="echo hi",
        cwd="/workspace",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
    )
    job_store.mark_running(job_id, pid=1)
    job_store.mark_finished(
        job_id,
        state=state,
        exit_code=0,
        stdout_total_bytes=len(log_bytes),
        stderr_total_bytes=0,
        output_truncated=False,
    )
    # Backdate finished_at directly — mark_finished always uses "now".
    db.execute("UPDATE job SET finished_at = ? WHERE id = ?", (finished_at, job_id))
    db.commit()


def test_prune_deletes_logs_older_than_retention_days(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    _create_finished_job(
        job_store, db, tmp_path, job_id="job_old", finished_at="2000-01-01T00:00:00+00:00"
    )

    pruned = prune_job_logs(job_store, job_retention_days=7, job_log_max_total_bytes=10**9)

    assert pruned == ["job_old"]
    record = job_store.get("job_old")
    assert record.logs_pruned is True
    assert not Path(record.stdout_log_path).exists()  # type: ignore[arg-type]


def test_prune_keeps_recent_jobs(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    _create_finished_job(
        job_store, db, tmp_path, job_id="job_recent", finished_at="2100-01-01T00:00:00+00:00"
    )

    pruned = prune_job_logs(job_store, job_retention_days=7, job_log_max_total_bytes=10**9)

    assert pruned == []
    record = job_store.get("job_recent")
    assert record.logs_pruned is False
    assert Path(record.stdout_log_path).exists()  # type: ignore[arg-type]


def test_prune_never_touches_a_running_job(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    stdout_path = tmp_path / "job_running" / "stdout.log"
    stdout_path.parent.mkdir(parents=True)
    stdout_path.write_bytes(b"still going")
    job_store.create(
        job_id="job_running",
        workspace_id=WORKSPACE_ID,
        command="sleep 100",
        cwd="/workspace",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(tmp_path / "job_running" / "stderr.log"),
    )
    job_store.mark_running("job_running", pid=1)

    # A retention window of 0 days and a size cap of 0 bytes would prune
    # everything eligible — a running job must never be eligible.
    pruned = prune_job_logs(job_store, job_retention_days=0, job_log_max_total_bytes=0)

    assert pruned == []
    assert job_store.get("job_running").logs_pruned is False
    assert stdout_path.exists()


def test_prune_by_total_size_evicts_oldest_finished_first(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    _create_finished_job(
        job_store,
        db,
        tmp_path,
        job_id="job_a",
        finished_at="2100-01-01T00:00:00+00:00",
        log_bytes=b"x" * 100,
    )
    _create_finished_job(
        job_store,
        db,
        tmp_path,
        job_id="job_b",
        finished_at="2100-01-02T00:00:00+00:00",
        log_bytes=b"x" * 100,
    )
    _create_finished_job(
        job_store,
        db,
        tmp_path,
        job_id="job_c",
        finished_at="2100-01-03T00:00:00+00:00",
        log_bytes=b"x" * 100,
    )
    # Total on-disk is 300 bytes; cap forces pruning down to <= 150.
    pruned = prune_job_logs(job_store, job_retention_days=36500, job_log_max_total_bytes=150)

    assert pruned == ["job_a", "job_b"]  # oldest-finished-first
    assert job_store.get("job_a").logs_pruned is True
    assert job_store.get("job_b").logs_pruned is True
    assert job_store.get("job_c").logs_pruned is False
    assert Path(job_store.get("job_c").stdout_log_path).exists()  # type: ignore[arg-type]


def test_prune_skips_a_job_already_pruned(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    _create_finished_job(
        job_store, db, tmp_path, job_id="job_old", finished_at="2000-01-01T00:00:00+00:00"
    )
    first = prune_job_logs(job_store, job_retention_days=7, job_log_max_total_bytes=10**9)
    assert first == ["job_old"]

    second = prune_job_logs(job_store, job_retention_days=7, job_log_max_total_bytes=10**9)
    assert second == []  # already pruned — not re-processed


def test_prune_marks_lost_jobs_too(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path
) -> None:
    _create_finished_job(
        job_store,
        db,
        tmp_path,
        job_id="job_lost",
        finished_at="2000-01-01T00:00:00+00:00",
        state=JobState.SUCCEEDED,
    )
    # mark_lost is a no-op on an already-terminal job, so simulate a job
    # that was actually left running and then reconciled to `lost`.
    db.execute("UPDATE job SET state = 'lost' WHERE id = 'job_lost'")
    db.commit()

    pruned = prune_job_logs(job_store, job_retention_days=7, job_log_max_total_bytes=10**9)
    assert pruned == ["job_lost"]


def test_at_g2r011_a_failure_to_prune_one_job_does_not_abort_the_rest(
    job_store: JobStore, db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2R-011 (`docs/GAP2_SPEC.md`): `prune_job_logs` runs
    opportunistically inline on every `exec_start` (this module's own
    docstring) — a failure pruning one eligible job (a locked log file,
    a transient DB error) must not raise out of the whole call and fail
    the unrelated `exec_start` that triggered it, and must not stop the
    rest of the batch from being pruned either, the same RR-012
    principle `workspace/manager_lifecycle.py`'s `delete()` already
    applies to its own best-effort cleanup steps."""
    _create_finished_job(
        job_store, db, tmp_path, job_id="job_poisoned", finished_at="2000-01-01T00:00:00+00:00"
    )
    _create_finished_job(
        job_store, db, tmp_path, job_id="job_fine", finished_at="2000-01-02T00:00:00+00:00"
    )
    real_mark_logs_pruned = job_store.mark_logs_pruned

    def _mark_logs_pruned_raising_for_poisoned(job_id: str):  # type: ignore[no-untyped-def]
        if job_id == "job_poisoned":
            raise sqlite3.OperationalError("database is locked")
        return real_mark_logs_pruned(job_id)

    monkeypatch.setattr(job_store, "mark_logs_pruned", _mark_logs_pruned_raising_for_poisoned)

    pruned = prune_job_logs(  # SHALL NOT raise
        job_store, job_retention_days=7, job_log_max_total_bytes=10**9
    )

    assert pruned == ["job_fine"]  # the poisoned job is skipped, not aborting the batch
    assert job_store.get("job_fine").logs_pruned is True
    assert job_store.get("job_poisoned").logs_pruned is False
