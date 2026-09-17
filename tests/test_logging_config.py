"""H31 (`docs/HARDENING_TODO.md`, HR-031): operational logging bootstrap."""

import logging

from mcp_chatgpt_sandbox.logging_config import configure_logging, redacted_extra


def test_configure_logging_sets_the_root_level(monkeypatch) -> None:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    try:
        configure_logging("WARNING")
        assert root.level == logging.WARNING
        configure_logging("DEBUG")
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(original_level)
        root.handlers = original_handlers


def test_redacted_extra_redacts_a_secret_shaped_value() -> None:
    """HR-031: "secrets SHALL pass through the same redaction as
    HR-013" — a thin wrapper over `errors.redact_secrets`, confirmed to
    actually delegate rather than just look like it does."""
    secret = "ghp_" + "a" * 36
    result = redacted_extra({"token": "hunter2", "remote": secret})
    assert result["token"] == "[REDACTED]"
    assert result["remote"] == "[REDACTED]"


def test_redacted_extra_preserves_ordinary_values() -> None:
    result = redacted_extra({"branch": "main", "policy": "force_push"})
    assert result == {"branch": "main", "policy": "force_push"}
