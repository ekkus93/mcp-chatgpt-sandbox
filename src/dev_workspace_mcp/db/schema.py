"""Schema migrations for the state database (§67, §68).

Tables cover `Repository`, `Workspace`, `Job`, and `AuditEvent` — the four
§67 entities the MVP needs. `WorkspaceLock` is deferred to S31 with locking
itself.

Migrations are tracked with `PRAGMA user_version` rather than a bookkeeping
table: SQLite provides exactly this counter for exactly this purpose, and a
four-table schema does not need more machinery than that.

§69: `container_id` and `container_name` on `workspace` are a cache, not
ground truth. Docker labels remain authoritative for reconciling a
workspace's container after a divergence (§6.2, §44, §69); the reconciler
that enforces that — recovering, quarantining, or marking a workspace
absent — is built where it is used (S6, S18), not here. This schema only
avoids getting in that reconciler's way: nothing here treats a cached
`container_id` as a foreign key SQLite itself would enforce, so a stale
value never blocks recovery.
"""

from __future__ import annotations

import sqlite3

# Each entry is the full set of statements that take the schema from
# version (index) to version (index + 1). Statements run inside one
# transaction per migration (`connection.py` handles that), so a migration
# either fully applies or not at all.
MIGRATIONS: list[list[str]] = [
    # --- version 0 -> 1: initial schema -----------------------------------
    [
        """
        CREATE TABLE repository (
            id               TEXT PRIMARY KEY,
            canonical_path   TEXT NOT NULL,
            configured_remote TEXT,
            default_branch   TEXT,
            policy_id        TEXT
        )
        """,
        """
        CREATE TABLE workspace (
            id               TEXT PRIMARY KEY,
            repository_id    TEXT NOT NULL REFERENCES repository (id),
            mode             TEXT NOT NULL CHECK (mode IN ('persistent', 'ephemeral')),
            container_id     TEXT,
            container_name   TEXT,
            image            TEXT,
            workspace_path   TEXT NOT NULL,
            state            TEXT NOT NULL CHECK (state IN (
                                 'absent', 'creating', 'stopped', 'starting',
                                 'running', 'stopping', 'rebuilding', 'failed',
                                 'deleting'
                             )),
            created_at       TEXT NOT NULL,
            last_used_at     TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_workspace_repository_id ON workspace (repository_id)",
        """
        CREATE TABLE job (
            id                   TEXT PRIMARY KEY,
            workspace_id         TEXT NOT NULL REFERENCES workspace (id),
            command              TEXT NOT NULL,
            cwd                  TEXT NOT NULL,
            state                TEXT NOT NULL CHECK (state IN (
                                     'queued', 'starting', 'running', 'succeeded',
                                     'failed', 'canceled', 'timed_out', 'lost'
                                 )),
            pid                  INTEGER,
            started_at           TEXT,
            finished_at          TEXT,
            exit_code            INTEGER,
            stdout_log_path      TEXT,
            stderr_log_path      TEXT,
            stdout_total_bytes   INTEGER NOT NULL DEFAULT 0,
            stderr_total_bytes   INTEGER NOT NULL DEFAULT 0,
            output_truncated     INTEGER NOT NULL DEFAULT 0 CHECK (output_truncated IN (0, 1)),
            logs_pruned          INTEGER NOT NULL DEFAULT 0 CHECK (logs_pruned IN (0, 1))
        )
        """,
        "CREATE INDEX idx_job_workspace_id ON job (workspace_id)",
        "CREATE INDEX idx_job_state ON job (state)",
        """
        CREATE TABLE audit_event (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            request_id    TEXT,
            session_id    TEXT,
            workspace_id  TEXT,
            repository_id TEXT,
            operation     TEXT NOT NULL,
            result        TEXT NOT NULL,
            metadata      TEXT
        )
        """,
        "CREATE INDEX idx_audit_event_timestamp ON audit_event (timestamp)",
        "CREATE INDEX idx_audit_event_workspace_id ON audit_event (workspace_id)",
    ],
    # --- version 1 -> 2: per-workspace ssh-agent tracking (§19.2, §44.1, S23) ---
    [
        # Deliberately no `REFERENCES workspace (id)`: `workspace_open`
        # (§19.2) must start the real agent process — and persist a
        # record of it, so a crash between the two doesn't leak an
        # untracked process — *before* `ensure_workspace` creates the
        # `workspace` row a brand-new persistent workspace's mounts
        # depend on. A `workspace_id` here can therefore transiently
        # exist with no matching `workspace` row yet.
        """
        CREATE TABLE ssh_agent (
            workspace_id  TEXT PRIMARY KEY,
            pid           INTEGER NOT NULL,
            socket_path   TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
        """,
    ],
    # --- version 2 -> 3: cached remote-protection result (§18.5, §20.4, S28) ---
    [
        # A JSON blob, not individual columns: `workspace_info` (a read
        # tool, §25) reports the *last* `workspace_open` verification
        # rather than re-querying GitHub on every read (§20.4 only
        # requires verifying on `workspace_open`, and optionally again
        # before a push) — this is that cache, one row per repository.
        "ALTER TABLE repository ADD COLUMN remote_protection TEXT",
    ],
    # --- version 3 -> 4: image provenance (§9.2, S30) ---------------------------
    [
        # `image` and `created_at` already exist on `workspace`; only the
        # digest and the project config's own `version` are new — the
        # reproducibility record §9.2 asks for.
        "ALTER TABLE workspace ADD COLUMN image_digest TEXT",
        "ALTER TABLE workspace ADD COLUMN config_version INTEGER",
    ],
]


def current_version(connection: sqlite3.Connection) -> int:
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    return int(version)


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Bring `connection`'s schema up to `len(MIGRATIONS)`, applying only
    the migrations it is missing. Idempotent: calling this on an
    already-current database is a no-op.

    HR-019 (`docs/HARDENING_SPEC.md` §9): `with connection:` looks like it
    wraps each migration in a transaction, but Python's `sqlite3` module
    only issues an *implicit* `BEGIN` ahead of the first DML statement
    (`INSERT`/`UPDATE`/`DELETE`/`REPLACE`) under the default legacy
    transaction control — DDL (`CREATE TABLE`, `ALTER TABLE`) and `PRAGMA
    user_version` never trigger it, so each statement below used to commit
    on its own as soon as it ran. Reproduced: a migration whose second
    statement fails leaves the first statement's table created but
    `user_version` unchanged, so the next start re-applies the same
    migration and fails again on "table already exists" — permanently,
    not just once. An explicit `BEGIN`/`COMMIT` SQLite itself treats as a
    single atomic unit even across DDL fixes this: every statement in a
    migration, including the `user_version` bump, commits together or not
    at all."""
    version = current_version(connection)
    for index in range(version, len(MIGRATIONS)):
        connection.execute("BEGIN")
        try:
            for statement in MIGRATIONS[index]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {index + 1}")
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
