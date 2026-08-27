"""S23: per-workspace `ssh-agent` lifecycle (§19.2, §44.1 steps 4-5),
against real `ssh-agent`/`ssh-add`/`ssh-keygen` processes — no Docker
needed, since the agent itself is a host-side process (§19.2's whole
point: the container only ever talks to its socket)."""

import contextlib
import os
import signal
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig, SSHConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import SSHKeyProvisionFailedError
from dev_workspace_mcp.repositories import RepositoryRecord, RepositoryRegistry
from dev_workspace_mcp.workspace.ssh_agent import (
    SSHAgentManager,
    agent_socket_path,
    resolve_ssh_key_path,
    running_workspace_key_paths,
)


def _real_keypair(tmp_path: Path, name: str = "testkey") -> Path:
    key_path = tmp_path / name
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)
    return key_path


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _identities(socket_path: Path) -> str:
    result = subprocess.run(
        ["ssh-add", "-l"],
        env={**os.environ, "SSH_AUTH_SOCK": str(socket_path)},
        capture_output=True,
        text=True,
    )
    return result.stdout


def _seeded_db(*, workspace_id: str = "ws_test", state: str = "running") -> sqlite3.Connection:
    db = open_db(":memory:")
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES (?, 'repo_test', 'persistent', '/workspace', ?,
                '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
        """,
        (workspace_id, state),
    )
    db.commit()
    return db


@pytest.fixture(autouse=True)
def _cleanup_spawned_agents():
    """Every test here spawns a real `ssh-agent`; kill anything still
    alive afterward regardless of test outcome, mirroring how the Docker
    fixtures in this suite force-remove containers on teardown."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)


# --- resolve_ssh_key_path ------------------------------------------------------------


def test_resolve_ssh_key_path_repository_scope(tmp_path: Path) -> None:
    ssh_config = SSHConfig(
        scope="repository", keys_dir=tmp_path / "keys", known_hosts=tmp_path / "known_hosts"
    )
    repository = RepositoryRecord(
        id="example", canonical_path=tmp_path, github="owner/example", ssh_key="example.ed25519"
    )
    assert resolve_ssh_key_path(ssh_config, repository) == tmp_path / "keys" / "example.ed25519"


def test_resolve_ssh_key_path_repository_scope_none_when_unset(tmp_path: Path) -> None:
    ssh_config = SSHConfig(
        scope="repository", keys_dir=tmp_path / "keys", known_hosts=tmp_path / "known_hosts"
    )
    repository = RepositoryRecord(
        id="example", canonical_path=tmp_path, github="owner/example", ssh_key=None
    )
    assert resolve_ssh_key_path(ssh_config, repository) is None


def test_resolve_ssh_key_path_shared_scope(tmp_path: Path) -> None:
    ssh_config = SSHConfig(
        scope="shared",
        keys_dir=tmp_path / "keys",
        known_hosts=tmp_path / "known_hosts",
        shared_key="shared.ed25519",
    )
    repository = RepositoryRecord(
        id="example", canonical_path=tmp_path, github="owner/example", ssh_key="ignored.ed25519"
    )
    assert resolve_ssh_key_path(ssh_config, repository) == tmp_path / "keys" / "shared.ed25519"


def test_resolve_ssh_key_path_shared_scope_none_when_unset(tmp_path: Path) -> None:
    ssh_config = SSHConfig(
        scope="shared", keys_dir=tmp_path / "keys", known_hosts=tmp_path / "known_hosts"
    )
    repository = RepositoryRecord(id="example", canonical_path=tmp_path, github="owner/example")
    assert resolve_ssh_key_path(ssh_config, repository) is None


# --- SSHAgentManager.ensure / unload_key / terminate ---------------------------------


def test_ensure_starts_a_real_agent_with_the_key_loaded(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")

    record = manager.ensure("ws_test", key_path)
    _cleanup_spawned_agents.append(record.pid)

    assert record.socket_path == agent_socket_path(tmp_path / "agents", "ws_test")
    assert record.socket_path.exists()
    assert _is_alive(record.pid)
    assert "testkey" in _identities(record.socket_path) or "SHA256" in _identities(
        record.socket_path
    )


def test_at_h031_agent_start_and_terminate_are_logged(
    tmp_path: Path, _cleanup_spawned_agents, caplog: pytest.LogCaptureFixture
) -> None:
    """H31 (`docs/HARDENING_TODO.md`, HR-031): ssh-agent lifecycle
    (start, terminate) SHALL be logged."""
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")

    with caplog.at_level("INFO", logger="dev_workspace_mcp.workspace.ssh_agent"):
        record = manager.ensure("ws_test", key_path)
        _cleanup_spawned_agents.append(record.pid)
        manager.terminate("ws_test")

    messages = [r.message for r in caplog.records]
    assert any("started" in m and "ws_test" in m for m in messages)
    assert any("terminated" in m and "ws_test" in m for m in messages)


def test_ensure_is_idempotent_no_churn_on_an_already_running_agent(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")

    first = manager.ensure("ws_test", key_path)
    _cleanup_spawned_agents.append(first.pid)
    second = manager.ensure("ws_test", key_path)

    assert second.pid == first.pid  # same process, not a fresh one


def test_unload_key_removes_the_key_but_leaves_the_agent_running(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    record = manager.ensure("ws_test", key_path)
    _cleanup_spawned_agents.append(record.pid)

    manager.unload_key("ws_test")

    assert _is_alive(record.pid)  # AT-036: the agent itself keeps running
    assert "no identities" in _identities(record.socket_path).lower()


def test_ensure_after_unload_key_reloads_without_restarting_the_agent(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    first = manager.ensure("ws_test", key_path)
    _cleanup_spawned_agents.append(first.pid)
    manager.unload_key("ws_test")

    second = manager.ensure("ws_test", key_path)

    assert second.pid == first.pid
    assert "no identities" not in _identities(second.socket_path).lower()


def test_terminate_kills_the_agent_and_forgets_the_record(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    record = manager.ensure("ws_test", key_path)

    manager.terminate("ws_test")

    assert manager.get("ws_test") is None
    # `terminate` reaps synchronously (`_terminate_pid`'s own `waitpid` —
    # see its docstring for the zombie-process bug that made this
    # necessary), so no polling wait is needed here.
    assert not _is_alive(record.pid)  # AT-036: no agent after delete


def test_terminate_is_a_noop_for_an_unknown_workspace(tmp_path: Path) -> None:
    db = _seeded_db()
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    manager.terminate("ws_never_existed")  # must not raise


def test_ensure_raises_on_a_bad_key_path(tmp_path: Path) -> None:
    db = _seeded_db()
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")

    with pytest.raises(SSHKeyProvisionFailedError):
        manager.ensure("ws_test", tmp_path / "does-not-exist.ed25519")

    # the failed attempt leaves nothing behind to leak or collide with a retry
    assert manager.get("ws_test") is None


# --- §44.1 steps 4-5: daemon-restart reconciliation ----------------------------------


def test_reconcile_starts_fresh_agents_for_running_workspaces(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")

    manager.reconcile({"ws_test": key_path})

    record = manager.get("ws_test")
    assert record is not None
    _cleanup_spawned_agents.append(record.pid)
    assert _is_alive(record.pid)
    assert "no identities" not in _identities(record.socket_path).lower()


def test_reconcile_terminates_agents_for_no_longer_running_workspaces(
    tmp_path: Path,
) -> None:
    db = _seeded_db(workspace_id="ws_stopped", state="stopped")
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    stale = manager.ensure("ws_stopped", key_path)  # simulate a pre-restart agent

    manager.reconcile({})  # nothing is "running" anymore

    assert manager.get("ws_stopped") is None
    assert not _is_alive(stale.pid)


def test_reconcile_replaces_a_tracked_agent_with_a_genuinely_fresh_one(
    tmp_path: Path, _cleanup_spawned_agents
) -> None:
    """§44.1 step 4's own premise: the previous agent died with the
    daemon, so reconciliation never assumes a tracked pid is still good —
    even one that (in this test) actually still is."""
    db = _seeded_db()
    key_path = _real_keypair(tmp_path)
    manager = SSHAgentManager(db, runtime_dir=tmp_path / "agents")
    before = manager.ensure("ws_test", key_path)
    _cleanup_spawned_agents.append(before.pid)

    manager.reconcile({"ws_test": key_path})

    after = manager.get("ws_test")
    assert after is not None
    _cleanup_spawned_agents.append(after.pid)
    assert after.pid != before.pid
    assert not _is_alive(before.pid)  # the old one was really torn down


def test_running_workspace_key_paths_scopes_to_running_persistent_workspaces(
    tmp_path: Path,
) -> None:
    db = open_db(":memory:")
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('example', '/repo/example')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES
            ('ws_example', 'example', 'persistent', '/workspace', 'running',
             '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00'),
            ('ws_stopped', 'example', 'persistent', '/workspace', 'stopped',
             '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
        """
    )
    db.commit()

    ssh_config = SSHConfig(
        scope="repository", keys_dir=tmp_path / "keys", known_hosts=tmp_path / "known_hosts"
    )
    config = ServerConfig(
        version=1,
        state_db_path=tmp_path / "state.db",
        docker={"default_image": "mcp-dev-ubuntu:24.04"},
        ssh={"keys_dir": str(tmp_path / "keys"), "known_hosts": str(tmp_path / "known_hosts")},
        audit={"path": tmp_path / "audit.jsonl"},
        repositories=[
            RepositoryConfig(
                id="example",
                path=tmp_path / "repo",
                github="owner/example",
                ssh_key="example.ed25519",
            )
        ],
    )
    registry = RepositoryRegistry(config)

    result = running_workspace_key_paths(db, registry, ssh_config)

    assert result == {"ws_example": tmp_path / "keys" / "example.ed25519"}
