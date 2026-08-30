"""The §31 error taxonomy and its mapping to MCP tool errors (§6.1).

Every domain error in the system is one of the 26 typed exceptions below,
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
    GIT_NOTHING_TO_COMMIT = "GIT_NOTHING_TO_COMMIT"
    POLICY_DENIED = "POLICY_DENIED"
    CONTAINER_CREATE_FAILED = "CONTAINER_CREATE_FAILED"
    CONTAINER_RUNTIME_FAILED = "CONTAINER_RUNTIME_FAILED"
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
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


# --- the 25 typed errors -------------------------------------------------------


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


class GitNothingToCommitError(WorkspaceError):
    code = ErrorCode.GIT_NOTHING_TO_COMMIT
    default_recoverable = False  # the request named no real change; retrying as-is won't help


class PolicyDeniedError(WorkspaceError):
    code = ErrorCode.POLICY_DENIED
    default_recoverable = False  # a policy decision, not a transient failure


class ContainerCreateFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_CREATE_FAILED
    default_recoverable = False  # usually a configuration problem (e.g. AT-016)


class ContainerRuntimeFailedError(WorkspaceError):
    code = ErrorCode.CONTAINER_RUNTIME_FAILED
    default_recoverable = True  # Docker/container hiccups are often transient


class ImageNotFoundError(WorkspaceError):
    code = ErrorCode.IMAGE_NOT_FOUND
    # G2R-013 (`docs/GAP2_SPEC.md`): a persistent workspace already
    # RUNNING an older container, reconciled against an updated
    # `.mcp-workspace.yml` `image:` nothing has pulled locally yet —
    # `reconcile()` returns RUNNING without touching Docker images, so
    # nothing pulls the new one, and the reproducibility-record lookup
    # (`docker_client.images.get`) in `workspace_open` is what discovers
    # it is missing. Usually a configuration problem (a typo, or an
    # image genuinely not built/pulled yet), not a transient hiccup —
    # same category `ContainerCreateFailedError` is already in.
    default_recoverable = False


class NetworkFailedError(WorkspaceError):
    code = ErrorCode.NETWORK_FAILED
    default_recoverable = True  # network failures are often transient


class InternalError(WorkspaceError):
    code = ErrorCode.INTERNAL_ERROR
    default_recoverable = False  # unknown cause; do not promise a retry will help
    # H32 (`docs/HARDENING_SPEC.md` §9): still never raised anywhere.
    # Considered, and deliberately did NOT wire this up to a blanket
    # `except Exception` somewhere in the tool layer to make it
    # "reachable" — `translate_workspace_errors`'s own docstring is
    # explicit that an exception which is not a `WorkspaceError` is
    # meant to propagate as the SDK's own crash handling ("nothing from
    # an unexpected exception reaches the client"), precisely so a real
    # bug surfaces loudly (in logs, in CI, in an operator's face)
    # instead of being silently reshaped into a generic "something went
    # wrong, unknown cause" the client can do nothing useful with and
    # nobody is prompted to go fix. Every OTHER §31 error code this task
    # made reachable (`GIT_AUTH_FAILED`, `GIT_REFSPEC_NOT_ALLOWED`,
    # `NETWORK_FAILED`) has a specific, well-understood failure it
    # names; `INTERNAL_ERROR` by definition does not, which is exactly
    # what makes wiring it up a regression against this codebase's own
    # established "crash loudly on the genuinely unexpected" design,
    # not an improvement to it — the same shape of tension H30's three
    # retracted validators found and resolved the same way.


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


def _redact(value: Any, *, key: str | None = None, inherited_secret: bool = False) -> Any:
    """RR-009 (`docs/REMEDIATION_SPEC.md` §7): `inherited_secret` carries
    a secret-hinted ancestor *key*'s verdict down through however many
    dict/list layers separate it from the leaf that actually gets
    redacted — the list branch below already threaded a bare `key`
    through to its items (H11); the dict branch used to overwrite that
    with each child's own key instead of combining the two, so
    `{'secret': {'a': 'hunter2'}}` lost the outer hint entirely the
    moment it recursed one level and leaked (`'a'` flags nothing on its
    own). Each recursive call still also checks its own `key` — a
    `password` two levels under an unrelated ancestor is still caught —
    the two checks are OR'd, not one replacing the other.

    G2R-004 (`docs/GAP2_SPEC.md`): `key`'s own type hint (`str | None`)
    is not enforced by anything — `redact_secrets`' top-level `details:
    dict[str, Any]` only constrains the outermost keys; a *nested* dict
    reached through that `Any` can have a key of any hashable type, and
    the dict branch just below passes each one straight through as
    `key=k` on the way back into this function. `isinstance(key, str)`,
    not `key is not None`, is what makes that safe: a non-`str` key
    carries no free-text substring to hint-match in the first place, the
    same way `None` already doesn't, so it is treated identically rather
    than reaching `key.lower()` and raising `AttributeError`. Reproduced
    before this fix: `redact_secrets({"outer": {1: "hunter2"}})` crashed
    on the inner `1`; `to_tool_error` catches only `WorkspaceError`, so
    this would have surfaced as an unstructured crash, and `audit.py`'s
    `except OSError` would not have caught it either, exactly as GR-007
    documented for the `TypeError` case this same fail-closed pass
    fixed."""
    key_flags_secret = inherited_secret or (
        isinstance(key, str) and any(hint in key.lower() for hint in _SECRET_KEY_HINTS)
    )
    if isinstance(value, dict):
        return {k: _redact(v, key=k, inherited_secret=key_flags_secret) for k, v in value.items()}
    if isinstance(value, list):
        # No per-item key of its own (`key=key`, not each item's key) —
        # every item under a sensitive key inherits that key's verdict,
        # same as `inherited_secret` does for a dict's values.
        return [_redact(v, key=key, inherited_secret=key_flags_secret) for v in value]
    # VR-005 (`docs/REVIEW_SPEC.md` §4): `tuple` recurses the same way
    # `list` does, just type-preserved rather than coerced — R18's own
    # "preserve type" precedent, applied to the container itself, not
    # only to a non-redacted scalar inside it. Safe to preserve: a
    # `tuple` serializes as a JSON array, same as a `list` would.
    #
    # AR-002 (`docs/AUDIT_SPEC.md` §4): a `namedtuple` degrades to a
    # plain `tuple` here — `tuple(...)` on a `_fields`-carrying subclass
    # doesn't reconstruct it. Deliberately not special-cased: no caller
    # in this codebase constructs one for `details`/audit `metadata`
    # today, and its field names are never consulted as key hints either
    # way — a `P(user=..., password="hunter2")` under a non-secret key
    # leaves the value in the clear unless `_looks_like_secret` catches
    # its shape on its own. Content is still correctly redacted; only
    # the field names (and so this one extra source of key hints) are
    # lost.
    if isinstance(value, tuple):
        return tuple(_redact(v, key=key, inherited_secret=key_flags_secret) for v in value)
    # GR-007 (`docs/GAP_SPEC.md`): unlike `tuple`, a `set` has no JSON
    # representation at all — VR-005's type-preserving choice, correct
    # for `tuple`, made `redact_secrets` output that `json.dumps` (both
    # call sites: `to_tool_error`, `write_audit_event`) rejects outright
    # the moment any caller puts one in `details`/`metadata`. Coerced to
    # a `list` here instead: JSON-safe, and lossless for redaction's own
    # purpose (`details`/`metadata` values are reported, never consumed
    # back as sets).
    #
    # `frozenset` is NOT specially handled here (`isinstance(x, set)` is
    # `False` for one) — it falls through to the fail-closed placeholder
    # at the bottom of this function instead (G2R-004,
    # `docs/GAP2_SPEC.md`). That placeholder exists specifically because
    # this comment used to claim the fallthrough was merely "documented,
    # not silently unhandled" — which undersold the actual consequence:
    # a secret-flagged `frozenset` reached `json.dumps` with its
    # contents intact. A leak, not just a crash. See the placeholder's
    # own comment for the fix and how it was verified.
    if isinstance(value, set):
        return [_redact(v, key=key, inherited_secret=key_flags_secret) for v in value]

    if isinstance(value, str):
        if key_flags_secret or _looks_like_secret(value):
            return "[REDACTED]"
        return _URL_USERINFO_RE.sub(r"\1[REDACTED]@", value)
    # AR-002 (`docs/AUDIT_SPEC.md` §4): `bytes`/`bytearray` get the same
    # *two* checks `str` gets above — a secret-hinted key **or**
    # secret-shaped content — not the key hint alone. V5 (`docs/REVIEW_
    # SPEC.md` VR-005) added the key-hint half only, leaving a
    # secret-shaped `bytes` value under an unrelated key (e.g. raw
    # command output containing a token) unredacted while the identical
    # `str` was caught; reproduced before this fix, corrected here.
    # `_looks_like_secret`'s patterns are ASCII-shaped (a GitHub token,
    # a PEM header, SSH key material), so decoding with
    # `errors="replace"` first is safe for pattern-matching purposes —
    # a genuine secret matching these patterns is always valid ASCII,
    # and an unrelated non-UTF-8 byte sequence simply won't match
    # anything regardless of how the un-matched bytes decode.
    # `bytearray` is `bytes`'s mutable twin, handled identically;
    # `memoryview` is NOT specially handled here — same as `frozenset`
    # above, it falls through to the fail-closed placeholder at the
    # bottom of this function (G2R-004, `docs/GAP2_SPEC.md`) rather than
    # being redacted for secret-shaped content the way `bytes` is.
    #
    # GR-007 (`docs/GAP_SPEC.md`): the non-secret passthrough used to
    # return `value` itself — the raw `bytes`/`bytearray` object, which
    # `json.dumps` rejects exactly like `set` did above. Decoded with
    # `errors="replace"` instead (the same `decode_utf8_lossy`-style
    # tradeoff `sanitize_for_client`, `git/service.py`, already makes at
    # a client-facing boundary): JSON-safe, and the closest a `str` can
    # get to the original content when it isn't valid UTF-8.
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", errors="replace")
        if key_flags_secret or _looks_like_secret(text):
            return "[REDACTED]"
        return text
    # RR-009: a non-string, non-bytes JSON *primitive*
    # (`{"ssh_key_present": True}`, `{"key_count": 3}`, `None`) carries
    # no free-text secret material of its own — so it is never redacted
    # on a substring key hint alone. This used to silently turn every
    # `bool`/`int` under any `*key*`-ish field name into the string
    # `"[REDACTED]"`, corrupting `details`' and audit `metadata`'s types
    # for no actual secrecy gained; the type is preserved by simply
    # never touching it.
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    # G2R-004 (`docs/GAP2_SPEC.md`): every branch above explicitly
    # handles one known-safe type. This used to be an unconditional
    # `return value` instead — an open-by-default fallthrough, the same
    # shape as the denylist that produced G2R-001
    # (`docs/GAP2_TODO.md` X1) — and its actual failure mode was worse
    # than the `set`/`bytes` crash GR-007 (`docs/GAP_SPEC.md`) fixed:
    # neither the `frozenset` nor the `memoryview` comments above match
    # this value, so `key_flags_secret` was never consulted for either,
    # and a secret-flagged one reached `json.dumps` with its contents
    # intact. Reproduced: `_redact(frozenset({'hunter2'}), key='secret')`
    # returned the frozenset unchanged — a leak, not merely a crash.
    #
    # Fails closed instead: every recognized container/scalar type above
    # is redacted or passed through deliberately; anything this function
    # does not recognize — `frozenset`, `memoryview`, `Decimal`, `Path`,
    # `datetime`, a dataclass, whatever a future caller reaches for next
    # — becomes a type-named placeholder rather than reaching a caller
    # unexamined. This can never leak a secret (an unrecognized type's
    # contents are never inspected or serialized) and can never crash
    # `json.dumps` (the placeholder is always a `str`). Verified every
    # `details=`/`metadata=` call site in `src/` passes only primitives
    # or `str(...)`-coerced values today (`grep -rn "details=" src/`),
    # which is what makes fail-closed safe here rather than merely
    # tidy — re-run that grep before relying on this claim staying true.
    return f"[UNREPRESENTABLE: {type(value).__name__}]"


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
