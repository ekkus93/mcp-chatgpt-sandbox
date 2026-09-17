"""S14: job store (§15.1, §15.2, §23, §67)."""

import pytest

from mcp_chatgpt_sandbox.errors import JobNotFoundError
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


def test_create_starts_in_queued_state(job_store: JobStore) -> None:
    record = job_store.create(
        job_id="job_1",
        workspace_id=WORKSPACE_ID,
        command="echo hi",
        cwd="/workspace",
        stdout_log_path="/state/jobs/job_1/stdout.log",
        stderr_log_path="/state/jobs/job_1/stderr.log",
    )
    assert record.state == JobState.QUEUED
    assert record.workspace_id == WORKSPACE_ID
    assert record.command == "echo hi"
    assert record.cwd == "/workspace"
    assert record.pid is None
    assert record.started_at is None
    assert record.finished_at is None
    assert record.exit_code is None
    assert record.stdout_log_path == "/state/jobs/job_1/stdout.log"
    assert record.stderr_log_path == "/state/jobs/job_1/stderr.log"
    assert record.stdout_total_bytes == 0
    assert record.stderr_total_bytes == 0
    assert record.output_truncated is False
    assert record.logs_pruned is False


def test_get_missing_job_raises(job_store: JobStore) -> None:
    with pytest.raises(JobNotFoundError):
        job_store.get("job_does_not_exist")


def test_get_returns_created_job(job_store: JobStore) -> None:
    _create(job_store)
    record = job_store.get("job_1")
    assert record.id == "job_1"


def test_mark_starting_transitions_state(job_store: JobStore) -> None:
    _create(job_store)
    record = job_store.mark_starting("job_1")
    assert record.state == JobState.STARTING
    assert job_store.get("job_1").state == JobState.STARTING


def test_mark_running_sets_pid_and_started_at(job_store: JobStore) -> None:
    _create(job_store)
    job_store.mark_starting("job_1")
    record = job_store.mark_running("job_1", pid=4242)
    assert record.state == JobState.RUNNING
    assert record.pid == 4242
    assert record.started_at is not None


@pytest.mark.parametrize(
    "state", [JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED, JobState.TIMED_OUT]
)
def test_mark_finished_sets_terminal_state_and_finished_at(
    job_store: JobStore, state: JobState
) -> None:
    _create(job_store)
    job_store.mark_running("job_1", pid=1)
    record = job_store.mark_finished(
        "job_1",
        state=state,
        exit_code=0 if state == JobState.SUCCEEDED else 1,
        stdout_total_bytes=10,
        stderr_total_bytes=0,
        output_truncated=False,
    )
    assert record.state == state
    assert record.finished_at is not None
    assert record.exit_code == (0 if state == JobState.SUCCEEDED else 1)


def test_mark_finished_rejects_lost_state(job_store: JobStore) -> None:
    """`lost` only ever comes from `mark_lost` (§15.4) — a job the daemon
    actually watched to completion is never `lost`."""
    _create(job_store)
    with pytest.raises(ValueError, match="lost"):
        job_store.mark_finished(
            "job_1",
            state=JobState.LOST,
            exit_code=None,
            stdout_total_bytes=0,
            stderr_total_bytes=0,
            output_truncated=False,
        )


def test_mark_finished_rejects_non_terminal_state(job_store: JobStore) -> None:
    _create(job_store)
    with pytest.raises(ValueError):
        job_store.mark_finished(
            "job_1",
            state=JobState.RUNNING,
            exit_code=None,
            stdout_total_bytes=0,
            stderr_total_bytes=0,
            output_truncated=False,
        )


def test_mark_lost_transitions_from_non_terminal_state(job_store: JobStore) -> None:
    _create(job_store)
    job_store.mark_running("job_1", pid=1)
    record = job_store.mark_lost("job_1")
    assert record.state == JobState.LOST
    assert record.finished_at is not None


def test_mark_lost_on_queued_job_needs_no_pid(job_store: JobStore) -> None:
    _create(job_store)
    record = job_store.mark_lost("job_1")
    assert record.state == JobState.LOST
    assert record.pid is None


def test_mark_lost_is_a_noop_on_an_already_terminal_job(job_store: JobStore) -> None:
    """Idempotent: re-reconciling an already-lost (or otherwise finished)
    job must not stomp on a real recorded outcome."""
    _create(job_store)
    job_store.mark_running("job_1", pid=1)
    first = job_store.mark_finished(
        "job_1",
        state=JobState.SUCCEEDED,
        exit_code=0,
        stdout_total_bytes=5,
        stderr_total_bytes=0,
        output_truncated=False,
    )
    second = job_store.mark_lost("job_1")
    assert second == first


def test_list_by_workspace_returns_jobs_for_that_workspace(job_store: JobStore) -> None:
    _create(job_store, "job_1")
    _create(job_store, "job_2")
    jobs = job_store.list_by_workspace(WORKSPACE_ID)
    assert {j.id for j in jobs} == {"job_1", "job_2"}


def test_list_by_workspace_excludes_other_workspaces(job_store: JobStore) -> None:
    _create(job_store, "job_1")
    assert job_store.list_by_workspace("ws_other") == []


def test_list_by_state_filters_correctly(job_store: JobStore) -> None:
    _create(job_store, "job_1")
    _create(job_store, "job_2")
    job_store.mark_running("job_2", pid=1)
    queued = job_store.list_by_state(JobState.QUEUED)
    running = job_store.list_by_state(JobState.RUNNING)
    assert [j.id for j in queued] == ["job_1"]
    assert [j.id for j in running] == ["job_2"]


def test_list_by_state_accepts_multiple_states(job_store: JobStore) -> None:
    _create(job_store, "job_1")
    _create(job_store, "job_2")
    job_store.mark_starting("job_2")
    jobs = job_store.list_by_state(JobState.QUEUED, JobState.STARTING)
    assert {j.id for j in jobs} == {"job_1", "job_2"}
