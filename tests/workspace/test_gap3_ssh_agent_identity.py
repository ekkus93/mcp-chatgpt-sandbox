"""GAP3 ssh-agent process-identity regression tests.

The persisted ssh-agent row survives daemon restarts, so a bare PID is not
an identity: the original agent can die and the kernel can reuse that
integer for an unrelated process before reconciliation runs.
"""

import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _legacy_agent_row(
    db: sqlite3.Connection, *, workspace_id: str, pid: int, socket_path: Path
) -> None:
    """Insert the exact pre-G3R-002 persisted shape: PID + socket only."""
    db.execute(
        """
        INSERT INTO ssh_agent (workspace_id, pid, socket_path, created_at)
        VALUES (?, ?, ?, '2000-01-01T00:00:00+00:00')
        """,
        (workspace_id, pid, str(socket_path)),
    )
    db.commit()


def test_at_g3at002_reconcile_never_signals_a_pid_only_legacy_record(
    tmp_path: Path,
) -> None:
    """G3AT-002: deterministic PID-reuse simulation.

    Spawn a real, unrelated host process and deliberately persist *its* PID
    as though it belonged to a pre-restart ssh-agent row. The pre-fix
    reconciler trusts the integer and SIGTERMs this process. A safe
    reconciler treats a legacy row with no restart-stable identity as
    unverifiable: forget/quarantine the stale tracking and socket, but do
    not send any signal to that PID.
    """
    db = open_db(":memory:")
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    unrelated = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _legacy_agent_row(
            db,
            workspace_id="ws_pid_reused",
            pid=unrelated.pid,
            socket_path=tmp_path / "agents" / "ws_pid_reused" / "agent.sock",
        )

        manager.reconcile({})

        assert _is_alive(unrelated.pid), "reconciliation signaled an unrelated reused PID"
        assert manager.get("ws_pid_reused") is None
    finally:
        if _is_alive(unrelated.pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(unrelated.pid, signal.SIGTERM)
            unrelated.wait(timeout=5)
