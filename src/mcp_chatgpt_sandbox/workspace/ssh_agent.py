"""Per-workspace `ssh-agent` lifecycle (§19.2, §44.1 steps 4-5, §8.1).

G3R-002 (`docs/GAP3_SPEC.md`) makes persisted process identity explicit:
a PID is only an address in the kernel's process table, not an identity.
After a daemon restart the original agent may be gone and that integer may
belong to an unrelated host process. Every signal based on persisted state
therefore requires restart-stable identity proof first.

The proof is Linux-specific by design: this daemon targets Linux Docker
hosts. It combines the host boot ID, `/proc/<pid>/stat` start time, the
current process name from `/proc/<pid>/comm`, and the exact
`ssh-agent -D -a <socket>` command shape. `comm` is deliberate rather than
`/proc/<pid>/exe`: hardened/self-hosted environments can deny reading the
`exe` symlink even when `stat`/`comm`/`cmdline` for the same process remain
readable. Legacy rows without the new identity fields are deliberately
unverifiable and are forgotten without signalling their PID.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mcp_chatgpt_sandbox.config.models import SSHConfig
from mcp_chatgpt_sandbox.errors import (
    RepositoryNotAllowedError,
    SSHCredentialCleanupFailedError,
    SSHKeyProvisionFailedError,
)
from mcp_chatgpt_sandbox.repositories import RepositoryRecord, RepositoryRegistry

_logger = logging.getLogger(__name__)

_AGENT_READY_TIMEOUT_SECONDS = 5.0
_AGENT_READY_POLL_INTERVAL_SECONDS = 0.02
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_PROC_ROOT = Path("/proc")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SSHAgentRecord:
    workspace_id: str
    pid: int
    socket_path: Path
    boot_id: str | None = None
    process_start_time: int | None = None


@dataclass(frozen=True, slots=True)
class _ProcessIdentity:
    boot_id: str
    process_start_time: int


def agent_socket_path(runtime_dir: Path, workspace_id: str) -> Path:
    return runtime_dir / workspace_id / "agent.sock"


def ssh_key_fingerprint(key_path: Path) -> str | None:
    result = subprocess.run(  # noqa: S603
        ["ssh-keygen", "-lf", str(key_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split()
    return fields[1] if len(fields) >= 2 else None


def resolve_ssh_key_path(ssh_config: SSHConfig, repository: RepositoryRecord) -> Path | None:
    if ssh_config.scope == "shared":
        return ssh_config.keys_dir / ssh_config.shared_key if ssh_config.shared_key else None
    return ssh_config.keys_dir / repository.ssh_key if repository.ssh_key else None


def _read_boot_id() -> str:
    value = _BOOT_ID_PATH.read_text().strip()
    if not value:
        raise OSError(f"empty Linux boot id at {_BOOT_ID_PATH}")
    return value


def _read_process_start_time(pid: int) -> int:
    """Return Linux `/proc/<pid>/stat` field 22 (clock ticks since boot).

    The process name in field 2 is parenthesized and may contain spaces or
    `)`, so split only after its final `)` instead of naïvely splitting the
    whole line. The first token after that delimiter is field 3 (`state`),
    making field 22 index 19 in the remaining token list.
    """
    raw = (_PROC_ROOT / str(pid) / "stat").read_text()
    close_paren = raw.rfind(")")
    if close_paren < 0:
        raise ValueError(f"malformed /proc/{pid}/stat: missing process-name terminator")
    fields = raw[close_paren + 1 :].split()
    if len(fields) <= 19:
        raise ValueError(f"malformed /proc/{pid}/stat: missing start-time field")
    return int(fields[19])


def _capture_process_identity(pid: int) -> _ProcessIdentity:
    return _ProcessIdentity(
        boot_id=_read_boot_id(),
        process_start_time=_read_process_start_time(pid),
    )


def _read_process_name(pid: int) -> str:
    """Return Linux `/proc/<pid>/comm` without requiring `/proc/<pid>/exe` access.

    Some hardened procfs/Yama configurations deny `readlink(.../exe)` for
    an otherwise-readable process. `comm` remains sufficient here because
    it is only one component of the identity proof: boot ID + process start
    time prevent PID reuse, while the exact command line below binds the
    process to this workspace's expected ssh-agent/socket invocation.
    """
    name = (_PROC_ROOT / str(pid) / "comm").read_text().strip()
    if not name:
        raise OSError(f"empty Linux process name at /proc/{pid}/comm")
    return name


def _read_cmdline(pid: int) -> list[str]:
    raw = (_PROC_ROOT / str(pid) / "cmdline").read_bytes()
    return [part.decode(errors="surrogateescape") for part in raw.split(b"\0") if part]


def _has_expected_agent_signature(pid: int, socket_path: Path) -> bool:
    """Confirm the live PID still looks like the agent this daemon started."""
    if _read_process_name(pid) != "ssh-agent":
        return False
    cmdline = _read_cmdline(pid)
    if not cmdline or Path(cmdline[0]).name != "ssh-agent" or "-D" not in cmdline:
        return False
    for index, value in enumerate(cmdline[:-1]):
        if value == "-a" and cmdline[index + 1] == str(socket_path):
            return True
    return False


def _record_matches_running_agent(record: SSHAgentRecord) -> bool:
    """Fail closed unless every persisted/current identity component agrees."""
    if record.boot_id is None or record.process_start_time is None:
        return False
    try:
        current = _capture_process_identity(record.pid)
        if current.boot_id != record.boot_id:
            return False
        if current.process_start_time != record.process_start_time:
            return False
        return _has_expected_agent_signature(record.pid, record.socket_path)
    except (OSError, ValueError):
        return False


def _pid_exists(pid: int) -> bool:
    """Liveness only; never use this as process identity proof."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


