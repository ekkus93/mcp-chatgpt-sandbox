"""Fixtures for job-store tests (S14). The `job` table has a `NOT NULL
REFERENCES workspace (id)` foreign key (`PRAGMA foreign_keys = ON`, set by
`open_db`), so every test needs a real workspace row to attach jobs to —
seeded directly with SQL rather than through `DockerWorkspaceManager`,
since these tests exercise the job store in isolation from Docker."""

import sqlite3
from collections.abc import Iterator

import pytest

from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.jobs.store import JobStore

WORKSPACE_ID = "ws_test"


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = open_db(":memory:")
    connection.execute(
        "INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')"
    )
    connection.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES (?, 'repo_test', 'persistent', '/workspace', 'running', '2026-01-01', '2026-01-01')
        """,
        (WORKSPACE_ID,),
    )
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def job_store(db: sqlite3.Connection) -> JobStore:
    return JobStore(db)
