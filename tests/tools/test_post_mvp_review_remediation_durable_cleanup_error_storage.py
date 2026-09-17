"""R3 / PMORRAT-004 durable cleanup error hygiene acceptance."""

from __future__ import annotations

import errno
import json
import logging
from dataclasses import asdict
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.config.models import ServerConfig
from mcp_chatgpt_sandbox.db.connection import open_db
from mcp_chatgpt_sandbox.jobs import retention
from mcp_chatgpt_sandbox.jobs.retention import (
    attempt_job_log_cleanup,
    get_job_log_cleanup_debt,
)
from mcp_chatgpt_sandbox.jobs.store import JobStore
from mcp_chatgpt_sandbox.maintenance import MaintenanceScheduler
from mcp_chatgpt_sandbox.operations.ssh_agent_cleanup import (
    attempt_ssh_agent_cleanup,
    get_ssh_agent_cleanup_debt,
)
from mcp_chatgpt_sandbox.server import create_server

_HOSTILE_DETAIL = (
    "cannot remove /home/phil/.ssh/id_rsa; "
    "DOCKER_AUTH_CONFIG=/home/phil/.docker/config.json; "
    "SSH_AUTH_SOCK=/tmp/private-agent.sock"
)
_LEGACY_DETAIL = (
    "PermissionError: legacy cleanup failure at /srv/private/repo/.ssh/id_ed25519; "
    "DOCKER_CONFIG=/srv/private/.docker; SSH_AUTH_SOCK=/tmp/legacy-agent.sock"
)


def test_r3_cleanup_failures_persist_only_safe_bounded_classification(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PMORRAT-004: new cleanup failures must never persist raw exception text."""
    state_dir = minimal_config.state_db_path.parent
    db = open_db(minimal_config.state_db_path)

    job_id = "job-r3-hostile-cleanup"
    job_dir = state_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "stdout.log").write_bytes(b"r3-job-log")
    db.execute(
        """
        INSERT INTO job_log_cleanup
            (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, ?, NULL, NULL, ?)
        """,
        (job_id, "ws-r3-job", "2026-09-06T00:00:00+00:00", 10),
    )
    db.commit()
    job_debt = get_job_log_cleanup_debt(db, job_id)
    assert job_debt is not None

    real_rmtree = retention.shutil.rmtree

    def _hostile_rmtree(path: Path) -> None:
        if Path(path) == job_dir:
            raise PermissionError(errno.EACCES, _HOSTILE_DETAIL)
        real_rmtree(path)

    monkeypatch.setattr(retention.shutil, "rmtree", _hostile_rmtree)
    caplog.set_level(logging.WARNING)

    assert attempt_job_log_cleanup(db, state_dir=state_dir, debt=job_debt) is False
    persisted_job = get_job_log_cleanup_debt(db, job_id)
    assert persisted_job is not None
    assert persisted_job.last_error == "cleanup_failed: PermissionError: errno=13"
    assert _HOSTILE_DETAIL not in persisted_job.last_error
    assert str(state_dir) not in caplog.text
    assert _HOSTILE_DETAIL not in caplog.text

    workspace_id = "ws-r3-hostile-ssh-cleanup"
    socket_path = state_dir / "ssh-agents" / workspace_id / "agent.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_bytes(b"r3-socket")
    socket_stat = socket_path.lstat()
    db.execute(
        """
        INSERT INTO ssh_agent_cleanup
            (workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
             socket_device, socket_inode)
        VALUES (?, ?, NULL, NULL, ?, ?, ?)
        """,
        (
            workspace_id,
            "2026-09-06T00:00:00+00:00",
            socket_stat.st_size,
            socket_stat.st_dev,
            socket_stat.st_ino,
        ),
    )
    db.commit()
    ssh_debt = get_ssh_agent_cleanup_debt(db, workspace_id)
    assert ssh_debt is not None

    real_unlink = Path.unlink

    def _hostile_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == socket_path:
            raise PermissionError(errno.EACCES, _HOSTILE_DETAIL)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _hostile_unlink)
    assert (
        attempt_ssh_agent_cleanup(db, state_dir=state_dir, debt=ssh_debt)
        == "secondary_cleanup_failed"
    )
    persisted_ssh = get_ssh_agent_cleanup_debt(db, workspace_id)
    assert persisted_ssh is not None
    assert persisted_ssh.last_error == "secondary_cleanup_failed: PermissionError: errno=13"
    assert _HOSTILE_DETAIL not in persisted_ssh.last_error


async def test_r3_legacy_raw_cleanup_error_is_not_republished(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PMORRAT-004: legacy raw debt text must stay out of health/public/audit surfaces."""
    db = open_db(minimal_config.state_db_path)
    job_id = "job-r3-legacy-error"
    db.execute(
        """
        INSERT INTO job_log_cleanup
            (job_id, workspace_id, created_at, last_attempt_at, last_error, bytes_hint)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        (
            job_id,
            "ws-r3-legacy",
            "2026-09-06T00:00:00+00:00",
            _LEGACY_DETAIL,
            7,
        ),
    )
    db.commit()

    # Keep the legacy row present so the scheduler reports the existing debt
    # rather than retrying/clearing it during this compatibility assertion.
    monkeypatch.setattr(
        "mcp_chatgpt_sandbox.maintenance.prune_job_logs",
        lambda *args, **kwargs: [],
    )
    scheduler = MaintenanceScheduler(minimal_config)
    failures: list[tuple[str, str | None, str]] = []
    scheduler._run_job_log_maintenance(failures, job_store=JobStore(db), db=db)
    scheduler._finish_cycle(failures)
    health = scheduler.health()
    assert health.status == "degraded"
    assert health.last_failure_task == "job_log_cleanup_debt"
    assert health.last_failure_item == job_id
    assert health.last_failure_reason == "cleanup_failure_recorded"
    assert _LEGACY_DETAIL not in json.dumps(asdict(health), sort_keys=True)

    monkeypatch.setattr(
        "mcp_chatgpt_sandbox.server._try_docker_client",
        lambda: None,
    )
    server = create_server(minimal_config)
    inventory = (await server.call_tool("resource_inventory", {})).structured_content
    plan = (
        await server.call_tool(
            "resource_prune_plan",
            {"resource_kinds": ["job_log_cleanup"]},
        )
    ).structured_content
    assert plan["candidate_count"] == 1

    assert _LEGACY_DETAIL not in json.dumps(inventory, sort_keys=True)
    assert _LEGACY_DETAIL not in json.dumps(plan, sort_keys=True)
    audit_text = minimal_config.audit.path.read_text(encoding="utf-8")
    assert _LEGACY_DETAIL not in audit_text
