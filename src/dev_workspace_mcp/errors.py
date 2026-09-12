"""Typed workspace errors and secret-safe MCP error/audit redaction.

Only expected domain failures are converted into structured MCP tool errors.
Unexpected exceptions deliberately propagate to the MCP SDK's crash handling so
bugs stay loud in logs and CI instead of being disguised as ordinary user or
policy failures.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import Any, ClassVar

from docker.errors import APIError
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    WORKSPACE_NOT_FOUND = "WORKSPACE_NOT_FOUND"
    WORKSPACE_NOT_RUNNING = "WORKSPACE_NOT_RUNNING"
    REPOSITORY_NOT_ALLOWED = "REPOSITORY_NOT_ALLOWED"
    PATH_OUTSIDE_REPOSITORY = "PATH_OUTSIDE_REPOSITORY"
    FILE_CONFLICT = "FILE_CONFLICT"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    GIT_DIRTY = "GIT_DIRTY"
    GIT_CONFLICT = "GIT_CONFLICT"
    GIT_HEAD_CHANGED = "GIT_HEAD_CHANGED"
    PATH_GIT_METADATA = "PATH_GIT_METADATA"
    GIT_REMOTE_NOT_ALLOWED = "GIT_REMOTE_NOT_ALLOWED"
    GIT_REFSPEC_NOT_ALLOWED = "GIT_REFSPEC_NOT_ALLOWED"
    GIT_AUTH_FAILED = "GIT_AUTH_FAILED"
    REMOTE_PROTECTION_MISSING = "REMOTE_PROTECTION_MISSING"
    SSH_KEY_PROVISION_FAILED = "SSH_KEY_PROVISION_FAILED"
    SSH_CREDENTIAL_CLEANUP_FAILED = "SSH_CREDENTIAL_CLEANUP_FAILED"
    GIT_NON_FAST_FORWARD = "GIT_NON_FAST_FORWARD"
    GIT_NOTHING_TO_COMMIT = "GIT_NOTHING_TO_COMMIT"
    POLICY_DENIED = "POLICY_DENIED"
    CONTAINER_CREATE_FAILED = "CONTAINER_CREATE_FAILED"
    CONTAINER_RUNTIME_FAILED = "CONTAINER_RUNTIME_FAILED"
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
    NETWORK_FAILED = "NETWORK_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class WorkspaceError(Exception):
    """Base class for every structured workspace-domain error.

    `recoverable` says whether the same request might plausibly succeed later
    without changing its meaning. Each subclass owns its default so callers do
    not have to infer retry semantics from a free-form message.
    """

    code: ClassVar[ErrorCode]
    default_recoverable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        recoverable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recoverable = self.default_recoverable if recoverable is None else recoverable
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Return the stable client-facing error payload before redaction."""
        return {
            "code": self.code.value,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": self.details,
        }


class InvalidArgumentError(WorkspaceError):
    code = ErrorCode.INVALID_ARGUMENT
    default_recoverable = True


class WorkspaceNotFoundError(WorkspaceError):
    code = ErrorCode.WORKSPACE_NOT_FOUND
    default_recoverable = True


class WorkspaceNotRunningError(WorkspaceError):
    code = ErrorCode.WORKSPACE_NOT_RUNNING
    default_recoverable = True


class RepositoryNotAllowedError(WorkspaceError):
    code = ErrorCode.REPOSITORY_NOT_ALLOWED
    default_recoverable = False


class PathOutsideRepositoryError(WorkspaceError):
    code = ErrorCode.PATH_OUTSIDE_REPOSITORY
    default_recoverable = True


class FileConflictError(WorkspaceError):
    code = ErrorCode.FILE_CONFLICT
    default_recoverable = True


class JobNotFoundError(WorkspaceError):
    code = ErrorCode.JOB_NOT_FOUND
    default_recoverable = False


class CommandFailedError(WorkspaceError):
    code = ErrorCode.COMMAND_FAILED
    default_recoverable = False


