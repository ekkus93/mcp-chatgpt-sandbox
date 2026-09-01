"""Focused maintenance health tests for GAP3 Y14."""

from __future__ import annotations

import asyncio
import threading

import pytest

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.maintenance import MaintenanceScheduler


def test_g3at_014_maintenance_health_redacts_persisted_failure_text(
    minimal_config: ServerConfig,
) -> None:
    """G3AT-014: maintenance diagnostics must not expose cleanup secrets."""
    scheduler = MaintenanceScheduler(minimal_config)
    token = "ghp_" + "a" * 36

    scheduler._finish_cycle(  # noqa: SLF001 - acceptance probes the health boundary directly
        [("job_log_cleanup_debt", "job_secret", f"OSError: failed cleanup with {token}")]
    )

    health = scheduler.health()
    assert health.status == "degraded"
    assert health.last_failure_task == "job_log_cleanup_debt"
    assert health.last_failure_item == "job_secret"
    assert health.last_failure_reason == "[REDACTED]"
    assert token not in (health.last_failure_reason or "")


@pytest.mark.asyncio
async def test_g3at_014_blocking_cycle_does_not_block_server_event_loop(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous Docker/filesystem work must not freeze MCP asyncio."""
    scheduler = MaintenanceScheduler(minimal_config, interval_seconds=60.0)
    worker_started = threading.Event()
    release_worker = threading.Event()

    def blocking_cycle() -> None:
        worker_started.set()
        assert release_worker.wait(timeout=2.0)

    monkeypatch.setattr(scheduler, "_run_cycle", blocking_cycle)
    scheduler.start()
    try:
        assert await asyncio.to_thread(worker_started.wait, 1.0)

        heartbeat = asyncio.Event()

        async def pulse() -> None:
            await asyncio.sleep(0)
            heartbeat.set()

        asyncio.create_task(pulse())
        await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
    finally:
        release_worker.set()
        await scheduler.stop()

    assert scheduler.health().running is False
