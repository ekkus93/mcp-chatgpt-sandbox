"""S4: the §31 error taxonomy, redaction, and the §6.1 interface-layer
mapping to MCP tool errors."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

from mcp_chatgpt_sandbox.errors import (
    CommandFailedError,
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
    """§31 lists 27 codes after G3R-004 adds
    `SSH_CREDENTIAL_CLEANUP_FAILED`; each must map to exactly one
    WorkspaceError subclass, and every subclass must be reachable from
    this module."""
    subclasses = WorkspaceError.__subclasses__()
    codes = [cls.code for cls in subclasses]
    assert len(codes) == 27
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


# --- HR-013 (docs/HARDENING_SPEC.md §8): redaction gaps -----------------------------


def test_at_hat011_a_list_of_plain_strings_under_a_sensitive_key_is_redacted() -> None:
    """A real regression: `{"password": ["hunter2"]}` used to reach the
    client unredacted — the list branch recursed with no `key`, so the
    parent's "this is a password" hint never reached the string items
    inside it."""
    assert redact_secrets({"password": ["hunter2"]}) == {"password": ["[REDACTED]"]}
    assert redact_secrets({"token": ["abc123def"]}) == {"token": ["[REDACTED]"]}


def test_at_hat011_a_non_string_scalar_under_a_sensitive_key_is_not_stringified() -> None:
    """RR-009 (`docs/REMEDIATION_SPEC.md` §7) corrected H11's own fix
    here: H11 made `{"secret": 12345}` redact to the *string*
    `"[REDACTED]"`, which silently mangled `key_count`/`*_present`-style
    booleans and integers elsewhere in `details`/audit `metadata`
    whenever their field name happened to contain a `*key*`-family
    substring. A non-string scalar carries no free-text secret material
    of its own — only a string can actually *be* a secret — so it is
    left untouched, type and value both, rather than redacted."""
    assert redact_secrets({"secret": 12345}) == {"secret": 12345}


# --- RR-009 (docs/REMEDIATION_SPEC.md §7): the dict branch's own hint gap ------------


def test_rr009_a_secret_hinted_key_s_hint_survives_recursing_into_a_nested_dict() -> None:
    """The defect exactly as reproduced: H11 fixed the list branch
    (above) and left the dict branch one line above it unfixed —
    `{'secret': {'a': 'hunter2'}}` leaked, because recursing into the
    nested dict passed only `'a'` (which flags nothing) as the key,
    discarding `'secret'`'s own verdict entirely."""
    assert redact_secrets({"secret": {"a": "hunter2"}}) == {"secret": {"a": "[REDACTED]"}}


def test_rr009_a_secret_hint_still_applies_several_containers_deep() -> None:
    """The inherited hint survives mixed list/dict nesting, not just one
    level of dict — the same `password` example H11's own list-branch
    test uses, one dict layer further in."""
    details = {"secret": [{"a": "hunter2"}, {"b": "unrelated but still under secret"}]}
    redacted = redact_secrets(details)
    assert redacted == {
        "secret": [{"a": "[REDACTED]"}, {"b": "[REDACTED]"}],
    }


def test_rr009_a_nested_key_s_own_hint_still_applies_under_an_unrelated_ancestor() -> None:
    """The inherited-hint fix must OR with each level's own key check,
    not replace it — a `password` two levels under an ancestor with no
    hint of its own (`outer`) must still be caught."""
    assert redact_secrets({"outer": {"password": "hunter2"}}) == {
        "outer": {"password": "[REDACTED]"}
    }


def test_rr009_non_string_scalars_keep_their_type_even_nested_under_a_secret_hint() -> None:
    """The type-preservation half of the fix also holds once the hint is
    *inherited* from an ancestor key (`present`/`count` flag nothing on
    their own — only the inherited `secret` verdict could redact them —
    and even then a non-string scalar is left untouched, not just at
    the top level)."""
    assert redact_secrets({"secret": {"present": True, "count": 3}}) == {
        "secret": {"present": True, "count": 3}
    }