class CommandTimeoutError(WorkspaceError):
    code = ErrorCode.COMMAND_TIMEOUT
    default_recoverable = True


class GitDirtyError(WorkspaceError):
    code = ErrorCode.GIT_DIRTY
    default_recoverable = True


class GitConflictError(WorkspaceError):
    code = ErrorCode.GIT_CONFLICT
    default_recoverable = True


class GitHeadChangedError(WorkspaceError):
    code = ErrorCode.GIT_HEAD_CHANGED
    default_recoverable = True


class PathGitMetadataError(WorkspaceError):
    code = ErrorCode.PATH_GIT_METADATA
    default_recoverable = False


class GitRemoteNotAllowedError(WorkspaceError):
    code = ErrorCode.GIT_REMOTE_NOT_ALLOWED
    default_recoverable = False


class GitRefspecNotAllowedError(WorkspaceError):
    code = ErrorCode.GIT_REFSPEC_NOT_ALLOWED
    default_recoverable = True


class GitAuthFailedError(WorkspaceError):
    code = ErrorCode.GIT_AUTH_FAILED
    default_recoverable = False


class RemoteProtectionMissingError(WorkspaceError):
    code = ErrorCode.REMOTE_PROTECTION_MISSING
    default_recoverable = False


class SSHKeyProvisionFailedError(WorkspaceError):
    code = ErrorCode.SSH_KEY_PROVISION_FAILED
    default_recoverable = False


class SSHCredentialCleanupFailedError(WorkspaceError):
    code = ErrorCode.SSH_CREDENTIAL_CLEANUP_FAILED
    default_recoverable = False


class GitNonFastForwardError(WorkspaceError):
    code = ErrorCode.GIT_NON_FAST_FORWARD
    default_recoverable = True


class GitNothingToCommitError(WorkspaceError):
    code = ErrorCode.GIT_NOTHING_TO_COMMIT
    default_recoverable = False


class PolicyDeniedError(WorkspaceError):
    code = ErrorCode.POLICY_DENIED
    default_recoverable = False


class ContainerCreateFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_CREATE_FAILED
    default_recoverable = False


class ContainerRuntimeFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_RUNTIME_FAILED
    default_recoverable = True


class ImageNotFoundError(WorkspaceError):
    code = ErrorCode.IMAGE_NOT_FOUND
    default_recoverable = False


class NetworkFailedError(WorkspaceError):
    code = ErrorCode.NETWORK_FAILED
    default_recoverable = True


class InternalError(WorkspaceError):
    code = ErrorCode.INTERNAL_ERROR
    default_recoverable = False


# Redaction is defense-in-depth for `WorkspaceError.details`, error messages,
# and audit metadata. More sensitive paths, such as prune execution failures,
# still use explicit allowlisted classifications rather than depending only on
# regex replacement.
_SECRET_KEY_HINTS = (
    "token",
    "password",
    "secret",
    "key",
    "credential",
    "authorization",
    "passphrase",
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9._-]{19,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"
    ),
    re.compile(r"-----BEGIN [A-Z ]*" r"PRIVATE KEY-----"),
    re.compile(r"ssh-(?:ed25519|rsa|ecdsa)[A-Za-z0-9+/= ]{20,}"),
)

_HIGH_ENTROPY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{32,128}\b")
_PUBLIC_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_PUBLIC_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_PUBLIC_SSH_FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/=]{32,128}")
_SAFE_OPAQUE_ID_PREFIXES = ("rq_", "pp_", "pc_")
_URL_USERINFO_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+@")


