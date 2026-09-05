"""S3: state database schema and migrations (§67, §68)."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from dev_workspace_mcp.db import schema
from dev_workspace_mcp.db.connection import open_db
from dev_workspace_mcp.db.schema import MIGRATIONS, apply_migrations, current_version


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_open_db_creates_all_seven_tables() -> None:
    connection = open_db(":memory:")
    assert _table_names(connection) == {
        "repository",
        "workspace",
        "job",
        "audit_event",
        "ssh_agent",
        "job_log_cleanup",
        "ssh_agent_cleanup",
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


# --- HR-019/HAT-016: migrations are atomic -----------------------------------------


def test_at_hat016_a_partially_applied_migration_leaves_no_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduced directly, without needing a real migration to actually
    fail: a substitute migration whose second statement is guaranteed to
    fail ("table already exists," the same failure mode the real defect
    hit) stands in for any mid-migration error. Before the fix: the first
    statement's table existed but `user_version` stayed at the old value,
    so a retry hit the identical "table already exists" failure forever —
    the daemon could never start again. After: neither the table nor the
    version bump survives the failure, and a subsequent
    `apply_migrations` call starts the migration cleanly, proving the
    daemon actually recovers rather than merely "not corrupting further."
    """
    db_path = tmp_path / "db.sqlite3"
    connection = open_db(db_path)
    starting_version = current_version(connection)

    broken_migration = [
        "CREATE TABLE mid_migration_marker (id INTEGER)",
        "CREATE TABLE mid_migration_marker (id INTEGER)",  # fails: already exists
    ]
    monkeypatch.setattr(schema, "MIGRATIONS", [*MIGRATIONS, broken_migration])

    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        apply_migrations(connection)

    assert current_version(connection) == starting_version  # not bumped
    marker = connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'mid_migration_marker'"
    ).fetchone()
    assert marker is None  # the first statement's effect was rolled back too

    monkeypatch.undo()
    apply_migrations(connection)  # a real daemon restart, effectively — SHALL succeed
    assert current_version(connection) == len(MIGRATIONS)


def test_at_hat016_migration_from_an_older_populated_database_preserves_data(
    tmp_path: Path,
) -> None:
    """HR-019's own second requirement: a migration test against a
    database that already has real data at an older schema version, not
    only an empty freshly-created one — the migrations added after
    version 1 (the `ssh_agent` table, `remote_protection`/image-provenance
    columns) could plausibly interact badly with pre-existing rows in
    ways `open_db()` against a fresh file never exercises."""
    db_path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # Manually bring the DB to version 1 only — repository/workspace/job/
    # audit_event, none of the columns or tables later migrations add.
    for statement in MIGRATIONS[0]:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()

    connection.execute(
        "INSERT INTO repository (id, canonical_path) VALUES ('example', '/work/example')"
    )
    connection.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_example', 'example', 'persistent', '/workspace', 'running',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    connection.commit()

    apply_migrations(connection)

    assert current_version(connection) == len(MIGRATIONS)
    repo = connection.execute("SELECT * FROM repository WHERE id = 'example'").fetchone()
    assert repo["canonical_path"] == "/work/example"
    workspace = connection.execute("SELECT * FROM workspace WHERE id = 'ws_example'").fetchone()
    assert workspace["state"] == "running"
    assert workspace["image_digest"] is None  # a later migration's new column, defaulted NULL
    # later migration tables are present and usable
    connection.execute(
        "INSERT INTO ssh_agent (workspace_id, pid, socket_path, created_at) "
        "VALUES ('ws_example', 1234, '/tmp/agent.sock', '2026-01-01T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO job_log_cleanup (job_id, workspace_id, created_at) "
        "VALUES ('job_orphan', 'ws_deleted', '2026-01-01T00:00:00Z')"
    )
    connection.commit()


def test_at_hat016_busy_timeout_and_wal_are_configured(tmp_path: Path) -> None:
    """`busy_timeout` alone does not actually distinguish this task's fix
    from its absence: Python's own `sqlite3.connect()` already defaults
    its `timeout` parameter to 5 seconds, which — verified empirically —
    already sets `PRAGMA busy_timeout` to `5000` with no code change at
    all. The explicit `PRAGMA busy_timeout = 5000` this task adds makes
    that behavior a documented, intentional choice rather than an
    incidental side effect of an unrelated default an unrelated future
    change (e.g. passing `timeout=0` explicitly somewhere) could silently
    remove — worth keeping, but not itself a behavioral fix. `journal_mode
    = wal` is: the pre-fix default (`delete`, a rollback journal) is what
    this assertion actually catches a regression in."""
    connection = open_db(tmp_path / "db.sqlite3")
    (busy_timeout,) = connection.execute("PRAGMA busy_timeout").fetchone()
    (journal_mode,) = connection.execute("PRAGMA journal_mode").fetchone()
    assert busy_timeout == 5000
    assert journal_mode == "wal"


def test_at_hat016_busy_timeout_lets_a_concurrent_writer_wait_instead_of_failing(
    tmp_path: Path,
) -> None:
    """A pragma value alone doesn't prove concurrent access actually
    tolerates contention — two real connections to the same file, one
    holding a write lock while the other attempts a write, reproduces
    the "two processes open the same file" scenario HR-019 names
    directly. Passes even against the pre-fix code too (Python's own
    default `timeout=5.0` already covers this specific write-vs-write
    case, per the note above) — kept anyway as a direct demonstration
    that the mechanism genuinely works end to end, not merely that a
    pragma echoes the expected number back."""
    db_path = tmp_path / "db.sqlite3"
    open_db(db_path).close()  # create and migrate first

    holder = sqlite3.connect(str(db_path))
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO repository (id, canonical_path) VALUES ('r1', '/p1')")

    outcomes: list[BaseException | None] = []

    def attempt_write() -> None:
        # Its own connection, created inside this thread: sqlite3
        # connections are single-thread by default, and open_db (the
        # connection under test — has busy_timeout configured) is what
        # every real caller actually uses.
        try:
            writer = open_db(db_path)
            writer.execute("INSERT INTO repository (id, canonical_path) VALUES ('r2', '/p2')")
            writer.commit()
            outcomes.append(None)
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            outcomes.append(exc)

    thread = threading.Thread(target=attempt_write)
    thread.start()
    time.sleep(0.2)  # let the writer thread reach the lock and start waiting on it
    holder.commit()  # release the lock — the waiting writer SHALL then succeed
    thread.join(timeout=5)

    assert outcomes == [None]
