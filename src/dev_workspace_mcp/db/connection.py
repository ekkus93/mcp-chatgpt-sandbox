"""Open a state database connection with the schema applied (§68)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dev_workspace_mcp.db.schema import apply_migrations


def open_db(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the state database at `path` and bring its
    schema up to date. Pass `":memory:"` for an ephemeral database, which
    tests use so each test gets a fresh, isolated schema.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection)
    return connection