def test_at_vat005_a_secret_flagged_tuple_recurses() -> None:
    """VR-005 (`docs/REVIEW_SPEC.md` §4): `_redact` used to recurse into
    `dict`/`list` only — a `tuple` under a secret-hinted key passed
    through completely unredacted."""
    assert redact_secrets({"secret": ("hunter2",)}) == {"secret": ("[REDACTED]",)}


def test_at_vat005_a_secret_flagged_set_recurses() -> None:
    """GR-007 (`docs/GAP_SPEC.md`): recurses into `list`, not `set` — a
    `set` isn't JSON-serializable at all, unlike `tuple` (a JSON array),
    so `redact_secrets`'s original type-preserving choice for `set`
    (matching VR-005's own precedent for `tuple`) was corrected once a
    caller putting one in `details`/`metadata` was shown to crash
    `json.dumps` at both call sites (`to_tool_error`, `write_audit_
    event`) — this assertion used to expect a `set` back; that was the
    defect, not this test."""
    redacted = redact_secrets({"secret": {"hunter2"}})
    assert redacted == {"secret": ["[REDACTED]"]}
    # GR-009 (`docs/GAP_SPEC.md`): `isinstance`, not just equality — a
    # `set`->`frozenset` coercion would have passed a plain `{"secret":
    # {"[REDACTED]"}} == {"secret": frozenset({"[REDACTED]"})}` check
    # (`True` in Python) the way this test's predecessor could not have
    # caught, back when both the fix and the assertion still used `set`
    # literals. Matches `test_at_vat005_tuple_recursion_preserves_the_
    # container_type`'s own shape, immediately above.
    assert isinstance(redacted["secret"], list)


def test_at_vat005_tuple_recursion_preserves_the_container_type() -> None:
    """Matching R18's own "preserve type" precedent for non-string
    scalars: recursing a `tuple` still returns a `tuple`, not a `list`,
    and a non-secret element inside it is left untouched."""
    redacted = redact_secrets({"secret": ("hunter2", 42)})
    assert redacted == {"secret": ("[REDACTED]", 42)}
    assert isinstance(redacted["secret"], tuple)


def test_at_vat005_a_secret_flagged_bytes_value_is_redacted() -> None:
    """VR-005: R18's own rationale for never redacting a non-string
    scalar — "only a string literally *can be* a secret" — is false for
    `bytes`: a token or key can be handed through as `bytes` just as
    easily as `str`. Redacted to the same `"[REDACTED]"` string literal
    the `str` branch already returns, not `b"[REDACTED]"` — nothing
    downstream needs a `bytes` value out of a redaction."""
    assert redact_secrets({"secret": b"ghp_abc"}) == {"secret": "[REDACTED]"}


def test_at_vat005_a_non_secret_bytes_value_is_decoded_not_left_as_bytes() -> None:
    """GR-007 (`docs/GAP_SPEC.md`): the non-secret passthrough used to
    return the raw `bytes` object unchanged — also not JSON-serializable,
    the same defect class as the `set` case above. Decoded with
    `errors="replace"` instead, matching `sanitize_for_client`'s own
    lossy-but-safe tradeoff at a client-facing boundary elsewhere in this
    codebase; content is unredacted, only the type changed."""
    assert redact_secrets({"payload": b"hello"}) == {"payload": "hello"}


def test_at_gr007_a_non_utf8_non_secret_bytes_value_decodes_lossily_not_left_as_bytes() -> None:
    """The genuinely-non-UTF-8 case `errors="replace"` exists for: still
    not the raw `bytes` object (still not JSON-serializable), and still
    unredacted (content, not secret-shaped)."""
    redacted = redact_secrets({"payload": b"\xff\xfe"})
    assert isinstance(redacted["payload"], str)
    assert "�" in redacted["payload"]


