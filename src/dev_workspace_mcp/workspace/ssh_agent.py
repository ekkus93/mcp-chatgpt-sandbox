"""Per-workspace `ssh-agent` lifecycle (§19.2 `mode: agent`, §44.1 steps
4-5, §8.1's idle-stop key removal) — S23.

One real `ssh-agent -D` (foreground, so its `Popen.pid` is the actual
running agent, not a detaching parent whose child immediately reparents
away — the same reason S15's execution service prefers directly
observable process handles over anything that hides behind a fork) per
workspace, bound to a fixed, deterministic socket path so
`workspace_open` can bind-mount it into the container at the same
container-side path (`container_config.SSH_AGENT_SOCKET_CONTAINER_PATH`)
on every call, whether this is the first time the workspace starts or a
reused persistent one. State is tracked in the `ssh_agent` table (§67-
style: SQLite is the source of truth for "which pid, which socket,"
Docker/the filesystem is not) so a daemon restart's reconciliation
(§44.1 steps 4-5) knows what to kill and what to recreate without
guessing from `ps` output.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dev_workspace_mcp.config.models import SSHConfig
from dev_workspace_mcp.errors import RepositoryNotAllowedError, SSHKeyProvisionFailedError
from dev_workspace_mcp.repositories import RepositoryRecord, RepositoryRegistry

#: How long to wait for a freshly spawned `ssh-agent -D` to actually bind
#: its listening socket before giving up — real, but fast: `ssh-agent`
#: binds its socket before doing anything else, so this is a safety net
#: for scheduler jitter, not a real startup delay.
_AGENT_READY_TIMEOUT_SECONDS = 5.0
_AGENT_READY_POLL_INTERVAL_SECONDS = 0.02


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SSHAgentRecord:
    workspace_id: str
    pid: int
    socket_path: Path


def agent_socket_path(runtime_dir: Path, workspace_id: str) -> Path:
    """A fixed, deterministic per-workspace socket path — the same path
    is bind-mounted into the container on every `workspace_open` for a
    given workspace, whether or not the agent behind it is new."""
    return runtime_dir / workspace_id / "agent.sock"


def ssh_key_fingerprint(key_path: Path) -> str | None:
    """The key's fingerprint, `ssh-keygen -lf`'s own `"SHA256:..."` form
    — for AT-037's `ssh_auth` audit record (`docs/SPEC.md` §37,
    `tools/execution.py`). Computed fresh per call rather than cached
    alongside `SSHAgentRecord`: `ssh-keygen -lf` reads only the public
    key material on disk (never the agent, never any loaded private
    key), so it is cheap and has nothing privileged to go stale. `None`
    on failure (a key file that doesn't exist, or isn't in a format
    `ssh-keygen` recognizes) — best-effort for an audit trail, not a
    provisioning-path check; a key that fails to fingerprint here would
    already have failed loudly in `_load_key` before ever reaching this
    point in practice."""
    result = subprocess.run(  # noqa: S603
        ["ssh-keygen", "-lf", str(key_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # "256 SHA256:AbC123... comment (ED25519)" -> "SHA256:AbC123..."
    fields = result.stdout.strip().split()
    return fields[1] if len(fields) >= 2 else None


def resolve_ssh_key_path(ssh_config: SSHConfig, repository: RepositoryRecord) -> Path | None:
    """§19.1: which key file this repository's workspace should load,
    under `ssh.mode: agent`. `None` when nothing is configured for it —
    a discovered repository (no `repositories[]` entry at all), a
    `scope: repository` entry that omits `ssh_key`, or `scope: shared`
    with no `ssh.shared_key` set. Callers treat `None` as "nothing to
    provision," not an error: not every repository need be push-capable."""
    if ssh_config.scope == "shared":
        return ssh_config.keys_dir / ssh_config.shared_key if ssh_config.shared_key else None
    return ssh_config.keys_dir / repository.ssh_key if repository.ssh_key else None


