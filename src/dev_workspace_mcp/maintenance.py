"""Lifecycle-owned periodic maintenance (G3R-014 / G3AT-014).

Idle workspace stopping and job-log retention both existed before Y14, but
only as opportunistic work attached to unrelated MCP requests. This module
owns the real server-lifecycle scheduler: it starts with the MCP lifespan,
runs both maintenance families on a bounded interval without client traffic,
isolates failures, and stops cleanly with the daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import docker

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.jobs.retention import (
    list_job_log_cleanup_debt,
    pending_job_log_cleanup_bytes,
    prune_job_logs,
)
from dev_workspace_mcp.jobs.store import NON_TERMINAL_STATES, TERMINAL_STATES, JobStore
from dev_workspace_mcp.logging_config import redacted_extra
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager

_logger = logging.getLogger(__name__)

DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class MaintenanceHealth:
    status: Literal["healthy", "degraded"]
    running: bool
    last_cycle_at: str | None
    last_failure_at: str | None
    last_failure_task: str | None
    last_failure_item: str | None
    last_failure_reason: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_reason(exc: BaseException) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    value = redacted_extra({"reason": raw})["reason"]
    return str(value)[:1024]


class MaintenanceScheduler:
    """Periodic maintenance task owned by one MCP server lifespan."""

    def __init__(
        self,
        config: ServerConfig,
        *,
        db: sqlite3.Connection,
        docker_client: docker.DockerClient | None,
        interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("maintenance interval must be > 0")
        self._config = config
        self._db = db
        self._job_store = JobStore(db)
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._health_lock = threading.Lock()
        self._health = MaintenanceHealth(
            status="healthy",
            running=False,
            last_cycle_at=None,
            last_failure_at=None,
            last_failure_task=None,
            last_failure_item=None,
            last_failure_reason=None,
        )

        ssh_agents = (
            SSHAgentManager(db, runtime_dir=config.state_db_path.parent / "ssh-agents")
            if config.ssh.mode == "agent"
            else None
        )
        self._manager = (
            DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agents)
            if docker_client is not None
            else None
        )

    def health(self) -> MaintenanceHealth:
        with self._health_lock:
            return self._health

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._update_health(running=True)
        self._task = asyncio.create_task(self._run_loop(), name="dev-workspace-maintenance")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._update_health(running=False)

    async def _run_loop(self) -> None:
        while True:
            try:
                self._run_cycle()
            except Exception as exc:  # noqa: BLE001 - loop must survive unexpected cycle defects
                _logger.exception("maintenance cycle failed unexpectedly")
                self._finish_cycle([("maintenance_cycle", None, _safe_reason(exc))])
            await asyncio.sleep(self._interval_seconds)

    def _run_cycle(self) -> None:
        failures: list[tuple[str, str | None, str]] = []
        self._run_idle_sweep(failures)
        self._run_job_log_maintenance(failures)
        self._finish_cycle(failures)

    def _run_idle_sweep(self, failures: list[tuple[str, str | None, str]]) -> None:
        if self._config.workspace.idle_stop_after_seconds is None:
            return
        if self._manager is None:
            failures.append(
                (
                    "idle_workspace_sweep",
                    None,
                    "ContainerRuntimeFailedError: Docker is not reachable; idle maintenance cannot run",
                )
            )
            return

        try:
            due = self._idle_due_workspace_ids()
            stopped = set(
                self._manager.sweep_idle_workspaces(
                    idle_stop_after_seconds=self._config.workspace.idle_stop_after_seconds,
                    job_store=self._job_store,
                    audit_config=self._config.audit,
                )
            )
            for workspace_id in sorted(due - stopped):
                # `sweep_idle_workspaces` already isolates/logs each
                # WorkspaceError and leaves the workspace running for retry.
                # It intentionally does not expose the swallowed exception,
                # so health reports the failed item without inventing a more
                # specific error type than the scheduler can prove.
                failures.append(
                    (
                        "idle_workspace_sweep",
                        workspace_id,
                        "WorkspaceError: scheduled idle-stop did not complete; see operational log",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - isolate the maintenance family
            _logger.exception("maintenance: idle workspace sweep failed")
            failures.append(("idle_workspace_sweep", None, _safe_reason(exc)))

    def _idle_due_workspace_ids(self) -> set[str]:
        assert self._config.workspace.idle_stop_after_seconds is not None
        cutoff_seconds = self._config.workspace.idle_stop_after_seconds
        now = datetime.now(UTC)
        due: set[str] = set()
        rows = self._db.execute(
            "SELECT id, last_used_at FROM workspace WHERE state = 'running' AND mode = 'persistent'"
        ).fetchall()
        for row in rows:
            idle_seconds = (now - datetime.fromisoformat(row["last_used_at"])).total_seconds()
            if idle_seconds < cutoff_seconds:
                continue
            if any(
                job.state in NON_TERMINAL_STATES
                for job in self._job_store.list_by_workspace(row["id"])
            ):
                continue
            due.add(row["id"])
        return due

    def _run_job_log_maintenance(self, failures: list[tuple[str, str | None, str]]) -> None:
        try:
            cleanup_before = {debt.job_id for debt in list_job_log_cleanup_debt(self._db)}
            cutoff = datetime.now(UTC) - timedelta(days=self._config.execution.job_retention_days)
            aged_before = {
                job.id
                for job in self._job_store.list_by_state(*TERMINAL_STATES)
                if (
                    not job.logs_pruned
                    and job.finished_at is not None
                    and datetime.fromisoformat(job.finished_at) < cutoff
                )
            }
            prune_job_logs(
                self._job_store,
                job_retention_days=self._config.execution.job_retention_days,
                job_log_max_total_bytes=self._config.execution.job_log_max_total_bytes,
                state_dir=self._config.state_db_path.parent,
            )

            cleanup_after = {debt.job_id: debt for debt in list_job_log_cleanup_debt(self._db)}
            for job_id in sorted(cleanup_before & cleanup_after.keys()):
                debt = cleanup_after[job_id]
                failures.append(
                    (
                        "job_log_cleanup_debt",
                        job_id,
                        debt.last_error or "cleanup debt remained after scheduled retry",
                    )
                )

            for job_id in sorted(aged_before):
                try:
                    job = self._job_store.get(job_id)
                except Exception as exc:  # noqa: BLE001 - report item, keep scheduler alive
                    failures.append(("job_log_retention", job_id, _safe_reason(exc)))
                    continue
                if not job.logs_pruned:
                    failures.append(
                        (
                            "job_log_retention",
                            job_id,
                            "expired job logs remained after scheduled retention",
                        )
                    )

            self._record_over_budget(failures)
        except Exception as exc:  # noqa: BLE001 - isolate retention from idle maintenance
            _logger.exception("maintenance: job-log retention failed")
            failures.append(("job_log_retention", None, _safe_reason(exc)))

    def _record_over_budget(self, failures: list[tuple[str, str | None, str]]) -> None:
        cleanup_bytes = pending_job_log_cleanup_bytes(
            self._db, state_dir=self._config.state_db_path.parent
        )
        retained_bytes = 0
        for job in self._job_store.list_by_state(*TERMINAL_STATES):
            if job.logs_pruned:
                continue
            for path in (job.stdout_log_path, job.stderr_log_path):
                if path is None:
                    continue
                try:
                    retained_bytes += Path(path).stat().st_size
                except FileNotFoundError:
                    continue
        total_bytes = cleanup_bytes + retained_bytes
        cap = self._config.execution.job_log_max_total_bytes
        if total_bytes > cap:
            failures.append(
                (
                    "job_log_retention",
                    None,
                    f"tracked job-log usage remains above cap: {total_bytes} > {cap} bytes",
                )
            )

    def _finish_cycle(self, failures: list[tuple[str, str | None, str]]) -> None:
        now = _now()
        with self._health_lock:
            previous = self._health
            if failures:
                task, item, reason = failures[-1]
                self._health = MaintenanceHealth(
                    status="degraded",
                    running=previous.running,
                    last_cycle_at=now,
                    last_failure_at=now,
                    last_failure_task=task,
                    last_failure_item=item,
                    last_failure_reason=reason[:1024],
                )
            else:
                self._health = replace(previous, status="healthy", last_cycle_at=now)

    def _update_health(self, *, running: bool) -> None:
        with self._health_lock:
            self._health = replace(self._health, running=running)
