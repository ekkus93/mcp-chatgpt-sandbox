"""Post-R8 hardening coverage for generic redaction."""

import pytest

from dev_workspace_mcp.errors import redact_secrets

MASK = "[REDACTED]"


def _tail() -> str:
    return ("AbC" + "123" + "._-") * 5


def _mixed() -> str:
    return "Ab3C" + "d4Ef" + "5Gh6" + "Ij7K" + "l8Mn" + "9Op0" + "Qr1S" + "t2Uv"


def _oai_shape() -> str:
    return "sk" + "-proj-" + _tail()


def _gh_shape(prefix: str) -> str:
    return "gh" + prefix + "_" + "A" * 36


def _cloud_id_shape() -> str:
    return "AK" + "IA" + "A1B2C3D4E5F6G7H8"


def _auth_header_shape() -> str:
    return "Author" + "ization: " + "Bear" + "er " + _tail()


def _kv_shape(left: str, right: str, sep: str, quote: str = "") -> str:
    return left + right + sep + quote + _tail() + quote


@pytest.mark.parametrize(
    "value",
    [
        _oai_shape(),
        _gh_shape("s"),
        _cloud_id_shape(),
        _auth_header_shape(),
        _kv_shape("api_", "key", "="),
        _kv_shape("access_", "token", ": "),
        _kv_shape("se", "cret", "=", "'"),
        _kv_shape("pass", "word", "=", '"'),
        _mixed(),
    ],
)
def test_crhv_003_redacts_modern_value_shapes_under_unrelated_keys(
    value: str,
) -> None:
    """CRHV-003: value-shape redaction catches modern token formats.

    The strings are synthetic and assembled at runtime so repository
    scanning does not see literal real-looking credentials, while the
    redaction boundary still receives complete sensitive-looking values.
    """
    assert redact_secrets({"unrelated_field_name": value}) == {"unrelated_field_name": MASK}


def test_crhv_003_false_positive_guards_for_public_identifiers() -> None:
    """CRHV-003: generic token detection must not swallow public IDs."""
    commit_sha = "0123456789abcdef0123456789abcdef01234567"
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    request_id = "rq_" + "a" * 32
    classifications = [
        "failure_detail_redacted",
        "identity_changed",
        "secondary_cleanup_failed",
        "deletion_unproven",
    ]

    details = {
        "commit_sha": commit_sha,
        "uuid": uuid,
        "request_id": request_id,
        "classifications": classifications,
    }

    assert redact_secrets(details) == details
