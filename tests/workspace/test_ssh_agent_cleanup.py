"""GAP3 Y4 / G3AT-004: credential cleanup must fail closed."""

import contextlib
import os
import signal
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import WorkspaceError
from dev_workspace_mcp.workspace import ssh_agent as ssh_agent_module
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


def _seeded_db() -> sqlite3.Connection:
    db = open_db(":memory:")
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_test', 'repo_test', 'persistent', '/workspace', 'running',
                '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
        """
    )
    db.commit()
    return db


def _real_keypair(tmp_path: Path) -> Path:
    key_path = tmp_path / "testkey"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True,
    )
    return key_path


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _failed_unload_run(real_run):
    def _run(argv, *args, **kwargs):
        if argv == ["ssh-add", "-D"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="synthetic unload failure")
        return real_run(argv, *args, **kwargs)

    return _run


@pytest.fixture
def agent(tmp_path: Path):
    manager = SSHAgentManager(_seeded_db(), runtime_dir=tmp_path / "agents")
    record = manager.ensure("ws_test", _real_keypair(tmp_path))
    yield manager, record
    with contextlib.suppress(ProcessLookupError):
        os.kill(record.pid, signal.SIGTERM)


def test_at_g3at004_failed_key_unload_terminates_the_workspace_agent(
    agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, record = agent
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", _failed_unload_run(real_run))

    manager.unload_key("ws_test")

    assert manager.get("ws_test") is None
    assert not _is_alive(record.pid)


def test_at_g3at004_failed_unload_and_failed_termination_raise_a_typed_cleanup_error(
    agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, record = agent
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", _failed_unload_run(real_run))

    def _cannot_terminate(pid: int) -> None:
        assert pid == record.pid
        raise PermissionError("synthetic signal denial")

    monkeypatch.setattr(ssh_agent_module, "_terminate_pid", _cannot_terminate)

    with pytest.raises(WorkspaceError) as exc_info:
        manager.unload_key("ws_test")

    payload = exc_info.value.to_dict()
    assert payload["code"] == "SSH_CREDENTIAL_CLEANUP_FAILED"
    assert payload["recoverable"] is False
    assert manager.get("ws_test") is not None
    assert _is_alive(record.pid)
