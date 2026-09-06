"""Post-MVP Operations O11 / PMOAT-011 conservative-maintenance acceptance."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import docker
import pytest

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.jobs.retention import get_job_log_cleanup_debt
from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.maintenance import MaintenanceScheduler
from dev_workspace_mcp.operations.ssh_agent_cleanup import (
    cleanup_socket_path,
    get_ssh_agent_cleanup_debt,
    observe_socket_identity,
)
from dev_workspace_mcp.project_config import ProjectCacheEntry
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.caches import build_cache_mounts, resolve_caches
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceState


def _create_job_log_cleanup_debt(
    db: sqlite3.Connection,
    *,
    state_dir: Path,
    workspace_id: str,
) -> tuple[str, Path]:
    job_id = "o11_job_cleanup_debt"
    job_dir = state_dir / "jobs" / job_id
    job_dir.mkdir(parents=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    stdout_path.write_text("authorized cleanup debt stdout\n")
    stderr_path.write_text("authorized cleanup debt stderr\n")

    store = JobStore(db)
    store.create(
        job_id=job_id,
        workspace_id=workspace_id,
        command="true",
        cwd="/workspace",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
    )
    with db:
        db.execute("DELETE FROM job WHERE id = ?", (job_id,))
    assert get_job_log_cleanup_debt(db, job_id) is not None
    return job_id, job_dir


def _create_ssh_cleanup_debt(
    db: sqlite3.Connection,
    *,
    state_dir: Path,
) -> tuple[str, Path]:
    workspace_id = "o11_ssh_cleanup_debt"
    socket_path = cleanup_socket_path(state_dir, workspace_id)
    socket_path.parent.mkdir(parents=True)
    socket_path.write_bytes(b"already-authorized secondary cleanup")
    identity = observe_socket_identity(socket_path)
    assert identity.device is not None
    assert identity.inode is not None

    with db:
        db.execute(
            """
            INSERT INTO ssh_agent_cleanup
                (workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
                 socket_device, socket_inode)
            VALUES (?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                workspace_id,
                datetime.now(UTC).isoformat(),
                identity.bytes_hint,
                identity.device,
                identity.inode,
            ),
        )
    assert get_ssh_agent_cleanup_debt(db, workspace_id) is not None
    return workspace_id, socket_path


def _assert_retained_resources_unchanged(
    *,
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    workspace_id: str,
    container_id: str,
    container_labels: dict[str, str],
    cache_volume_name: str,
    cache_labels: dict[str, str],
    image_id: str,
) -> None:
    assert manager.get(workspace_id).state is WorkspaceState.STOPPED

    container = docker_client.containers.get(container_id)
    container.reload()
    assert container.id == container_id
    assert container.status != "running"
    assert (container.attrs.get("Config", {}).get("Labels") or {}) == container_labels

    volume = docker_client.volumes.get(cache_volume_name)
    volume.reload()
    assert volume.name == cache_volume_name
    assert (volume.attrs.get("Labels") or {}) == cache_labels

    assert docker_client.images.get(image_id).id == image_id


def test_pmoat_011_multiple_cycles_retry_only_authorized_cleanup_debt(
    minimal_config: ServerConfig,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated maintenance must never turn visibility into broad reclamation."""
    config = minimal_config
    state_dir = config.state_db_path.parent
    db = open_db(config.state_db_path)

    repository = RepositoryRecord(
        id="o11-retained",
        canonical_path=tmp_path / "retained-repository",
        github="owner/o11-retained",
    )
    repository.canonical_path.mkdir(parents=True)
    manager = DockerWorkspaceManager(docker_client, db)
    cache = resolve_caches(
        repository_id=repository.id,
        project_caches=[ProjectCacheEntry(name="cargo", path="$HOME/.cargo")],
    )[0]
    record = manager.ensure_workspace(
        repository=repository,
        image="alpine:3.20",
        run_kwargs={
            "command": ["sleep", "3600"],
            "mounts": build_cache_mounts([cache]),
        },
    )
    assert record.container_id is not None
    manager.stop(record.id)
    with db:
        db.execute(
            "UPDATE workspace SET last_used_at = datetime('now', '-90 days') || '+00:00' "
            "WHERE id = ?",
            (record.id,),
        )

    container = docker_client.containers.get(record.container_id)
    container.reload()
    container_labels = dict(container.attrs.get("Config", {}).get("Labels") or {})
    volume = docker_client.volumes.get(cache.volume_name)
    volume.reload()
    cache_labels = dict(volume.attrs.get("Labels") or {})
    image_id = docker_client.images.get("alpine:3.20").id

    job_id, job_dir = _create_job_log_cleanup_debt(
        db,
        state_dir=state_dir,
        workspace_id=record.id,
    )
    ssh_debt_id, ssh_socket = _create_ssh_cleanup_debt(db, state_dir=state_dir)

    real_unlink = Path.unlink
    failed_once = False

    def fail_first_authorized_ssh_cleanup(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal failed_once
        if path == ssh_socket and not failed_once:
            failed_once = True
            raise PermissionError("O11 simulated first-cycle secondary cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_authorized_ssh_cleanup)

    scheduler = MaintenanceScheduler(config)
    scheduler._run_cycle()

    _assert_retained_resources_unchanged(
        manager=manager,
        docker_client=docker_client,
        workspace_id=record.id,
        container_id=record.container_id,
        container_labels=container_labels,
        cache_volume_name=cache.volume_name,
        cache_labels=cache_labels,
        image_id=image_id,
    )
    assert get_job_log_cleanup_debt(db, job_id) is None
    assert not job_dir.exists()

    remaining = get_ssh_agent_cleanup_debt(db, ssh_debt_id)
    assert remaining is not None
    assert remaining.last_attempt_at is not None
    assert remaining.last_error is not None
    assert "secondary_cleanup_failed" in remaining.last_error
    first_health = scheduler.health()
    assert first_health.status == "degraded"
    assert first_health.last_failure_task == "ssh_agent_cleanup_debt"
    assert first_health.last_failure_item == ssh_debt_id

    scheduler._run_cycle()

    _assert_retained_resources_unchanged(
        manager=manager,
        docker_client=docker_client,
        workspace_id=record.id,
        container_id=record.container_id,
        container_labels=container_labels,
        cache_volume_name=cache.volume_name,
        cache_labels=cache_labels,
        image_id=image_id,
    )
    assert get_ssh_agent_cleanup_debt(db, ssh_debt_id) is None
    assert not ssh_socket.exists()
    assert scheduler.health().status == "healthy"

    db.close()


def test_pmoat_011_documents_that_automatic_age_or_pressure_pruning_is_future_policy() -> None:
    docs_path = Path(__file__).resolve().parents[2] / "docs" / "TOOLS.md"
    docs = docs_path.read_text()
    assert "Automatic age- or pressure-based resource reclamation is not implemented" in docs
    assert "separate explicit configuration contract" in docs