class SSHAgentManager:
    def __init__(self, db: sqlite3.Connection, *, runtime_dir: Path) -> None:
        self._db = db
        self._runtime_dir = runtime_dir

    def get(self, workspace_id: str) -> SSHAgentRecord | None:
        row = self._db.execute(
            """
            SELECT workspace_id, pid, socket_path, boot_id, process_start_time
            FROM ssh_agent WHERE workspace_id = ?
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return SSHAgentRecord(
            workspace_id=row["workspace_id"],
            pid=row["pid"],
            socket_path=Path(row["socket_path"]),
            boot_id=row["boot_id"],
            process_start_time=row["process_start_time"],
        )

    def _save(self, record: SSHAgentRecord) -> None:
        self._db.execute(
            """
            INSERT INTO ssh_agent
                (workspace_id, pid, socket_path, created_at, boot_id, process_start_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (workspace_id) DO UPDATE SET
                pid = excluded.pid,
                socket_path = excluded.socket_path,
                created_at = excluded.created_at,
                boot_id = excluded.boot_id,
                process_start_time = excluded.process_start_time
            """,
            (
                record.workspace_id,
                record.pid,
                str(record.socket_path),
                _now(),
                record.boot_id,
                record.process_start_time,
            ),
        )
        self._db.commit()

    def _forget(self, workspace_id: str) -> None:
        self._db.execute("DELETE FROM ssh_agent WHERE workspace_id = ?", (workspace_id,))
        self._db.commit()

    def list_all(self) -> list[SSHAgentRecord]:
        rows = self._db.execute(
            "SELECT workspace_id, pid, socket_path, boot_id, process_start_time FROM ssh_agent"
        ).fetchall()
        return [
            SSHAgentRecord(
                workspace_id=row["workspace_id"],
                pid=row["pid"],
                socket_path=Path(row["socket_path"]),
                boot_id=row["boot_id"],
                process_start_time=row["process_start_time"],
            )
            for row in rows
        ]

    def _discard_unverifiable(self, record: SSHAgentRecord, *, operation: str) -> None:
        """Remove our stale access path/state without guessing at process identity."""
        _logger.warning(
            "workspace %s: %s found unverifiable ssh-agent pid=%d; "
            "refusing to signal it and discarding stale tracking",
            record.workspace_id,
            operation,
            record.pid,
        )
        record.socket_path.unlink(missing_ok=True)
        self._forget(record.workspace_id)

    def ensure_agent(self, workspace_id: str) -> SSHAgentRecord:
        """Ensure a mountable per-workspace agent exists without loading a key.

        G3R-005 separates socket/process provisioning from credential
        provisioning so Docker create/start failures cannot leave a
        repository key loaded merely because the socket had to exist
        before container creation.
        """
        existing = self.get(workspace_id)
        if existing is not None:
            if _record_matches_running_agent(existing):
                return existing
            self._discard_unverifiable(existing, operation="ensure_agent")
        return self._start_agent(workspace_id)

    def load_key(self, workspace_id: str, key_path: Path) -> None:
        """Load `key_path` only into the verified agent for `workspace_id`."""
        record = self.get(workspace_id)
        if record is None or not _record_matches_running_agent(record):
            raise SSHKeyProvisionFailedError(
                "cannot load the workspace key: no verified ssh-agent is available",
                details={"workspace_id": workspace_id, "key_path": str(key_path)},
            )
        self._load_key(record.socket_path, key_path, workspace_id=workspace_id)

    def ensure(self, workspace_id: str, key_path: Path) -> SSHAgentRecord:
        """Compatibility helper: ensure an agent and ensure `key_path` is loaded."""
        existing = self.get(workspace_id)
        if existing is not None:
            if _record_matches_running_agent(existing):
                self._load_key(existing.socket_path, key_path, workspace_id=workspace_id)
                return existing
            self._discard_unverifiable(existing, operation="ensure")
        return self._start(workspace_id, key_path)

    def _start_agent(self, workspace_id: str) -> SSHAgentRecord:
        socket_path = agent_socket_path(self._runtime_dir, workspace_id)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)

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
                process.wait()
                raise SSHKeyProvisionFailedError(
                    f"ssh-agent for workspace {workspace_id!r} did not bind its "
                    f"socket within {_AGENT_READY_TIMEOUT_SECONDS}s",
                    details={"workspace_id": workspace_id},
                )
            time.sleep(_AGENT_READY_POLL_INTERVAL_SECONDS)

        try:
            identity = _capture_process_identity(process.pid)
            if not _has_expected_agent_signature(process.pid, socket_path):
                raise OSError("spawned process does not match expected ssh-agent command")
        except (OSError, ValueError) as exc:
            process.kill()
            process.wait()
            socket_path.unlink(missing_ok=True)
            raise SSHKeyProvisionFailedError(
                f"could not capture restart-safe Linux process identity for ssh-agent: {exc}",
                details={"workspace_id": workspace_id},
            ) from exc

        record = SSHAgentRecord(
            workspace_id=workspace_id,
            pid=process.pid,
            socket_path=socket_path,
            boot_id=identity.boot_id,
            process_start_time=identity.process_start_time,
        )
        self._save(record)
        _logger.info("workspace %s: ssh-agent started (pid=%d)", workspace_id, process.pid)
        return record

    def _start(self, workspace_id: str, key_path: Path) -> SSHAgentRecord:
        record = self._start_agent(workspace_id)
        try:
            self._load_key(record.socket_path, key_path, workspace_id=workspace_id)
        except SSHKeyProvisionFailedError:
            self._terminate_for_credential_cleanup(
                record, reason="repository key load failed during ssh-agent startup"
            )
            raise
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

    def _terminate_for_credential_cleanup(self, record: SSHAgentRecord, *, reason: str) -> None:
        """Fail-closed fallback used when key removal cannot be proven.

        G3R-004: because each agent is workspace-scoped, terminating the
        verified agent is stronger and simpler than pretending a failed
        `ssh-add -D` removed the key. The persisted row is intentionally
        kept until termination is both attempted against the verified
        process and observed to have ended. A failure therefore remains
        retryable/observable instead of being warning-and-forget state.
        """
        if not _record_matches_running_agent(record):
            raise SSHCredentialCleanupFailedError(
                "cannot prove SSH credentials were removed: the tracked ssh-agent "
                "identity is no longer verifiable, so it is unsafe to signal the PID",
                details={"workspace_id": record.workspace_id, "stage": reason},
            )
        try:
            _terminate_pid(record.pid)
        except OSError as exc:
            raise SSHCredentialCleanupFailedError(
                "cannot prove SSH credentials were removed: terminating the "
                "workspace ssh-agent failed",
                details={"workspace_id": record.workspace_id, "stage": reason},
            ) from exc

        # `_terminate_pid` normally reaps a same-process child. Re-check
        # identity anyway so a non-child/restart-recovered agent cannot be
        # reported clean merely because SIGTERM was sent successfully.
        if _record_matches_running_agent(record):
            raise SSHCredentialCleanupFailedError(
                "cannot prove SSH credentials were removed: the workspace "
                "ssh-agent is still running after termination",
                details={"workspace_id": record.workspace_id, "stage": reason},
            )

        record.socket_path.unlink(missing_ok=True)
        self._forget(record.workspace_id)
        _logger.warning(
            "workspace %s: %s; terminated the per-workspace ssh-agent as fail-closed fallback",
            record.workspace_id,
            reason,
        )

    def unload_key(self, workspace_id: str) -> None:
        """Remove all keys, or prove the workspace agent itself is gone.

        G3R-004 closes the old silent fallback: `ssh-add -D`'s exit status
        is security-significant and is checked. A nonzero result triggers
        verified agent termination. If that fallback also fails, a typed
        non-recoverable error is raised and the tracking row is preserved
        so a later retry/operator can still identify the cleanup debt.
        stderr is deliberately not reflected into the client/log message;
        only the exit code/reason class is recorded, avoiding credential
        leakage from an unexpected `ssh-add` diagnostic.
        """
        record = self.get(workspace_id)
        if record is None:
            return

        if not record.socket_path.exists():
            if not _pid_exists(record.pid):
                # The tracked process is confirmed gone; no agent remains
                # that could still use a loaded credential.
                self._forget(workspace_id)
                return
            self._terminate_for_credential_cleanup(
                record, reason="ssh-agent socket disappeared before key unload"
            )
            return

        unload = subprocess.run(  # noqa: S603
            ["ssh-add", "-D"],
            env={**os.environ, "SSH_AUTH_SOCK": str(record.socket_path)},
            capture_output=True,
            text=True,
        )
        if unload.returncode == 0:
            return

        _logger.warning(
            "workspace %s: ssh-add -D failed with exit code %d; "
            "terminating per-workspace agent instead",
            workspace_id,
            unload.returncode,
        )
        self._terminate_for_credential_cleanup(
            record, reason=f"ssh-add -D failed with exit code {unload.returncode}"
        )

    def terminate(self, workspace_id: str) -> None:
        record = self.get(workspace_id)
        if record is None:
            return
        if _record_matches_running_agent(record):
            _terminate_pid(record.pid)
            record.socket_path.unlink(missing_ok=True)
            self._forget(workspace_id)
            _logger.info("workspace %s: ssh-agent terminated (pid=%d)", workspace_id, record.pid)
            return
        self._discard_unverifiable(record, operation="terminate")

    def reconcile(self, running_workspaces: dict[str, Path]) -> None:
        """Reconcile restart-persisted agents without ever trusting a PID alone.

        A matching record can be terminated before a fresh agent is
        provisioned. A legacy/mismatched/unreadable record is not signalable:
        its socket path and DB row are removed, with a warning that the old
        host process may remain orphaned. This is intentionally safer than
        risking SIGTERM against a PID the kernel reassigned.
        """
        for record in self.list_all():
            if _record_matches_running_agent(record):
                _terminate_pid(record.pid)
                record.socket_path.unlink(missing_ok=True)
                self._forget(record.workspace_id)
            else:
                self._discard_unverifiable(record, operation="startup reconciliation")

        for workspace_id, key_path in running_workspaces.items():
            self._start(workspace_id, key_path)

        _logger.info("ssh-agent reconciliation: %d agent(s) restarted", len(running_workspaces))


def running_workspace_key_paths(
    db: sqlite3.Connection, registry: RepositoryRegistry, ssh_config: SSHConfig
) -> dict[str, Path]:
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
