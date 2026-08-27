"""S3: state database schema and migrations (§67, §68)."""

import sqlite3
from pathlib import Path

import pytest

from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.db.schema import MIGRATIONS, apply_migrations, current_version


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_open_db_creates_all_five_tables() -> None:
    connection = open_db(":memory:")
    assert _table_names(connection) == {
        "repository",
        "workspace",
        "job",
        "audit_event",
        "ssh_agent",
    }


def test_open_db_reaches_current_schema_version() -> None:
    connection = open_db(":memory:")
    assert current_version(connection) == len(MIGRATIONS)


def test_apply_migrations_is_idempotent() -> None:
    connection = open_db(":memory:")
    version_after_first_open = current_version(connection)
    apply_migrations(connection)  # must not error or re-run already-applied migrations
    assert current_version(connection) == version_after_first_open


def test_open_db_creates_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "state" / "db.sqlite3"
    connection = open_db(db_path)
    assert db_path.exists()
    assert current_version(connection) == len(MIGRATIONS)


def test_reopening_an_existing_database_preserves_data(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    first = open_db(db_path)
    first.execute(
        "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
        ("example", "/home/phil/work/example"),
    )
    first.commit()
    first.close()

    second = open_db(db_path)
    row = second.execute("SELECT * FROM repository WHERE id = 'example'").fetchone()
    assert row["canonical_path"] == "/home/phil/work/example"
    assert current_version(second) == len(MIGRATIONS)


def test_foreign_keys_are_enforced() -> None:
    connection = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES ('ws_x', 'does-not-exist', 'persistent', '/workspace', 'running', '', '')
            """
        )


def test_workspace_state_check_constraint_rejects_unknown_state() -> None:
    connection = open_db(":memory:")
    connection.execute("INSERT INTO repository (id, canonical_path) VALUES ('r', '/p')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES ('ws_x', 'r', 'persistent', '/workspace', 'not-a-real-state', '', '')
            """
        )


def test_workspace_mode_check_constraint_rejects_unknown_mode() -> None:
    connection = open_db(":memory:")
    connection.execute("INSERT INTO repository (id, canonical_path) VALUES ('r', '/p')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO workspace
                (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
            VALUES ('ws_x', 'r', 'not-a-real-mode', '/workspace', 'running', '', '')
            """
        )


def test_job_state_check_constraint_rejects_unknown_state() -> None:
    connection = open_db(":memory:")
    connection.execute("INSERT INTO repository (id, canonical_path) VALUES ('r', '/p')")
    connection.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_x', 'r', 'persistent', '/workspace', 'running', '', '')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO job (id, workspace_id, command, cwd, state) "
            "VALUES ('job_1', 'ws_x', 'echo hi', '/workspace', 'not-a-real-state')"
        )


def test_full_row_round_trip_across_all_four_tables() -> None:
    """Proves the schema shape is actually usable end to end, not just
    that CREATE TABLE succeeded."""
    connection = open_db(":memory:")

    connection.execute(
        """
        INSERT INTO repository (id, canonical_path, configured_remote, default_branch)
        VALUES ('example', '/home/phil/work/example', 'owner/example', 'master')
        """
    )
    connection.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, container_id, container_name, image,
             workspace_path, state, created_at, last_used_at)
        VALUES ('ws_example', 'example', 'persistent', 'c0ffee', 'mcp-dev-example',
                'mcp-dev-ubuntu:24.04', '/workspace', 'running',
                '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
        """
    )
    connection.execute(
        """
        INSERT INTO job (id, workspace_id, command, cwd, state, exit_code,
                          stdout_total_bytes, output_truncated)
        VALUES ('job_1', 'ws_example', 'cargo test', '/workspace', 'succeeded', 0, 128, 0)
        """
    )
    connection.execute(
        """
        INSERT INTO audit_event
            (timestamp, workspace_id, repository_id, operation, result, metadata)
        VALUES ('2026-08-26T00:00:00Z', 'ws_example', 'example', 'git_push', 'success', '{}')
        """
    )
    connection.commit()

    repo = connection.execute("SELECT * FROM repository WHERE id = 'example'").fetchone()
    assert repo["default_branch"] == "master"

    workspace = connection.execute("SELECT * FROM workspace WHERE id = 'ws_example'").fetchone()
    assert workspace["state"] == "running"
    assert workspace["repository_id"] == "example"

    job = connection.execute("SELECT * FROM job WHERE id = 'job_1'").fetchone()
    assert job["exit_code"] == 0
    assert job["output_truncated"] == 0

    audit = connection.execute("SELECT * FROM audit_event WHERE operation = 'git_push'").fetchone()
    assert audit["result"] == "success"
    assert audit["id"] is not None  # autoincrement primary key assigned
