"""The §31 error taxonomy and its mapping to MCP tool errors (§6.1).

Every domain error in the system is one of the 24 typed exceptions below,
each carrying the fields §31 requires: a stable code, a concise message, a
recoverable flag, and structured details. `to_tool_error()` and the
`@translate_workspace_errors` decorator are the "map internal errors to
MCP errors at the interface layer" from §6.1: they convert a
`WorkspaceError` into the SDK's `ToolError` (§31), with a §31-shaped JSON
payload for the client to read, and redaction applied to `details` as
defense in depth.

Anything that is *not* a `WorkspaceError` is left to propagate. The MCP SDK
treats an unanticipated exception as a crash — traceback logged
server-side, a generic message to the client — and that is the correct
behavior for a bug the domain did not anticipate. Coercing it into a clean
§31 error would hide the difference between "the client did something the
system rejects" and "the system broke."
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import Any, ClassVar

from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

# --- §31 codes -------------------------------------------------------------


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
    GIT_NON_FAST_FORWARD = "GIT_NON_FAST_FORWARD"
    POLICY_DENIED = "POLICY_DENIED"
    CONTAINER_CREATE_FAILED = "CONTAINER_CREATE_FAILED"
    CONTAINER_RUNTIME_FAILED = "CONTAINER_RUNTIME_FAILED"
    NETWORK_FAILED = "NETWORK_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# --- base ---------------------------------------------------------------------


class WorkspaceError(Exception):
    """Base for every §31 domain error.

    `recoverable` describes whether the *same request*, unmodified, could
    plausibly succeed later (e.g. `WORKSPACE_NOT_RUNNING` — start it and
    retry) versus needing the client to change something first (e.g.
    `REPOSITORY_NOT_ALLOWED`). Each subclass sets a considered default;
    override per raise site when the caller has better information.
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
        """The §31 response shape: stable code, concise message,
        recoverable flag, structured details."""
        return {
            "code": self.code.value,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": self.details,
        }


# --- the 24 typed errors -------------------------------------------------------


class InvalidArgumentError(WorkspaceError):
    code = ErrorCode.INVALID_ARGUMENT
    default_recoverable = True  # the client can correct the argument and retry


class WorkspaceNotFoundError(WorkspaceError):
    code = ErrorCode.WORKSPACE_NOT_FOUND
    default_recoverable = True  # open the workspace, or check the id, and retry


class WorkspaceNotRunningError(WorkspaceError):
    code = ErrorCode.WORKSPACE_NOT_RUNNING
    default_recoverable = True  # start it and retry


class RepositoryNotAllowedError(WorkspaceError):
    code = ErrorCode.REPOSITORY_NOT_ALLOWED
    default_recoverable = False  # an authorization decision, not a transient state


class PathOutsideRepositoryError(WorkspaceError):
    code = ErrorCode.PATH_OUTSIDE_REPOSITORY
    default_recoverable = True  # fix the path and retry


class FileConflictError(WorkspaceError):
    code = ErrorCode.FILE_CONFLICT
    default_recoverable = True  # re-read, re-hash, retry (§22.2)


class JobNotFoundError(WorkspaceError):
    code = ErrorCode.JOB_NOT_FOUND
    default_recoverable = False  # the job id will not start existing


class CommandFailedError(WorkspaceError):
    code = ErrorCode.COMMAND_FAILED
    default_recoverable = False  # the command itself failed; retrying the same call repeats it


class CommandTimeoutError(WorkspaceError):
    code = ErrorCode.COMMAND_TIMEOUT
    default_recoverable = True  # a longer timeout or exec_start instead of exec may succeed


class GitDirtyError(WorkspaceError):
    code = ErrorCode.GIT_DIRTY
    default_recoverable = True  # commit/stash the changes and retry (§22.3)


class GitConflictError(WorkspaceError):
    code = ErrorCode.GIT_CONFLICT
    default_recoverable = True  # resolve the conflict and retry


class GitHeadChangedError(WorkspaceError):
    code = ErrorCode.GIT_HEAD_CHANGED
    default_recoverable = True  # re-read HEAD, re-guard, retry (§45)


class PathGitMetadataError(WorkspaceError):
    code = ErrorCode.PATH_GIT_METADATA
    default_recoverable = False  # this path will never become writable through this tool (§12.1)


class GitRemoteNotAllowedError(WorkspaceError):
    code = ErrorCode.GIT_REMOTE_NOT_ALLOWED
    default_recoverable = False  # an authorization decision (§20.2)


class GitRefspecNotAllowedError(WorkspaceError):
    code = ErrorCode.GIT_REFSPEC_NOT_ALLOWED
    default_recoverable = True  # correct the refspec parameters and retry (§20.3)


class GitAuthFailedError(WorkspaceError):
    code = ErrorCode.GIT_AUTH_FAILED
    default_recoverable = False  # a credential/authorization problem, not a transient one