def test_at_aat002_a_secret_shaped_bytes_value_is_redacted_under_an_unrelated_key() -> None:
    """AR-002 (`docs/AUDIT_SPEC.md` §4): `bytes` only ever redacted on the
    key hint (VR-005/V5) — the value-pattern content scan `str` gets
    (`_looks_like_secret`) never ran on `bytes`, so a secret-*shaped*
    `bytes` value under a key that hints at nothing leaked, unlike the
    identical `str`. Both SHALL be caught the same way."""
    token = "ghp_" + "a" * 20
    assert redact_secrets({"blob": token}) == {"blob": "[REDACTED]"}
    assert redact_secrets({"blob": token.encode()}) == {"blob": "[REDACTED]"}


def test_at_aat002_a_secret_shaped_bytearray_value_is_redacted() -> None:
    """`bytearray` is `bytes`'s mutable twin — same content scan, same
    key-hint check, on both counts."""
    token = ("ghp_" + "a" * 20).encode()
    assert redact_secrets({"blob": bytearray(token)}) == {"blob": "[REDACTED]"}
    assert redact_secrets({"secret": bytearray(b"hunter2")}) == {"secret": "[REDACTED]"}


def test_at_aat002_a_non_secret_bytearray_value_is_decoded_not_left_as_bytearray() -> None:
    """GR-007 (`docs/GAP_SPEC.md`): `bytearray`'s own type-preservation
    used to stay `bytearray` here — also not JSON-serializable, the same
    defect class `bytes`/`set` had. Decoded with `errors="replace"`
    instead, matching the plain-`bytes` branch's own fix; content is
    unredacted, only the type changed."""
    redacted = redact_secrets({"payload": bytearray(b"hello")})
    assert redacted == {"payload": "hello"}
    assert isinstance(redacted["payload"], str)


def test_at_g2r004_a_secret_flagged_frozenset_does_not_leak() -> None:
    """G2R-004 (`docs/GAP2_SPEC.md`): `frozenset` had no branch in
    `_redact` at all, so it fell through to the old unconditional
    `return value` — `key_flags_secret` was never consulted, and the
    secret reached the caller with its contents intact. Reproduced
    before this fix: `redact_secrets({"secret": frozenset({"hunter2"})})`
    returned the frozenset unchanged, not `"[REDACTED]"` or any
    redacted form — a leak, not merely the `json.dumps` crash GR-007
    fixed for `set`/`bytes`."""
    redacted = redact_secrets({"secret": frozenset({"hunter2"})})
    assert redacted == {"secret": "[UNREPRESENTABLE: frozenset]"}
    assert "hunter2" not in str(redacted)


def test_at_g2r004_a_secret_flagged_memoryview_does_not_leak() -> None:
    """The same defect, for `memoryview` — `bytes`/`bytearray` get a
    content scan and a key-hint check; `memoryview` got neither."""
    redacted = redact_secrets({"secret": memoryview(b"hunter2")})
    assert redacted == {"secret": "[UNREPRESENTABLE: memoryview]"}


def test_at_g2r004_an_unrecognized_type_under_a_non_secret_key_is_a_placeholder() -> None:
    """Fail-closed applies regardless of the key hint — an unrecognized
    type is never passed through untouched, even when nothing suggests
    it's a secret, since `_redact` cannot safely inspect its contents
    either way."""
    from decimal import Decimal

    assert redact_secrets({"count": Decimal("3.14")}) == {"count": "[UNREPRESENTABLE: Decimal]"}


def test_at_g2r004_json_primitives_still_pass_through_unchanged() -> None:
    """The fail-closed placeholder SHALL NOT swallow the ordinary case —
    RR-009's own concern, re-verified after narrowing the fallthrough:
    `bool`/`int`/`float`/`None` are still returned exactly as given."""
    redacted = redact_secrets(
        {"flag": True, "count": 3, "ratio": 0.5, "absent": None, "text": "ok"}
    )
    assert redacted == {"flag": True, "count": 3, "ratio": 0.5, "absent": None, "text": "ok"}


