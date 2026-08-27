"""S17: idle stop (§8.1), against a real Docker daemon.

Every test creates real containers; `conftest.py`'s `docker_client`
fixture force-removes everything carrying the §50 "managed" label on
teardown, regardless of test outcome.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.config.models import AuditConfig, RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceState
from tests.workspace.conftest import KEEP_ALIVE_RUN_KWARGS, TEST_IMAGE


def _backdate_last_used(db: sqlite3.Connection, workspace_id: str, *, seconds_ago: int) -> None:
    db.execute(
        "UPDATE workspace SET last_used_at = datetime('now', ?) || '+00:00' WHERE id = ?",
        (f"-{seconds_ago} seconds", workspace_id),
    )
    db.commit()


@pytest.fixture
def audit_config(tmp_path: Path) -> AuditConfig:
    return AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")


@pytest.fixture
def job_store(db: sqlite3.Connection) -> JobStore:
    return JobStore(db)


def test_sweep_stops_a_workspace_idle_past_the_threshold(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=100)

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    assert stopped == [record.id]
    assert manager.get(record.id).state == WorkspaceState.STOPPED
    container = docker_client.containers.get(record.container_id)  # type: ignore[arg-type]
    assert container.status != "running"


def test_sweep_leaves_a_recently_used_workspace_running(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=10)

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    assert stopped == []
    assert manager.get(record.id).state == WorkspaceState.RUNNING


def test_sweep_disabled_when_idle_stop_after_is_none(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    """`idle_stop_after: never` parses to `None` (config/models.py) and
    disables the sweep entirely, however old `last_used_at` gets."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=10_000_000)

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=None, job_store=job_store, audit_config=audit_config
    )

    assert stopped == []
    assert manager.get(record.id).state == WorkspaceState.RUNNING


def test_sweep_never_stops_a_workspace_with_a_running_job(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=100)
    job_store.create(
        job_id="job_holding_it_up",
        workspace_id=record.id,
        command="sleep 3600",
        cwd="/workspace",
        stdout_log_path=str(Path("/tmp/job/stdout.log")),
        stderr_log_path=str(Path("/tmp/job/stderr.log")),
    )
    job_store.mark_running("job_holding_it_up", pid=1)

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    assert stopped == []
    assert manager.get(record.id).state == WorkspaceState.RUNNING


def test_sweep_stops_once_the_holding_job_finishes(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=100)
    job_store.create(
        job_id="job_done",
        workspace_id=record.id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(Path("/tmp/job/stdout.log")),
        stderr_log_path=str(Path("/tmp/job/stderr.log")),
    )
    job_store.mark_running("job_done", pid=1)
    job_store.mark_finished(
        "job_done",
        state=JobState.SUCCEEDED,
        exit_code=0,
        stdout_total_bytes=0,
        stderr_total_bytes=0,
        output_truncated=False,
    )

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    assert stopped == [record.id]


def test_sweep_never_touches_an_ephemeral_workspace(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    """§8.1: idle-stop is a persistent-workspace concept. Ephemeral
    workspaces have their own bounded-task lifecycle."""
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, mode="ephemeral", run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=10_000)

    stopped = manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    assert stopped == []
    assert manager.get(record.id).state == WorkspaceState.RUNNING


def test_sweep_writes_an_audit_event(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=100)

    manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )

    lines = audit_config.path.read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["operation"] == "workspace_stopped_idle"
    assert event["result"] == "success"
    assert event["workspace_id"] == record.id
    assert event["repository_id"] == repository.id
    assert event["metadata"]["idle_seconds"] >= 100


def test_sweep_writes_nothing_when_auditing_is_disabled(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    tmp_path: Path,
) -> None:
    record = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, record.id, seconds_ago=100)
    disabled_audit = AuditConfig(enabled=False, path=tmp_path / "audit.jsonl")

    manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=disabled_audit
    )

    assert not disabled_audit.path.exists()


def test_workspace_open_restarts_a_workspace_stopped_for_idleness(
    manager: DockerWorkspaceManager,
    repository: RepositoryRecord,
    db: sqlite3.Connection,
    job_store: JobStore,
    audit_config: AuditConfig,
) -> None:
    """The other half of S17's checklist: `workspace_open` (via
    `ensure_workspace`, S6/S11) already restarts anything not `running` —
    idle-stopped or otherwise. Confirms it specifically for this path."""
    first = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    _backdate_last_used(db, first.id, seconds_ago=100)
    manager.sweep_idle_workspaces(
        idle_stop_after_seconds=60, job_store=job_store, audit_config=audit_config
    )
    assert manager.get(first.id).state == WorkspaceState.STOPPED

    reopened = manager.ensure_workspace(
        repository=repository, image=TEST_IMAGE, run_kwargs=KEEP_ALIVE_RUN_KWARGS
    )
    assert reopened.id == first.id
    assert reopened.state == WorkspaceState.RUNNING


# --- last_used_at is bumped by real usage, not just lifecycle transitions ----------


def _configured_server_config(
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


async def test_exec_bumps_last_used_at(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§8.1: idleness means no *request* touched the workspace, not just
    no lifecycle event — a long-running `exec`-only session must not
    look idle between calls."""
    config = _configured_server_config(
        minimal_config, default_image_tag, tmp_path / "work" / "example"
    )
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    _backdate_last_used(db, workspace_id, seconds_ago=1000)
    before = manager.get(workspace_id).last_used_at

    await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["true"]})

    after = manager.get(workspace_id).last_used_at
    assert after != before


def _configured_real_git_repo(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    """Like `_configured_server_config`, but with an actual git history
    (`git_commit` needs a real repository to operate on, not just a bare
    `.git` directory)."""
    import subprocess

    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def test_at_h024_git_commit_bumps_last_used_at(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H24 (`docs/HARDENING_TODO.md`, HR-022): a real regression —
    `git_commit` never called `touch_last_used`, so an active commit
    stream (a long session doing nothing but repeated `git_commit`
    calls, no `exec`/`read_file` in between) still looked idle and could
    be stopped out from under it by §8.1's sweep."""
    repo_path = tmp_path / "work" / "example"
    config = _configured_real_git_repo(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    # backdated *after* write_file (itself already a `touch_last_used`
    # caller) so what's isolated below is specifically git_commit's own
    # effect, not write_file's.
    _backdate_last_used(db, workspace_id, seconds_ago=1000)
    before = manager.get(workspace_id).last_used_at

    await server.call_tool(
        "git_commit",
        {"workspace_id": workspace_id, "message": "bump last_used_at", "paths": ["a.txt"]},
    )

    after = manager.get(workspace_id).last_used_at
    assert after != before


async def test_read_file_bumps_last_used_at(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured_server_config(minimal_config, default_image_tag, repo_path)
    (repo_path / "README.md").write_text("hello")
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    _backdate_last_used(db, workspace_id, seconds_ago=1000)
    before = manager.get(workspace_id).last_used_at

    await server.call_tool("read_file", {"workspace_id": workspace_id, "path": "README.md"})

    after = manager.get(workspace_id).last_used_at
    assert after != before