class RemoteProtectionMissingError(WorkspaceError):
    code = ErrorCode.REMOTE_PROTECTION_MISSING
    default_recoverable = False  # requires a ruleset change on the remote (§18.5)


class SSHKeyProvisionFailedError(WorkspaceError):
    code = ErrorCode.SSH_KEY_PROVISION_FAILED
    default_recoverable = False  # an operator configuration/registration problem (§19)


class GitNonFastForwardError(WorkspaceError):
    code = ErrorCode.GIT_NON_FAST_FORWARD
    default_recoverable = True  # pull first, then retry (§20.5)


class PolicyDeniedError(WorkspaceError):
    code = ErrorCode.POLICY_DENIED
    default_recoverable = False  # a policy decision, not a transient failure


class ContainerCreateFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_CREATE_FAILED
    default_recoverable = False  # usually a configuration problem (e.g. AT-016)


class ContainerRuntimeFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_RUNTIME_FAILED
    default_recoverable = True  # Docker/container hiccups are often transient


class NetworkFailedError(WorkspaceError):
    code = ErrorCode.NETWORK_FAILED
    default_recoverable = True  # network failures are often transient


class InternalError(WorkspaceError):
    code = ErrorCode.INTERNAL_ERROR
    default_recoverable = False  # unknown cause; do not promise a retry will help


# --- §31 "Secrets SHALL not be included": redaction as defense in depth ----------

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
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub PAT / OAuth / user-to-server tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained PAT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key
    re.compile(r"ssh-(?:ed25519|rsa|ecdsa)[A-Za-z0-9+/= ]{20,}"),  # SSH public/private key material
)


def _looks_like_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


#: HR-013 (`docs/HARDENING_SPEC.md` §8): `scheme://user:pass@host` embedded
#: credentials — a GitHub-token-shaped one is already caught by
#: `_SECRET_VALUE_PATTERNS`'s substring search regardless, but a plain
#: password (`https://user:hunter2@example.com`) matches none of those.
#: Only the userinfo segment is replaced, not the whole string: the rest of
#: a URL (host, path) is often genuinely useful diagnostic context, unlike
#: the credential itself.
_URL_USERINFO_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+@")


def _redact(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _redact(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        # `key=key` carried through: a list under a sensitive key
        # (`{"password": ["hunter2"]}`) used to lose that hint entirely,
        # since a bare recursive call with no `key` only ever caught a
        # value-pattern match, never the key-based one.
        return [_redact(v, key=key) for v in value]

    key_flags_secret = key is not None and any(hint in key.lower() for hint in _SECRET_KEY_HINTS)
    if isinstance(value, str):
        if key_flags_secret or _looks_like_secret(value):
            return "[REDACTED]"
        return _URL_USERINFO_RE.sub(r"\1[REDACTED]@", value)
    # A non-string scalar (`{"secret": 12345}`) under a sensitive key used
    # to pass through unredacted, since the original check only ever
    # looked at `key` inside the `isinstance(value, str)` branch.
    if key_flags_secret:
        return "[REDACTED]"
    return value


def redact_secrets(details: dict[str, Any]) -> dict[str, Any]:
    """Best-effort redaction of `details`, applied before anything reaches
    the client (§31) or an audit record (§24.1). Not a substitute for the
    caller never putting a secret in `details` in the first place — only a
    second layer."""
    return dict(_redact(details))


# --- mapping to the SDK's ToolError (§6.1) --------------------------------------


def to_tool_error(exc: WorkspaceError) -> MCPToolError:
    """Convert a domain error into the SDK's `ToolError`, whose message is
    the §31 shape serialized as JSON so the client can parse `code`,
    `recoverable`, and `details` out of it rather than pattern-matching
    free text.

    HR-013: `message` is redacted the same way `details` is — a real
    regression let a raw `ghp_...` token reach the client in cleartext in
    `message` while `details` was correctly `[REDACTED]`, since only
    `details` was ever passed through `_redact`. Many raise sites
    interpolate raw Git/SSH stderr, or a full remote URL, directly into
    `message`; a malformed `.mcp-workspace.yml`'s YAML parse error
    (`project_config.py`) echoes the offending source line the same way.
    Applying this once here, universally, covers every one of those call
    sites without touching them individually.
    """
    payload = exc.to_dict()
    payload["details"] = redact_secrets(payload["details"])
    redacted_message = _redact(payload["message"])
    payload["message"] = redacted_message if isinstance(redacted_message, str) else "[REDACTED]"
    return MCPToolError(json.dumps(payload, sort_keys=True))


def translate_workspace_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for tool functions (§6.1): a raised `WorkspaceError`
    becomes the SDK's `ToolError` via `to_tool_error`. Any other exception
    propagates unchanged, so the SDK's crash handling applies to it.
    Works on both sync and async tool functions.

    Typed as `Callable[..., Any]` rather than parameter-preserving: tool
    functions are invoked dynamically by the MCP framework from JSON
    arguments, via `**kwargs`, not through a statically-checked Python call
    site — a precisely-typed decorator would buy nothing here.
    """
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