def test_at_g2r004_a_non_str_dict_key_does_not_crash() -> None:
    """G2R-004: `redact_secrets`' own `details: dict[str, Any]` hint
    constrains only the outermost keys — a nested dict reached through
    that `Any` can have a key of any hashable type. Reproduced before
    this fix: `redact_secrets({"outer": {1: "hunter2"}})` raised
    `AttributeError: 'int' object has no attribute 'lower'` from
    `key.lower()`. A non-`str` key simply can't hint at a secret by
    substring match, so it's treated like `key=None` — no crash, and
    the value is still redacted if it's secret-*shaped* on its own
    content, exercised here via the token pattern rather than the key."""
    token = "ghp_" + "c" * 20
    redacted = redact_secrets({"outer": {1: token}})
    assert redacted == {"outer": {1: "[REDACTED]"}}


def test_at_hat011_url_userinfo_is_redacted_even_under_an_unrelated_key() -> None:
    """`scheme://user:pass@host` embedded credentials — a plain password
    (not GitHub-token-shaped) matches none of the value patterns and the
    key (`"url"`) doesn't hint at a secret either, so this needs its own
    check. Only the credential is masked; the rest of the URL, often
    genuinely useful diagnostic context, is preserved."""
    redacted = redact_secrets({"url": "https://user:hunter2@example.com/owner/repo"})
    assert redacted == {"url": "https://[REDACTED]@example.com/owner/repo"}


def test_at_hat011_to_tool_error_redacts_the_message_not_only_details() -> None:
    """A real regression, reproduced exactly this way during the
    2026-08-27 review: a `ghp_`-shaped token interpolated into `message`
    reached the client in cleartext while the same token in `details`
    was correctly `[REDACTED]`."""
    token = "ghp_" + "B" * 36
    exc = CommandFailedError(
        f"git push failed: remote: use a token: token={token}",
        details={"url": "https://user:hunter2@github.com/owner/repo.git"},
    )
    payload = json.loads(str(to_tool_error(exc)))
    assert token not in payload["message"]
    assert payload["details"]["url"] == "https://[REDACTED]@github.com/owner/repo.git"


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


def test_at_gr007_to_tool_error_does_not_crash_on_a_set_or_bytes_value() -> None:
    """GR-007 (`docs/GAP_SPEC.md`): `to_tool_error` calls `json.dumps` on
    `_redact`'s own output — reproduced before this fix that a `set` or
    non-secret `bytes` in `details` raised `TypeError: Object of type
    set/bytes is not JSON serializable` here, escaping as an unhandled
    crash rather than the structured response the caller actually
    raised. No caller constructs either today (latent, not live) — this
    guards the boundary regardless."""
    exc = InvalidArgumentError("bad set", details={"tags": {"a", "b"}, "payload": b"\x00\x01"})
    payload = json.loads(str(to_tool_error(exc)))  # SHALL NOT raise
    assert isinstance(payload["details"]["tags"], list)
    assert isinstance(payload["details"]["payload"], str)


def test_at_g2r004_to_tool_error_does_not_leak_a_secret_flagged_frozenset() -> None:
    """G2R-004 (`docs/GAP2_SPEC.md`): the end-to-end half, alongside
    `test_at_g2r004_a_secret_flagged_frozenset_does_not_leak` above —
    through the real client-facing boundary, not `_redact` directly."""
    exc = InvalidArgumentError("bad frozenset", details={"secret": frozenset({"hunter2"})})
    payload = json.loads(str(to_tool_error(exc)))  # SHALL NOT raise, SHALL NOT leak
    assert payload["details"]["secret"] == "[UNREPRESENTABLE: frozenset]"
    assert "hunter2" not in str(payload)


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
