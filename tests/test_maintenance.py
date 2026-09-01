"""Focused maintenance health tests for GAP3 Y14."""

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.maintenance import MaintenanceScheduler


def test_g3at_014_maintenance_health_redacts_persisted_failure_text(
    minimal_config: ServerConfig,
) -> None:
    """G3AT-014: maintenance diagnostics must not expose cleanup secrets."""
    db = open_db(minimal_config.state_db_path)
    scheduler = MaintenanceScheduler(minimal_config, db=db, docker_client=None)
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
