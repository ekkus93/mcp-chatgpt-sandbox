"""S17: minimal audit event writing (§24)."""

import json
from pathlib import Path

import pytest

from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import AuditConfig


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_write_audit_event_appends_a_json_line(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(
        config,
        operation="workspace_stopped_idle",
        result="success",
        workspace_id="ws_example",
        repository_id="example",
        metadata={"idle_seconds": 300},
    )
    lines = _read_lines(config.path)
    assert len(lines) == 1
    event = lines[0]
    assert event["operation"] == "workspace_stopped_idle"
    assert event["result"] == "success"
    assert event["workspace_id"] == "ws_example"
    assert event["repository_id"] == "example"
    assert event["metadata"] == {"idle_seconds": 300}
    assert event["request_id"] is None
    assert event["session_id"] is None
    assert "timestamp" in event


def test_write_audit_event_appends_rather_than_overwrites(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(config, operation="op_one", result="success")
    write_audit_event(config, operation="op_two", result="success")
    lines = _read_lines(config.path)
    assert [line["operation"] for line in lines] == ["op_one", "op_two"]


def test_write_audit_event_is_a_noop_when_disabled(tmp_path: Path) -> None:
    config = AuditConfig(enabled=False, path=tmp_path / "audit.jsonl")
    write_audit_event(config, operation="workspace_stopped_idle", result="success")
    assert not config.path.exists()


def test_write_audit_event_creates_parent_directories(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "nested" / "dir" / "audit.jsonl")
    write_audit_event(config, operation="op", result="success")
    assert config.path.exists()


def test_write_audit_event_redacts_secrets_in_metadata(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(
        config,
        operation="op",
        result="success",
        metadata={"token": "super-secret-value", "safe_field": "ok"},
    )
    content = config.path.read_text()
    assert "super-secret-value" not in content
    assert "ok" in content


def test_at_gr007_write_audit_event_does_not_crash_on_a_set_or_bytes_value(
    tmp_path: Path,
) -> None:
    """GR-007 (`docs/GAP_SPEC.md`): `write_audit_event` calls
    `json.dumps` on `redact_secrets`'s own output, the same as
    `to_tool_error` — reproduced before this fix that a `set` or
    non-secret `bytes` in `metadata` raised inside this call, before the
    `except OSError` below ever got a chance to matter (a `TypeError`
    isn't an `OSError`), losing the audit event entirely rather than
    writing it."""
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(
        config,
        operation="op",
        result="success",
        metadata={"tags": {"a", "b"}, "payload": b"\x00\x01"},
    )  # SHALL NOT raise
    lines = _read_lines(config.path)
    assert len(lines) == 1
    assert isinstance(lines[0]["metadata"]["tags"], list)
    assert isinstance(lines[0]["metadata"]["payload"], str)


def test_at_g2r004_write_audit_event_does_not_leak_a_secret_flagged_frozenset(
    tmp_path: Path,
) -> None:
    """G2R-004 (`docs/GAP2_SPEC.md`): the audit-writer half, alongside
    `tests/test_errors.py`'s `test_at_g2r004_*` — `frozenset`/
    `memoryview` had no branch in `_redact` at all, so a secret-flagged
    one reached the audit log with its contents intact, not merely
    crashing the write the way `set`/`bytes` did before GR-007."""
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(
        config,
        operation="op",
        result="success",
        metadata={"secret": frozenset({"hunter2"})},
    )  # SHALL NOT raise, SHALL NOT leak
    lines = _read_lines(config.path)
    assert len(lines) == 1
    assert lines[0]["metadata"]["secret"] == "[UNREPRESENTABLE: frozenset]"


def test_write_audit_event_defaults_metadata_to_empty_dict(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(config, operation="op", result="success")
    event = _read_lines(config.path)[0]
    assert event["metadata"] == {}


def test_write_audit_event_does_not_crash_when_the_path_is_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """HR-015 (`docs/HARDENING_SPEC.md` §8): an unwritable `audit.path`
    SHALL NOT turn a would-be audit write into an unhandled crash — most
    importantly when it's `audit_denials` doing the writing from inside
    its own exception handler for a real denial, where a crash here
    would replace that denial with an unrelated, opaque `OSError`
    instead. A file sitting where a parent directory needs to be created
    is a portable way to force a real `OSError` (`FileExistsError`) out
    of `Path.mkdir`, independent of the test runner's uid/permission
    bits."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    config = AuditConfig(enabled=True, path=blocker / "audit.jsonl")

    write_audit_event(config, operation="git_push", result="denied")  # SHALL NOT raise

    assert not (blocker / "audit.jsonl").exists()
    assert "audit" in capsys.readouterr().err  # the failure is at least reported, not silent
