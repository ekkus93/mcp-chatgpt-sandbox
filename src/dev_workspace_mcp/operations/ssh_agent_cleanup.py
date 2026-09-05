"""Durable, identity-bound SSH-agent secondary cleanup debt for O9.

The primary ssh-agent process/row can already be gone when unlinking its host
socket fails. This module keeps that post-primary obligation durable without
resurrecting workspace/agent metadata and without persisting a deletion path.
The only cleanup target is reconstructed beneath the trusted state directory,
and an existing artifact is removed only when its device/inode still matches
the identity captured before primary metadata deletion.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dev_workspace_mcp.operations.inventory import (
    ByteObservation,
    InventoryDiagnostic,
    InventoryResource,
    ResourceInventoryResult,
)
from dev_workspace_mcp.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
)
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentRecord, agent_socket_path

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SSHAgentCleanupDebt:
    workspace_id: str
    created_at: str
    last_attempt_at: str | None
    last_error: str | None
    bytes_hint: int | None
    socket_device: int | None
    socket_inode: int | None


@dataclass(frozen=True, slots=True)
class SocketIdentity:
    bytes_hint: int | None
    device: int | None
    inode: int | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _debt_from_row(row: sqlite3.Row) -> SSHAgentCleanupDebt:
    return SSHAgentCleanupDebt(
        workspace_id=str(row["workspace_id"]),
        created_at=str(row["created_at"]),
        last_attempt_at=row["last_attempt_at"],
        last_error=row["last_error"],
        bytes_hint=row["bytes_hint"],
        socket_device=row["socket_device"],
        socket_inode=row["socket_inode"],
    )


def get_ssh_agent_cleanup_debt(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> SSHAgentCleanupDebt | None:
    row = connection.execute(
        """
        SELECT workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
               socket_device, socket_inode
        FROM ssh_agent_cleanup
        WHERE workspace_id = ?
        """,
        (workspace_id,),
    ).fetchone()
    return None if row is None else _debt_from_row(row)


def list_ssh_agent_cleanup_debt(connection: sqlite3.Connection) -> list[SSHAgentCleanupDebt]:
    rows = connection.execute(
        """
        SELECT workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
               socket_device, socket_inode
        FROM ssh_agent_cleanup
        ORDER BY created_at, workspace_id
        """
    ).fetchall()
    return [_debt_from_row(row) for row in rows]


def augment_inventory_with_ssh_agent_cleanup_debt(
    connection: sqlite3.Connection,
    inventory: ResourceInventoryResult,
    *,
    max_diagnostics: int = 64,
) -> ResourceInventoryResult:
    """Append bounded cleanup debt only for inventories that do not collect it natively."""
    if any(resource.kind is ResourceKind.SSH_AGENT_CLEANUP for resource in inventory.resources):
        return inventory

    resources = list(inventory.resources)
    diagnostics = list(inventory.diagnostics)
    budget = min(
        max(inventory.result_limit - len(resources), 0),
        max(inventory.inspection_limit - inventory.inspected_count, 0),
    )
    try:
        rows = connection.execute(
            """
            SELECT workspace_id, bytes_hint, last_error, socket_device, socket_inode
            FROM ssh_agent_cleanup
            ORDER BY created_at, workspace_id
            LIMIT ?
            """,
            (budget + 1,),
        ).fetchall()
    except sqlite3.Error as exc:
        if len(diagnostics) < max_diagnostics:
            diagnostics.append(
                InventoryDiagnostic(
                    code="ssh_agent_cleanup_state_query_failed",
                    message=_safe_error(exc),
                    resource_kind=ResourceKind.SSH_AGENT_CLEANUP,
                )
            )
            diagnostics_truncated = inventory.diagnostics_truncated
        else:
            diagnostics_truncated = True
        return ResourceInventoryResult(
            resources=tuple(resources),
            diagnostics=tuple(diagnostics),
            inspected_count=inventory.inspected_count,
            result_limit=inventory.result_limit,
            inspection_limit=inventory.inspection_limit,
            truncated=inventory.truncated or diagnostics_truncated,
            incomplete=True,
            diagnostics_truncated=diagnostics_truncated,
        )

    overflow = len(rows) > budget
    inspected = inventory.inspected_count
    for row in rows[:budget]:
        inspected += 1
        bytes_hint = row["bytes_hint"]
        bytes_observation = (
            ByteObservation.accounted(int(bytes_hint))
            if isinstance(bytes_hint, int) and not isinstance(bytes_hint, bool)
            else ByteObservation.unavailable("SSH-agent cleanup byte accounting is unavailable")
        )
        resources.append(
            InventoryResource(
                kind=ResourceKind.SSH_AGENT_CLEANUP,
                resource_id=str(row["workspace_id"]),
                lifecycle_state=ResourceLifecycleState.CLEANUP_PENDING,
                ownership=OwnershipProof(
                    status=OwnershipStatus.OWNED,
                    evidence=("durable ssh_agent_cleanup tombstone row",),
                    reason="positive durable-state ownership proof",
                ),
                bytes=bytes_observation,
                workspace_id=str(row["workspace_id"]),
                details={
                    "last_error_recorded": row["last_error"] is not None,
                    "socket_device": row["socket_device"],
                    "socket_inode": row["socket_inode"],
                },
            )
        )

    return ResourceInventoryResult(
        resources=tuple(resources),
        diagnostics=tuple(diagnostics),
        inspected_count=inspected,
        result_limit=inventory.result_limit,
        inspection_limit=inventory.inspection_limit,
        truncated=inventory.truncated or overflow,
        incomplete=inventory.incomplete or overflow,
        diagnostics_truncated=inventory.diagnostics_truncated,
    )


def _validate_workspace_id(workspace_id: str) -> None:
    if (
        not workspace_id
        or workspace_id in {".", ".."}
        or Path(workspace_id).name != workspace_id
        or "/" in workspace_id
    ):
        raise ValueError("unsafe workspace id for ssh-agent cleanup")


def cleanup_socket_path(state_dir: Path, workspace_id: str) -> Path:
    """Reconstruct the only host path this debt is allowed to remove."""
    _validate_workspace_id(workspace_id)
    return agent_socket_path(state_dir / "ssh-agents", workspace_id)


def observe_socket_identity(path: Path) -> SocketIdentity:
    """Best-effort no-follow identity/size snapshot for one exact socket path."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return SocketIdentity(bytes_hint=0, device=None, inode=None)
    except OSError:
        return SocketIdentity(bytes_hint=None, device=None, inode=None)
    return SocketIdentity(
        bytes_hint=max(int(info.st_size), 0),
        device=int(info.st_dev),
        inode=int(info.st_ino),
    )