def _looks_like_high_entropy_token(value: str) -> bool:
    """Detect likely bearer/API tokens while preserving common public IDs.

    Long mixed-class opaque strings are suspicious in diagnostics, but several
    public identifiers have the same length profile. Commit SHAs, UUIDs,
    internal request/prune IDs, and public SSH fingerprints are explicitly
    preserved so redaction does not destroy useful non-secret evidence.
    """
    if _PUBLIC_SSH_FINGERPRINT_RE.fullmatch(value):
        return False
    for match in _HIGH_ENTROPY_TOKEN_RE.finditer(value):
        token = match.group(0)
        if _PUBLIC_COMMIT_SHA_RE.fullmatch(token):
            continue
        if _PUBLIC_UUID_RE.fullmatch(token):
            continue
        if token.startswith(_SAFE_OPAQUE_ID_PREFIXES):
            continue
        character_classes = sum(
            (
                any(char.islower() for char in token),
                any(char.isupper() for char in token),
                any(char.isdigit() for char in token),
                any(char in "_-" for char in token),
            )
        )
        if character_classes >= 3:
            return True
    return False


def looks_like_secret_value(value: str) -> bool:
    """Return whether a string has a credential/token-like shape.

    This predicate intentionally looks only at value shape, not the containing
    key. It is shared by generic redaction and stricter audit identifier
    validation so a token-shaped top-level ID cannot bypass audit sanitization
    merely because it uses the allowed opaque-ID alphabet.
    """
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS) or (
        _looks_like_high_entropy_token(value)
    )


def _looks_like_secret(value: str) -> bool:
    """Backward-compatible private alias for the generic secret-shape check."""
    return looks_like_secret_value(value)


def _redact(value: Any, *, key: str | None = None, inherited_secret: bool = False) -> Any:
    """Recursively redact a JSON-like diagnostic structure.

    Secret-key hints propagate into nested dictionaries and list/tuple/set items
    because `{"secret": {"value": ...}}` is just as sensitive as a direct
    `{"secret": ...}` leaf. Unknown or non-JSON-native values fail closed to a
    bounded placeholder so `json.dumps` call sites do not crash or leak a raw
    representation.
    """
    key_flags_secret = inherited_secret or (
        isinstance(key, str) and any(hint in key.lower() for hint in _SECRET_KEY_HINTS)
    )
    if isinstance(value, dict):
        return {k: _redact(v, key=k, inherited_secret=key_flags_secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key=key, inherited_secret=key_flags_secret) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact(v, key=key, inherited_secret=key_flags_secret) for v in value)
    if isinstance(value, set):
        return [_redact(v, key=key, inherited_secret=key_flags_secret) for v in value]
    if isinstance(value, str):
        if key_flags_secret or looks_like_secret_value(value):
            return "[REDACTED]"
        return _URL_USERINFO_RE.sub(r"\1[REDACTED]@", value)
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", errors="replace")
        if key_flags_secret or looks_like_secret_value(text):
            return "[REDACTED]"
        return text
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return f"[UNREPRESENTABLE: {type(value).__name__}]"


def redact_secrets(details: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe, secret-redacted copy of structured diagnostics."""
    return dict(_redact(details))


def to_tool_error(exc: WorkspaceError) -> MCPToolError:
    """Translate a domain error into the MCP SDK tool-error boundary.

    Docker storage-pressure enrichment is applied before the final payload is
    redacted. Non-`WorkspaceError` exceptions are intentionally not caught here.
    """
    from dev_workspace_mcp.operations.pressure import (
        classify_docker_storage_failure,
        enrich_workspace_error,
    )

    cause = exc.__cause__
    if isinstance(cause, APIError):
        pressure = classify_docker_storage_failure(cause)
        if pressure is not None:
            enrich_workspace_error(exc, pressure)

    payload = exc.to_dict()
    payload["details"] = redact_secrets(payload["details"])
    redacted_message = _redact(payload["message"])
    payload["message"] = redacted_message if isinstance(redacted_message, str) else "[REDACTED]"
    return MCPToolError(json.dumps(payload, sort_keys=True))


def translate_workspace_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate MCP tool functions with structured domain-error translation."""
    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except WorkspaceError as exc:
                raise to_tool_error(exc) from exc

        return async_wrapper

    @wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except WorkspaceError as exc:
            raise to_tool_error(exc) from exc

    return sync_wrapper
