"""S4: the §31 error taxonomy, redaction, and the §6.1 interface-layer
mapping to MCP tool errors."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

from dev_workspace_mcp.errors import (
    ErrorCode,
    FileConflictError,
    InternalError,
    InvalidArgumentError,
    PathGitMetadataError,
    RepositoryNotAllowedError,
    WorkspaceError,
    WorkspaceNotFoundError,
    redact_secrets,
    to_tool_error,
    translate_workspace_errors,
)

# --- every §31 code has a distinct, correctly-wired typed error ------------------

_ALL_CODES = list(ErrorCode)


def test_every_spec_31_code_is_covered_by_exactly_one_error_class() -> None:
    """§31 lists 24 codes; each must map to exactly one WorkspaceError
    subclass, and every subclass must be reachable from this module."""
    subclasses = WorkspaceError.__subclasses__()
    codes = [cls.code for cls in subclasses]
    assert len(codes) == 24
    assert sorted(codes, key=str) == sorted(_ALL_CODES, key=str)
    assert len(set(codes)) == len(codes)  # no code claimed by two classes


@pytest.mark.parametrize("cls", WorkspaceError.__subclasses__())
def test_each_error_class_carries_its_code_and_a_recoverable_default(
    cls: type[WorkspaceError],
) -> None:
    exc = cls("something went wrong")
    assert exc.code in ErrorCode
    assert isinstance(exc.recoverable, bool)
    assert exc.message == "something went wrong"
    assert exc.details == {}


def test_recoverable_can_be_overridden_per_raise_site() -> None:
    exc = WorkspaceNotFoundError("ws_x not found", recoverable=False)
    assert exc.recoverable is False  # overrides the class default of True


def test_to_dict_matches_spec_31_response_shape() -> None:
    exc = FileConflictError(
        "hash mismatch",
        details={"expected_sha256": "abc", "actual_sha256": "def"},
    )
    payload = exc.to_dict()
    assert payload == {
        "code": "FILE_CONFLICT",
        "message": "hash mismatch",
        "recoverable": True,
        "details": {"expected_sha256": "abc", "actual_sha256": "def"},
    }


# --- redaction ("Secrets SHALL not be included", §31 / §24.1) --------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token", "anything-at-all"),
        ("api_token", "anything-at-all"),
        ("password", "anything-at-all"),
        ("secret", "anything-at-all"),
        ("ssh_key", "anything-at-all"),
        ("credential", "anything-at-all"),
        ("Authorization", "anything-at-all"),
    ],
)
def test_redact_secrets_redacts_by_key_name(key: str, value: str) -> None:
    assert redact_secrets({key: value}) == {key: "[REDACTED]"}


@pytest.mark.parametrize(
    "value",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 30,
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI",
    ],
)
def test_redact_secrets_redacts_secret_shaped_values_regardless_of_key(value: str) -> None:
    assert redact_secrets({"unrelated_field_name": value}) == {"unrelated_field_name": "[REDACTED]"}


def test_redact_secrets_preserves_ordinary_values() -> None:
    details = {"path": "src/lib.rs", "line": 42, "nested": {"branch": "master"}}
    assert redact_secrets(details) == details


def test_redact_secrets_recurses_into_lists_and_nested_dicts() -> None:
    details = {
        "items": [{"token": "x"}, {"path": "ok"}],
        "outer": {"inner": {"password": "y"}},
    }
    redacted = redact_secrets(details)
    assert redacted["items"][0]["token"] == "[REDACTED]"
    assert redacted["items"][1]["path"] == "ok"
    assert redacted["outer"]["inner"]["password"] == "[REDACTED]"


# --- §6.1 mapping to the SDK's ToolError -----------------------------------------


def test_to_tool_error_returns_sdk_tool_error() -> None:
    exc = InvalidArgumentError("bad path")
    tool_error = to_tool_error(exc)
    assert isinstance(tool_error, MCPToolError)


def test_to_tool_error_message_is_parseable_json_with_spec_31_fields() -> None:
    exc = RepositoryNotAllowedError("repository not authorized", details={"id": "evil-repo"})
    tool_error = to_tool_error(exc)
    payload = json.loads(str(tool_error))
    assert payload["code"] == "REPOSITORY_NOT_ALLOWED"
    assert payload["message"] == "repository not authorized"
    assert payload["recoverable"] is False
    assert payload["details"] == {"id": "evil-repo"}


def test_to_tool_error_redacts_details() -> None:
    exc = PathGitMetadataError(
        "cannot write .git", details={"path": ".git/config", "token": "should-not-leak"}
    )
    payload = json.loads(str(to_tool_error(exc)))
    assert payload["details"]["token"] == "[REDACTED]"
    assert payload["details"]["path"] == ".git/config"


# --- @translate_workspace_errors ---------------------------------------------------


async def test_decorator_translates_workspace_error_on_async_function() -> None:
    @translate_workspace_errors
    async def tool_fn() -> None:
        raise WorkspaceNotFoundError("ws_missing not found")

    with pytest.raises(MCPToolError) as excinfo:
        await tool_fn()
    payload = json.loads(str(excinfo.value))
    assert payload["code"] == "WORKSPACE_NOT_FOUND"


def test_decorator_translates_workspace_error_on_sync_function() -> None:
    @translate_workspace_errors
    def tool_fn() -> None:
        raise InternalError("boom")

    with pytest.raises(MCPToolError) as excinfo:
        tool_fn()
    payload = json.loads(str(excinfo.value))
    assert payload["code"] == "INTERNAL_ERROR"


async def test_decorator_lets_unexpected_exceptions_propagate_unchanged() -> None:
    """An error the domain did not anticipate must look like a crash to the
    SDK, not be laundered into a clean §31 result (§6.1)."""

    @translate_workspace_errors
    async def tool_fn() -> None:
        raise ValueError("this is a bug, not a domain error")

    with pytest.raises(ValueError, match="this is a bug"):
        await tool_fn()


async def test_decorator_passes_through_successful_calls() -> None:
    @translate_workspace_errors
    async def tool_fn(x: int, y: int) -> int:
        return x + y

    assert await tool_fn(2, 3) == 5


def test_decorator_preserves_function_metadata() -> None:
    @translate_workspace_errors
    def named_tool() -> None:
        """A docstring."""

    assert named_tool.__name__ == "named_tool"
    assert named_tool.__doc__ == "A docstring."
