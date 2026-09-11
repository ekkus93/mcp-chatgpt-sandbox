"""Post-R8 CRH-004 coverage for SSH-agent cleanup legacy errors."""

import json
from dataclasses import asdict

from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.operations.ssh_agent_cleanup import (
    get_ssh_agent_cleanup_debt,
    list_ssh_agent_cleanup_debt,
)


def _insert_ssh_cleanup_debt(
    config: ServerConfig,
    *,
    workspace_id: str,
    last_error: str | None,
    bytes_hint: int | None = 17,
    socket_device: int | None = 123,
    socket_inode: int | None = 456,
) -> None:
    db = open_db(config.state_db_path)
    with db:
        db.execute(
            """
            INSERT INTO ssh_agent_cleanup
                (workspace_id, created_at, last_attempt_at, last_error, bytes_hint,
                 socket_device, socket_inode)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                "2026-09-10T00:00:00+00:00",
                "2026-09-10T00:01:00+00:00",
                last_error,
                bytes_hint,
                socket_device,
                socket_inode,
            ),
        )


_SAFE_SSH_CLEANUP_ERRORS = [
    "identity_unprovable",
    "identity_changed",
    "deletion_unproven",
    "identity_probe_failed: OSError: errno=5",
    "secondary_cleanup_failed: PermissionError: errno=13",
    "deletion_probe_failed: FileNotFoundError: errno=2",
    "identity_unprovable: ValueError",
]


def test_crhv_004_ssh_cleanup_debt_preserves_safe_persisted_errors(
    minimal_config: ServerConfig,
) -> None:
    for index, last_error in enumerate(_SAFE_SSH_CLEANUP_ERRORS):
        workspace_id = f"ws-crhv-004-safe-{index}"
        _insert_ssh_cleanup_debt(
            minimal_config,
            workspace_id=workspace_id,
            last_error=last_error,
        )
        debt = get_ssh_agent_cleanup_debt(open_db(minimal_config.state_db_path), workspace_id)
        assert debt is not None
        assert debt.last_error == last_error


def test_crhv_004_ssh_cleanup_debt_sanitizes_legacy_raw_last_error(
    minimal_config: ServerConfig,
) -> None:
    docker_env = "DOCKER" + "_CONFIG=/srv/private/.docker"
    ssh_env = "_".join(("SSH", "AUTH", "SOCK")) + "=/tmp/private-agent.sock"
    auth_header = "Authorization: " + "Bear" + "er synthetic-crhv-004-token"
    raw_error = (
        "PermissionError: failed unlink at /home/example/.ssh/id_ed25519; "
        + docker_env
        + "; "
        + ssh_env
        + "; "
        + auth_header
    )
    workspace_id = "ws-crhv-004-legacy"
    _insert_ssh_cleanup_debt(
        minimal_config,
        workspace_id=workspace_id,
        last_error=raw_error,
        bytes_hint=23,
        socket_device=321,
        socket_inode=654,
    )

    debt = get_ssh_agent_cleanup_debt(open_db(minimal_config.state_db_path), workspace_id)
    assert debt is not None
    assert debt.last_error == "cleanup_failure_recorded"
    assert debt.workspace_id == workspace_id
    assert debt.bytes_hint == 23
    assert debt.socket_device == 321
    assert debt.socket_inode == 654
    assert raw_error not in json.dumps(asdict(debt), sort_keys=True)

    debts = {
        item.workspace_id: item
        for item in list_ssh_agent_cleanup_debt(open_db(minimal_config.state_db_path))
    }
    assert debts[workspace_id].last_error == "cleanup_failure_recorded"
    assert raw_error not in json.dumps(
        [asdict(item) for item in debts.values()],
        sort_keys=True,
    )
