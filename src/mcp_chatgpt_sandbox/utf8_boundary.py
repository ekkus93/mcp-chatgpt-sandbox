"""UTF-8 character-boundary helpers (RR-018, `docs/REMEDIATION_SPEC.md`
§13) — shared by any host-side code that windows raw bytes before
decoding: `git/diff.py`'s `next_offset` pagination (RR-016) and
`filesystem/service.py`'s `read_file`. A byte window computed purely
from a byte count (`max_bytes`, a read `limit`) can legitimately end
mid-character; naively decoding it with `errors="replace"`/`"ignore"`
corrupts or silently drops that character instead of deferring it to
wherever the rest of its bytes actually are (the next page, or simply
"more of the file that wasn't read").

Extracted from `git/diff.py`'s own `_trim_incomplete_trailing_char`
(RR-016) rather than reimplemented for `read_file` — the same defect
class (a byte window blind to character boundaries) and the same fix
apply to both call sites, and duplicating a subtle, security-adjacent
algorithm in two places risks the two copies silently diverging.
"""

from __future__ import annotations


def utf8_lead_sequence_length(lead_byte: int) -> int | None:
    """How many bytes the UTF-8 sequence starting with `lead_byte` needs
    in total, or `None` if `lead_byte` isn't a valid lead byte at all (a
    continuation byte, or one of the values UTF-8 never uses as a lead:
    0xC0, 0xC1, 0xF5-0xFF)."""
    if lead_byte & 0x80 == 0x00:
        return 1
    if lead_byte & 0xE0 == 0xC0:
        return 2
    if lead_byte & 0xF0 == 0xE0:
        return 3
    if lead_byte & 0xF8 == 0xF0:
        return 4
    return None


def trim_incomplete_trailing_char(window: bytes) -> bytes:
    """`window` is an arbitrary byte slice of UTF-8 text — its end can
    land in the middle of a multi-byte character. Trim any incomplete
    trailing sequence off so decoding `window` never corrupts that
    character.

    Only a *truncated* sequence — one that would be valid if more bytes
    followed — is trimmed. A genuinely invalid byte is left in place;
    this function's job is boundary correctness, not encoding
    validation, so the caller's own lossy decode still applies to it.

    Scans backward from the end of `window` (at most 4 bytes, UTF-8's
    own maximum sequence length) for the trailing character's own lead
    byte, independent of whatever precedes it in the window — an
    earlier invalid byte does not mask a genuinely truncated character
    later on (confirmed empirically to be a real defect when this
    instead inspected only the first `UnicodeDecodeError` a plain
    `window.decode("utf-8")` raised: `b'\\xff\\xe6\\x97'`, an invalid
    byte followed by 2 of the 3 bytes of `日`, used to come back
    unchanged)."""
    limit = min(len(window), 4)
    for back in range(1, limit + 1):
        byte = window[-back]
        if byte & 0xC0 == 0x80:
            continue  # a continuation byte — keep scanning backward
        seq_len = utf8_lead_sequence_length(byte)
        if seq_len is not None and back < seq_len:
            return window[: len(window) - back]
        break  # a lead byte (valid or not) — nothing earlier to trim
    return window
