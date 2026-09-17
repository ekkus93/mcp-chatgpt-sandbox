"""Open a state database connection with the schema applied (§68)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp_chatgpt_sandbox.db.schema import apply_migrations

#: HR-019 (`docs/HARDENING_SPEC.md` §9): more than one connection can
#: legitimately open the same `state_db_path` at once — the long-running
#: server plus an operator's `mcp-chatgpt-sandbox reset`/`rebuild`/`delete`
#: CLI invocation, `reconcile_all` at startup racing a client's first
#: request — and neither `busy_timeout` nor WAL was configured, so a
#: writer already holding the lock made the *other* connection's write
#: fail immediately with `sqlite3.OperationalError: database is locked`
#: rather than waiting the short, ordinary time a lock like that is held.
#: `busy_timeout` makes SQLite retry for up to this long before giving up;
#: WAL additionally lets readers proceed without blocking on a writer at
#: all, which the default rollback-journal mode does not.
_BUSY_TIMEOUT_MS = 5000


def open_db(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the state database at `path` and bring its
    schema up to date. Pass `":memory:"` for an ephemeral database, which
    tests use so each test gets a fresh, isolated schema — `PRAGMA
    journal_mode = WAL` is a silent no-op there (SQLite keeps `memory`
    instead, which needs no on-disk `-wal`/`-shm` sidecar files a
    `:memory:` database has nowhere to put), not an error.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    apply_migrations(connection)
    return connection
