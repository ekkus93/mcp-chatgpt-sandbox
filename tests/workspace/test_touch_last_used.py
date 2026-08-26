"""S17: `touch_last_used` (§8.1) — pure SQL, no Docker needed."""

import sqlite3

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.workspace.manager import touch_last_used


def _seeded_db() -> sqlite3.Connection:
    db = open_db(":memory:")
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_test', 'repo_test', 'persistent', '/workspace', 'running',
                '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
        """
    )
    db.commit()
    return db


def test_touch_last_used_advances_the_timestamp() -> None:
    db = _seeded_db()
    touch_last_used(db, "ws_test")
    (last_used_at,) = db.execute(
        "SELECT last_used_at FROM workspace WHERE id = 'ws_test'"
    ).fetchone()
    assert last_used_at != "2000-01-01T00:00:00+00:00"


def test_touch_last_used_on_unknown_workspace_is_a_silent_noop() -> None:
    db = _seeded_db()
    touch_last_used(db, "ws_does_not_exist")  # must not raise
    (last_used_at,) = db.execute(
        "SELECT last_used_at FROM workspace WHERE id = 'ws_test'"
    ).fetchone()
    assert last_used_at == "2000-01-01T00:00:00+00:00"  # untouched