def _is_alive(pid: int) -> bool:
    """Host-side liveness check (§19.2's agent is a daemon-side process,
    never inside a container, so there is no PID-namespace ambiguity
    here — unlike checking a container-side pid from the host)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not signalable by us — still alive
    return True


def _terminate_pid(pid: int) -> None:
    """SIGTERM, then reap — verified empirically that skipping the reap
    leaves a zombie behind (a real bug this had: `os.kill(pid, 0)` on an
    unreaped zombie still reports "alive", `/proc/<pid>/stat` shows state
    `Z`, until something actually calls `waitpid`), which would make a
    just-terminated agent look alive to `_is_alive`/`ensure` afterward.
    `waitpid` only succeeds when `pid` is actually this process's own
    child — true for an agent this same daemon process started, not for
    one recovered from the `ssh_agent` table after a daemon restart
    (already reparented away before this process ever existed); either
    way the signal itself still reaches and terminates the process, so a
    `ChildProcessError` here is not a failure, just "someone else's job
    to reap."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


class SSHAgentManager:
    """Owns the `ssh_agent` table and the real `ssh-agent`/`ssh-add`
    processes it tracks. `db` is the same connection callers already
    have — no separate store module, since this is a small, single-table
    concern with process-management logic that belongs next to it."""

    def __init__(self, db: sqlite3.Connection, *, runtime_dir: Path) -> None:
        self._db = db
        self._runtime_dir = runtime_dir

    # --- persistence ------------------------------------------------------------

    def get(self, workspace_id: str) -> SSHAgentRecord | None:
        row = self._db.execute(
            "SELECT workspace_id, pid, socket_path FROM ssh_agent WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return SSHAgentRecord(
            workspace_id=row["workspace_id"], pid=row["pid"], socket_path=Path(row["socket_path"])
        )

    def _save(self, record: SSHAgentRecord) -> None:
        self._db.execute(
            """
            INSERT INTO ssh_agent (workspace_id, pid, socket_path, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (workspace_id) DO UPDATE SET
                pid = excluded.pid, socket_path = excluded.socket_path,
                created_at = excluded.created_at
            """,
            (record.workspace_id, record.pid, str(record.socket_path), _now()),
        )
        self._db.commit()

    def _forget(self, workspace_id: str) -> None:
        self._db.execute("DELETE FROM ssh_agent WHERE workspace_id = ?", (workspace_id,))
        self._db.commit()

    def list_all(self) -> list[SSHAgentRecord]:
        rows = self._db.execute("SELECT workspace_id, pid, socket_path FROM ssh_agent").fetchall()
        return [
            SSHAgentRecord(
                workspace_id=r["workspace_id"], pid=r["pid"], socket_path=Path(r["socket_path"])
            )
            for r in rows
        ]

    # --- process lifecycle --------------------------------------------------------

    def ensure(self, workspace_id: str, key_path: Path) -> SSHAgentRecord:
        """If a live agent is already tracked for this workspace, make
        sure `key_path` is loaded into it (`ssh-add` is idempotent —
        verified empirically: re-adding an already-loaded key succeeds
        cleanly rather than erroring — so this is correct both the
        common case, an already-running persistent workspace with its
        key already loaded, and the case right after a stop, whose own
        `unload_key` removed it without killing the agent) and return it
        unchanged. Otherwise start a fresh agent and load `key_path`."""
        existing = self.get(workspace_id)
        if existing is not None and _is_alive(existing.pid):
            self._load_key(existing.socket_path, key_path, workspace_id=workspace_id)
            return existing
        return self._start(workspace_id, key_path)

    def _start(self, workspace_id: str, key_path: Path) -> SSHAgentRecord:
        socket_path = agent_socket_path(self._runtime_dir, workspace_id)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)  # a stale socket from a dead agent blocks binding

        process = subprocess.Popen(  # noqa: S603
            ["ssh-agent", "-D", "-a", str(socket_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + _AGENT_READY_TIMEOUT_SECONDS
        while not socket_path.exists():
            if process.poll() is not None:
                raise SSHKeyProvisionFailedError(
                    f"ssh-agent for workspace {workspace_id!r} exited immediately "
                    f"(exit code {process.returncode})",
                    details={"workspace_id": workspace_id},
                )
            if time.monotonic() >= deadline:
                process.kill()
                raise SSHKeyProvisionFailedError(
                    f"ssh-agent for workspace {workspace_id!r} did not bind its "
                    f"socket within {_AGENT_READY_TIMEOUT_SECONDS}s",
                    details={"workspace_id": workspace_id},
                )
            time.sleep(_AGENT_READY_POLL_INTERVAL_SECONDS)

        try:
            self._load_key(socket_path, key_path, workspace_id=workspace_id)
        except SSHKeyProvisionFailedError:
            process.kill()
            socket_path.unlink(missing_ok=True)
            raise

        record = SSHAgentRecord(workspace_id=workspace_id, pid=process.pid, socket_path=socket_path)
        self._save(record)
        return record

    @staticmethod
    def _load_key(socket_path: Path, key_path: Path, *, workspace_id: str) -> None:
        add_result = subprocess.run(  # noqa: S603
            ["ssh-add", str(key_path)],
            env={**os.environ, "SSH_AUTH_SOCK": str(socket_path)},
            capture_output=True,
            text=True,
        )
        if add_result.returncode != 0:
            raise SSHKeyProvisionFailedError(
                f"failed to load the workspace key into its ssh-agent: {add_result.stderr.strip()}",
                details={"workspace_id": workspace_id, "key_path": str(key_path)},
            )

    def unload_key(self, workspace_id: str) -> None:
        """§8.1/§19.2: "remove it from the agent whenever the workspace
        stops" — the agent process itself keeps running (cheaper than a
        full stop/start cycle on every idle-stop, and `ensure` above
        already treats "agent alive, key or not" correctly by never
        re-checking the key — a workspace that stopped and starts again
        goes through `ensure`, which finds the still-alive agent and
        returns it as-is with no key; `workspace_open`'s own key-loading
        step, not this one, is what a restart needs to re-provision it).
        Best-effort: a socket that is already gone (agent died some other
        way) is not an error here — there is nothing left to unload from."""
        record = self.get(workspace_id)
        if record is None or not record.socket_path.exists():
            return
        subprocess.run(  # noqa: S603
            ["ssh-add", "-D"],
            env={**os.environ, "SSH_AUTH_SOCK": str(record.socket_path)},
            capture_output=True,
            text=True,
        )

    def terminate(self, workspace_id: str) -> None:
        """§19.2: "terminate the agent when the workspace is deleted."
        Best-effort on the signal itself — a pid that is already gone is
        not an error, just confirmation there is nothing left to do."""
        record = self.get(workspace_id)
        if record is None:
            return
        _terminate_pid(record.pid)
        record.socket_path.unlink(missing_ok=True)
        self._forget(workspace_id)

    # --- §44.1 steps 4-5: daemon-restart reconciliation --------------------------

    def reconcile(self, running_workspaces: dict[str, Path]) -> None:
        """`running_workspaces` maps workspace_id -> key_path for every
        workspace this daemon believes is currently `running` under
        `ssh.mode: agent`. Every previously-tracked agent is torn down
        unconditionally first (§44.1 step 4's own premise: the previous
        agent died with the daemon, so nothing here is assumed reusable),
        then a fresh one is started only for workspaces still running —
        which is also step 5 (orphan termination for a workspace that is
        no longer running): tearing down every tracked agent before
        rebuilding only the still-running subset covers both steps with
        one pass rather than two."""
        for record in self.list_all():
            _terminate_pid(record.pid)
            record.socket_path.unlink(missing_ok=True)
            self._forget(record.workspace_id)

        for workspace_id, key_path in running_workspaces.items():
            self._start(workspace_id, key_path)


def running_workspace_key_paths(
    db: sqlite3.Connection, registry: RepositoryRegistry, ssh_config: SSHConfig
) -> dict[str, Path]:
    """Every currently-`running`, persistent workspace's ssh-agent key
    path, for `SSHAgentManager.reconcile`'s use at daemon startup (§44.1
    steps 4-5) — `cli.py`'s step 6. Scoped to `mode = 'persistent'`:
    `workspace_open` (S23) only ever provisions an agent mount for a
    persistent workspace's container in the first place (see its own
    docstring for why ephemeral workspaces are a documented gap), so
    reconciling one for a `running` ephemeral row would spawn an agent
    nothing in that container references. Skips a workspace whose
    repository is no longer resolvable (repository config changed since
    the row was written) or has nothing configured to provision —
    reconciliation degrades gracefully rather than crashing startup over
    a stale row."""
    rows = db.execute(
        "SELECT id, repository_id FROM workspace WHERE state = 'running' AND mode = 'persistent'"
    ).fetchall()
    result: dict[str, Path] = {}
    for row in rows:
        try:
            repository = registry.get(row["repository_id"])
        except RepositoryNotAllowedError:
            continue
        key_path = resolve_ssh_key_path(ssh_config, repository)
        if key_path is not None:
            result[row["id"]] = key_path
    return result
