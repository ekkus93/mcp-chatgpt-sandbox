"""S17: minimal audit event writing (§24)."""

import json
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.audit import write_audit_event
from mcp_chatgpt_sandbox.config.models import AuditConfig


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _token_tail() -> str:
    return ("AbC" + "123" + "._-") * 5


def _mixed_high_entropy() -> str:
    return "Ab3C" + "d4Ef" + "5Gh6" + "Ij7K" + "l8Mn" + "9Op0" + "Qr1S" + "t2Uv"


def _github_token_shape() -> str:
    return "gh" + "p_" + "A" * 36


def _openai_key_shape() -> str:
    return "sk" + "-proj-" + _token_tail()


def _aws_access_key_id_shape() -> str:
    return "AK" + "IA" + "A1B2C3D4E5F6G7H8"


def _bearer_token_shape() -> str:
    return "Bear" + "er " + _token_tail()


def _assignment_token_shape() -> str:
    return "api_" + "key=" + _token_tail()


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


def test_write_audit_event_accepts_only_safe_correlation_ids(tmp_path: Path) -> None:
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    write_audit_event(
        config,
        operation="safe",
        result="success",
        request_id="rq_" + "a" * 32,
        session_id="session:alpha-1",
    )
    write_audit_event(
        config,
        operation="unsafe",
        result="success",
        request_id="/home/phil/.ssh/id_rsa",
        session_id="Authorization: " + "Bearer synthetic-crhv-002-token",
    )

    safe, unsafe = _read_lines(config.path)
    assert safe["request_id"] == "rq_" + "a" * 32
    assert safe["session_id"] == "session:alpha-1"
    assert unsafe["request_id"] is None
    assert unsafe["session_id"] is None


def test_crhv_002_write_audit_event_preserves_safe_top_level_ids(
    tmp_path: Path,
) -> None:
    """CRHV-002: safe top-level audit IDs remain useful."""
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")

    write_audit_event(
        config,
        operation="safe_top_level_ids",
        result="success",
        workspace_id="ws_safe-1",
        repository_id="repo.example:1",
    )

    event = _read_lines(config.path)[0]
    assert event["workspace_id"] == "ws_safe-1"
    assert event["repository_id"] == "repo.example:1"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("workspace_id", "/home/example/.ssh/id_rsa"),
        ("repository_id", "/srv/private/repository"),
        ("workspace_id", "Authorization: " + "Bearer synthetic-crhv-002-token"),
        ("repository_id", "AWS" + "_SECRET_ACCESS_KEY=synthetic-crhv-002"),
        ("workspace_id", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"),
        ("repository_id", "safe-id\nrm -rf /"),
    ],
)
def test_crhv_002_write_audit_event_drops_unsafe_top_level_ids(
    tmp_path: Path,
    field: str,
    unsafe_value: str,
) -> None:
    """CRHV-002: raw top-level audit IDs fail closed before persistence."""
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    kwargs = {
        "workspace_id": "ws_safe-1",
        "repository_id": "repo_safe-1",
        field: unsafe_value,
    }

    write_audit_event(
        config,
        operation="unsafe_top_level_ids",
        result="success",
        **kwargs,
    )

    content = config.path.read_text()
    event = _read_lines(config.path)[0]
    assert event[field] is None
    assert unsafe_value not in content


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("workspace_id", _github_token_shape()),
        ("repository_id", _github_token_shape()),
        ("workspace_id", _openai_key_shape()),
        ("repository_id", _openai_key_shape()),
        ("workspace_id", _aws_access_key_id_shape()),
        ("repository_id", _aws_access_key_id_shape()),
        ("workspace_id", _bearer_token_shape()),
        ("repository_id", _bearer_token_shape()),
        ("workspace_id", _assignment_token_shape()),
        ("repository_id", _assignment_token_shape()),
        ("workspace_id", _mixed_high_entropy()),
        ("repository_id", _mixed_high_entropy()),
        ("request_id", _github_token_shape()),
        ("session_id", _openai_key_shape()),
    ],
)
def test_pcrfv_001_write_audit_event_drops_pure_token_shaped_ids(
    tmp_path: Path,
    field: str,
    unsafe_value: str,
) -> None:
    """PCRFV-001: token-shaped IDs fail closed even when syntax-valid.

    Values are assembled at runtime so committed source does not contain
    literal real-looking credentials, while the audit boundary still receives
    complete credential-shaped strings.
    """
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")
    kwargs = {
        "request_id": "rq_" + "a" * 32,
        "session_id": "session:alpha-1",
        "workspace_id": "ws_safe-1",
        "repository_id": "repo_safe-1",
        field: unsafe_value,
    }

    write_audit_event(
        config,
        operation="unsafe_pure_token_ids",
        result="success",
        **kwargs,
    )

    content = config.path.read_text()
    event = _read_lines(config.path)[0]
    assert event[field] is None
    assert unsafe_value not in content


def test_pcrfv_001_write_audit_event_preserves_safe_internal_ids(
    tmp_path: Path,
) -> None:
    """PCRFV-001: stricter token-shape rejection keeps safe opaque IDs."""
    config = AuditConfig(enabled=True, path=tmp_path / "audit.jsonl")

    write_audit_event(
        config,
        operation="safe_internal_ids",
        result="success",
        request_id="rq_" + "a" * 32,
        session_id="session:alpha-1",
        workspace_id="ws_safe-1",
        repository_id="repo.example:1",
        metadata={
            "prune_plan_id": "pp_" + "b" * 32,
            "prune_candidate_id": "pc_" + "c" * 32,
        },
    )

    event = _read_lines(config.path)[0]
    assert event["request_id"] == "rq_" + "a" * 32
    assert event["session_id"] == "session:alpha-1"
    assert event["workspace_id"] == "ws_safe-1"
    assert event["repository_id"] == "repo.example:1"
    assert event["metadata"] == {
        "prune_plan_id": "pp_" + "b" * 32,
        "prune_candidate_id": "pc_" + "c" * 32,
    }


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
