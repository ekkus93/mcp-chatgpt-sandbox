"""S14: job reconciliation on daemon startup (§15.4)."""

import os
import subprocess
import sys

import pytest

from mcp_chatgpt_sandbox.jobs.reconcile import reconcile_jobs
from mcp_chatgpt_sandbox.jobs.store import JobState, JobStore
from tests.jobs.conftest import WORKSPACE_ID


def _create(job_store: JobStore, job_id: str = "job_1") -> None:
    job_store.create(
        job_id=job_id,
        workspace_id=WORKSPACE_ID,
        command="echo hi",
        cwd="/workspace",
        stdout_log_path=f"/state/jobs/{job_id}/stdout.log",
        stderr_log_path=f"/state/jobs/{job_id}/stderr.log",
    )


def test_reconcile_is_a_noop_when_nothing_is_non_terminal(job_store: JobStore) -> None:
    _create(job_store)
    job_store.mark_finished(
        "job_1",
        state=JobState.SUCCEEDED,
        exit_code=0,
        stdout_total_bytes=0,
        stderr_total_bytes=0,
        output_truncated=False,
    )
    assert reconcile_jobs(job_store) == []
    assert job_store.get("job_1").state == JobState.SUCCEEDED


def test_reconcile_marks_queued_job_lost(job_store: JobStore) -> None:
    """RR-022: a queued job never reached `mark_running`, so it never had
    a pid at all — `None`, not `False` (a queued job's process cannot be
    "confirmed dead"; there never was one to check)."""
    _create(job_store)
    result = reconcile_jobs(job_store)
    assert result == [("job_1", None)]
    assert job_store.get("job_1").state == JobState.LOST


def test_at_h031_reconciliation_outcome_is_logged(
    job_store: JobStore, caplog: pytest.LogCaptureFixture
) -> None:
    """H31 (`docs/HARDENING_TODO.md`, HR-031): reconciliation outcomes
    SHALL be logged — an operator restarting the daemon after an unclean
    shutdown should see this in the log, not only in `serve()`'s own
    stderr summary."""
    _create(job_store)
    with caplog.at_level("INFO", logger="mcp_chatgpt_sandbox.jobs.reconcile"):
        reconcile_jobs(job_store)

    messages = [r.message for r in caplog.records]
    assert any("job_1" in m and "lost" in m for m in messages)
    assert any("1 non-terminal job" in m for m in messages)


def test_at_h031_a_clean_reconciliation_logs_nothing(
    job_store: JobStore, caplog: pytest.LogCaptureFixture
) -> None:
    """No false positives: nothing non-terminal means nothing to log."""
    with caplog.at_level("INFO", logger="mcp_chatgpt_sandbox.jobs.reconcile"):
        result = reconcile_jobs(job_store)
    assert result == []
    assert caplog.records == []


def test_reconcile_marks_starting_job_lost(job_store: JobStore) -> None:
    """RR-022: `starting` precedes `mark_running` too, so this job never
    had a pid either — `None`, same reasoning as the `queued` case."""
    _create(job_store)
    job_store.mark_starting("job_1")
    result = reconcile_jobs(job_store)
    assert result == [("job_1", None)]
    assert job_store.get("job_1").state == JobState.LOST


def test_reconcile_marks_running_job_lost_even_with_no_live_pid(job_store: JobStore) -> None:
    """H28 (`docs/HARDENING_TODO.md`, HR-030): a real, reaped pid — not
    `2**30` (this host's own `pid_max` is 4194304; a hand-picked value
    far past it is a shape `mark_running`'s real callers (always a
    genuine `Popen.pid`) never produce, and `_pid_alive`'s `os.kill`
    call can't actually distinguish "never a valid pid at all" from
    "was valid, has since exited" — both raise `ProcessLookupError` the
    same way, so an out-of-range placeholder risks silently exercising
    the wrong scenario)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    proc.wait(timeout=5)  # exited and reaped — pid is now genuinely dead
    _create(job_store)
    job_store.mark_running("job_1", pid=proc.pid)
    result = reconcile_jobs(job_store)
    assert result == [("job_1", False)]
    assert job_store.get("job_1").state == JobState.LOST


def test_reconcile_flags_a_running_job_whose_pid_is_still_alive(job_store: JobStore) -> None:
    """Reconciliation always marks a `running` job `lost` (see
    reconcile.py's docstring for why), but flags whether the host process
    itself is still alive, so an operator knows to check on it by hand."""
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        _create(job_store)
        job_store.mark_running("job_1", pid=proc.pid)
        result = reconcile_jobs(job_store)
        assert result == [("job_1", True)]
        assert job_store.get("job_1").state == JobState.LOST
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_rr022_pid_never_known_is_distinguished_from_pid_confirmed_dead(
    job_store: JobStore,
) -> None:
    """RR-022's actual defect: before the fix, both of these collapsed to
    the identical `("<id>", False)` — discarding the "cannot verify,
    might still be orphaned" signal for the never-started job by making
    it indistinguishable from the confirmed-not-running one."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    proc.wait(timeout=5)  # exited and reaped — pid is now genuinely dead

    _create(job_store, "job_never_started")  # stays `queued`: no pid ever recorded
    _create(job_store, "job_confirmed_dead")
    job_store.mark_running("job_confirmed_dead", pid=proc.pid)

    result = dict(reconcile_jobs(job_store))
    assert result["job_never_started"] is None
    assert result["job_confirmed_dead"] is False
    assert result["job_never_started"] is not result["job_confirmed_dead"]


def test_reconcile_handles_multiple_jobs_independently(job_store: JobStore) -> None:
    _create(job_store, "job_1")
    _create(job_store, "job_2")
    _create(job_store, "job_3")
    job_store.mark_finished(
        "job_2",
        state=JobState.FAILED,
        exit_code=1,
        stdout_total_bytes=0,
        stderr_total_bytes=0,
        output_truncated=False,
    )
    result = reconcile_jobs(job_store)
    assert {job_id for job_id, _ in result} == {"job_1", "job_3"}
    assert job_store.get("job_1").state == JobState.LOST
    assert job_store.get("job_2").state == JobState.FAILED
    assert job_store.get("job_3").state == JobState.LOST


def test_pid_alive_helper_true_for_current_process() -> None:
    from mcp_chatgpt_sandbox.jobs.reconcile import _pid_alive

    assert _pid_alive(os.getpid()) is True


def test_pid_alive_helper_false_for_a_pid_almost_certainly_unused() -> None:
    from mcp_chatgpt_sandbox.jobs.reconcile import _pid_alive

    assert _pid_alive(2**30) is False
