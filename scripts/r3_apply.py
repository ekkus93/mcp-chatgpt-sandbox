from pathlib import Path

PATH = Path("src/dev_workspace_mcp/jobs/retention.py")
text = PATH.read_text(encoding="utf-8")

replacements = [
    (
        "import logging\nimport os\nimport shutil\nimport sqlite3\nimport stat\n",
        "import logging\nimport os\nimport re\nimport shutil\nimport sqlite3\nimport stat\n",
    ),
    (
        "        last_error=row[\"last_error\"],\n",
        "        last_error=_safe_persisted_cleanup_error(row[\"last_error\"]),\n",
    ),
    (
        '''def _safe_error(exc: BaseException) -> str:\n    text = f"{type(exc).__name__}: {exc}"\n    return text[:1024]\n''',
        '''_SAFE_DURABLE_CLEANUP_ERROR_RE = re.compile(\n    r"^(?:cleanup_failed|identity_unprovable): "\n    r"[A-Za-z_][A-Za-z0-9_]*(?:: errno=[0-9]+)?$"\n)\n\n\ndef _safe_error(exc: BaseException) -> str:\n    """Return only exception class plus optional errno; never raw exception text."""\n    if isinstance(exc, OSError) and exc.errno is not None:\n        return f"{type(exc).__name__}: errno={exc.errno}"\n    return type(exc).__name__\n\n\ndef _safe_persisted_cleanup_error(value: object) -> str | None:\n    """Fail closed when reading legacy free-form durable cleanup diagnostics."""\n    if value is None:\n        return None\n    if isinstance(value, str) and len(value) <= 128 and _SAFE_DURABLE_CLEANUP_ERROR_RE.fullmatch(value):\n        return value\n    return "cleanup_failure_recorded"\n''',
    ),
    (
        '''def _update_cleanup_failure(\n    connection: sqlite3.Connection,\n    debt: JobLogCleanupDebt,\n    *,\n    error: BaseException,\n    bytes_hint: int,\n) -> None:\n    """Persist failure details while preserving the pre-existing tombstone."""\n    try:\n        with connection:\n            connection.execute(\n                """\n                UPDATE job_log_cleanup\n                SET last_attempt_at = ?, last_error = ?, bytes_hint = ?\n                WHERE job_id = ?\n                """,\n                (_now(), _safe_error(error), max(bytes_hint, 0), debt.job_id),\n            )\n    except sqlite3.Error as db_exc:\n        # The tombstone was inserted before metadata deletion. If this\n        # diagnostic UPDATE itself fails, do not turn an already-completed\n        # workspace deletion into failure: the original tombstone remains\n        # durable/retryable even though its latest reason could not be saved.\n        _logger.error(\n            "job-log cleanup: failed to update cleanup debt for %s after %s: %s",\n            debt.job_id,\n            _safe_error(error),\n            db_exc,\n        )\n''',
        '''def _update_cleanup_failure(\n    connection: sqlite3.Connection,\n    debt: JobLogCleanupDebt,\n    *,\n    reason: str,\n    error: BaseException,\n    bytes_hint: int,\n) -> None:\n    """Persist only bounded classification/class/errno on the existing tombstone."""\n    diagnostic = f"{reason}: {_safe_error(error)}"[:128]\n    try:\n        with connection:\n            connection.execute(\n                """\n                UPDATE job_log_cleanup\n                SET last_attempt_at = ?, last_error = ?, bytes_hint = ?\n                WHERE job_id = ?\n                """,\n                (_now(), diagnostic, max(bytes_hint, 0), debt.job_id),\n            )\n    except sqlite3.Error as db_exc:\n        # The tombstone was inserted before metadata deletion. If this\n        # diagnostic UPDATE itself fails, do not turn an already-completed\n        # workspace deletion into failure: the original tombstone remains\n        # durable/retryable even though its latest reason could not be saved.\n        _logger.error(\n            "job-log cleanup: failed to update cleanup debt for %s after %s: %s",\n            debt.job_id,\n            diagnostic,\n            _safe_error(db_exc),\n        )\n''',
    ),
    (
        '''    except sqlite3.Error as exc:\n        # Files are already proven absent at this point. Leaving a stale\n        # tombstone is conservative: a later maintenance pass sees the\n        # directory missing and retries this DB clear. Never erase the only\n        # durable linkage before filesystem cleanup has succeeded.\n        _logger.error("job-log cleanup: failed to clear cleanup debt for %s: %s", job_id, exc)\n''',
        '''    except sqlite3.Error as exc:\n        # Files are already proven absent at this point. Leaving a stale\n        # tombstone is conservative: a later maintenance pass sees the\n        # directory missing and retries this DB clear. Never erase the only\n        # durable linkage before filesystem cleanup has succeeded.\n        _logger.error(\n            "job-log cleanup: failed to clear cleanup debt for %s: %s",\n            job_id,\n            _safe_error(exc),\n        )\n''',
    ),
    (
        '''    except ValueError as exc:\n        _update_cleanup_failure(connection, debt, error=exc, bytes_hint=debt.bytes_hint)\n        _logger.error("job-log cleanup: refusing unsafe debt %s: %s", debt.job_id, exc)\n        return False\n''',
        '''    except ValueError as exc:\n        _update_cleanup_failure(\n            connection,\n            debt,\n            reason="identity_unprovable",\n            error=exc,\n            bytes_hint=debt.bytes_hint,\n        )\n        _logger.error(\n            "job-log cleanup: identity_unprovable for job %s: %s",\n            debt.job_id,\n            _safe_error(exc),\n        )\n        return False\n''',
    ),
    (
        '''    except OSError as exc:\n        _update_cleanup_failure(connection, debt, error=exc, bytes_hint=bytes_hint)\n        _logger.warning("job-log cleanup: failed to remove %s: %s", job_dir, exc)\n        return False\n''',
        '''    except OSError as exc:\n        _update_cleanup_failure(\n            connection,\n            debt,\n            reason="cleanup_failed",\n            error=exc,\n            bytes_hint=bytes_hint,\n        )\n        _logger.warning(\n            "job-log cleanup: cleanup_failed for job %s: %s",\n            debt.job_id,\n            _safe_error(exc),\n        )\n        return False\n''',
    ),
    (
        '''        except sqlite3.Error as exc:\n            _logger.warning("prune_job_logs: failed to resolve cleanup state directory: %s", exc)\n''',
        '''        except sqlite3.Error as exc:\n            _logger.warning(\n                "prune_job_logs: failed to resolve cleanup state directory: %s",\n                _safe_error(exc),\n            )\n''',
    ),
    (
        '''        except sqlite3.Error as exc:\n            # Retention is opportunistic and must not fail the unrelated\n            # exec_start that triggered it. A DB-read failure also means the\n            # debt remains in the database; it is not converted to success.\n            _logger.warning("prune_job_logs: failed to read/retry cleanup debt: %s", exc)\n''',
        '''        except sqlite3.Error as exc:\n            # Retention is opportunistic and must not fail the unrelated\n            # exec_start that triggered it. A DB-read failure also means the\n            # debt remains in the database; it is not converted to success.\n            _logger.warning(\n                "prune_job_logs: failed to read/retry cleanup debt: %s",\n                _safe_error(exc),\n            )\n''',
    ),
    (
        '''    except sqlite3.Error as exc:\n        _logger.warning("prune_job_logs: failed to account cleanup debt: %s", exc)\n''',
        '''    except sqlite3.Error as exc:\n        _logger.warning(\n            "prune_job_logs: failed to account cleanup debt: %s",\n            _safe_error(exc),\n        )\n''',
    ),
    (
        '''            _logger.warning("prune_job_logs: failed to prune job %s: %s", job.id, exc)\n''',
        '''            _logger.warning(\n                "prune_job_logs: failed to prune job %s: %s",\n                job.id,\n                _safe_error(exc),\n            )\n''',
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match, got {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)

PATH.write_text(text, encoding="utf-8")