def transition_ssh_agent_to_cleanup_debt(
    connection: sqlite3.Connection,
    record: SSHAgentRecord,
    identity: SocketIdentity,
) -> SSHAgentCleanupDebt:
    """Atomically replace an exact primary ssh-agent row with cleanup debt."""
    created_at = _now()
    with connection:
        connection.execute(
            """
            INSERT INTO ssh_agent_cleanup
                (workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
                 socket_device, socket_inode)
            VALUES (?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                record.workspace_id,
                created_at,
                identity.bytes_hint,
                identity.device,
                identity.inode,
            ),
        )
        cursor = connection.execute(
            """
            DELETE FROM ssh_agent
            WHERE workspace_id = ? AND pid = ? AND boot_id = ?
              AND process_start_time = ? AND socket_path = ?
            """,
            (
                record.workspace_id,
                record.pid,
                record.boot_id,
                record.process_start_time,
                str(record.socket_path),
            ),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError("exact ssh-agent primary identity changed")
    return SSHAgentCleanupDebt(
        workspace_id=record.workspace_id,
        created_at=created_at,
        last_attempt_at=None,
        last_error=None,
        bytes_hint=identity.bytes_hint,
        socket_device=identity.device,
        socket_inode=identity.inode,
    )


def _safe_error(exc: BaseException) -> str:
    """Persist only a bounded, path-free failure class/errno."""
    if isinstance(exc, OSError) and exc.errno is not None:
        return f"{type(exc).__name__}: errno={exc.errno}"
    return type(exc).__name__


def _update_failure(
    connection: sqlite3.Connection,
    debt: SSHAgentCleanupDebt,
    *,
    reason: str,
    error: BaseException | None = None,
    bytes_hint: int | None = None,
) -> None:
    diagnostic = reason if error is None else f"{reason}: {_safe_error(error)}"
    try:
        with connection:
            connection.execute(
                """
                UPDATE ssh_agent_cleanup
                SET last_attempt_at = ?, last_error = ?, bytes_hint = ?
                WHERE workspace_id = ?
                """,
                (_now(), diagnostic[:1024], bytes_hint, debt.workspace_id),
            )
    except sqlite3.Error as exc:
        _logger.error(
            "ssh-agent cleanup: failed to update durable debt for %s: %s",
            debt.workspace_id,
            _safe_error(exc),
        )


def _clear_debt(connection: sqlite3.Connection, workspace_id: str) -> bool:
    try:
        with connection:
            connection.execute(
                "DELETE FROM ssh_agent_cleanup WHERE workspace_id = ?",
                (workspace_id,),
            )
    except sqlite3.Error as exc:
        _logger.error(
            "ssh-agent cleanup: failed to clear durable debt for %s: %s",
            workspace_id,
            _safe_error(exc),
        )
        return False
    return True


def attempt_ssh_agent_cleanup(
    connection: sqlite3.Connection,
    *,
    state_dir: Path,
    debt: SSHAgentCleanupDebt,
) -> str | None:
    """Retry one exact secondary artifact; return failure reason or ``None``."""
    try:
        path = cleanup_socket_path(state_dir, debt.workspace_id)
    except ValueError as exc:
        _update_failure(
            connection,
            debt,
            reason="identity_unprovable",
            error=exc,
            bytes_hint=debt.bytes_hint,
        )
        return "identity_unprovable"

    try:
        info = path.lstat()
    except FileNotFoundError:
        return None if _clear_debt(connection, debt.workspace_id) else "deletion_unproven"
    except OSError as exc:
        _update_failure(
            connection,
            debt,
            reason="identity_probe_failed",
            error=exc,
            bytes_hint=debt.bytes_hint,
        )
        return "identity_probe_failed"

    measured_bytes = max(int(info.st_size), 0)
    if debt.socket_device is None or debt.socket_inode is None:
        _update_failure(
            connection,
            debt,
            reason="identity_unprovable",
            bytes_hint=measured_bytes,
        )
        return "identity_unprovable"
    if int(info.st_dev) != debt.socket_device or int(info.st_ino) != debt.socket_inode:
        _update_failure(
            connection,
            debt,
            reason="identity_changed",
            bytes_hint=debt.bytes_hint,
        )
        return "identity_changed"

    try:
        path.unlink()
    except FileNotFoundError:
        return None if _clear_debt(connection, debt.workspace_id) else "deletion_unproven"
    except OSError as exc:
        _update_failure(
            connection,
            debt,
            reason="secondary_cleanup_failed",
            error=exc,
            bytes_hint=measured_bytes,
        )
        return "secondary_cleanup_failed"

    try:
        path.lstat()
    except FileNotFoundError:
        return None if _clear_debt(connection, debt.workspace_id) else "deletion_unproven"
    except OSError as exc:
        _update_failure(
            connection,
            debt,
            reason="deletion_probe_failed",
            error=exc,
            bytes_hint=measured_bytes,
        )
        return "deletion_probe_failed"

    _update_failure(
        connection,
        debt,
        reason="deletion_unproven",
        bytes_hint=measured_bytes,
    )
    return "deletion_unproven"
