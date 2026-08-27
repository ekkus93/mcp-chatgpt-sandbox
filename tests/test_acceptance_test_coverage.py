"""S33: a self-verifying audit that every Acceptance Test in `docs/SPEC.md`
is actually cited by name somewhere under `tests/`.

This does NOT judge whether a test's assertions are adequate — only that
each `AT-NNN` heading in the spec has at least one test file that mentions
its number, so a future spec edit that adds/removes/renumbers an AT is
caught here instead of silently drifting out of sync with the suite.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_PATH = _REPO_ROOT / "docs" / "SPEC.md"
_TESTS_DIR = Path(__file__).resolve().parent

_AT_HEADING_RE = re.compile(r"^### AT-(\d{3})\b", re.MULTILINE)
# Matches both the spec's own "AT-010" form and the lowercase-underscore
# form Python test names are forced into ("at_010" / "AT_010") — a plain
# `AT-0[0-9][0-9]` grep undercounts real coverage for exactly this reason
# (caught the hard way earlier in this task).
# No trailing `\b`: a citation inside a Python identifier continues with
# `_` (e.g. `test_at_011_...`), and `_` is itself a word character, so a
# `\b` there never matches. A trailing "not another digit" lookahead is
# the correct boundary instead.
_AT_CITATION_RE_TEMPLATE = r"AT[-_]0*{n}(?!\d)"


def _spec_acceptance_test_numbers() -> set[int]:
    text = _SPEC_PATH.read_text()
    return {int(match) for match in _AT_HEADING_RE.findall(text)}


_THIS_FILE = Path(__file__).resolve()


def _all_test_source() -> str:
    """Every other test file's source — NOT this one's. HR-027: this
    module's own error message (`f"AT-{uncited} have no citation..."`)
    and this comment's own reference to "AT-010" are both literal text
    in *this file's* source; including `__file__` in the scan let every
    AT this file's own strings happen to mention vacuously "cite"
    itself, regardless of whether any real test anywhere else actually
    covers it. Reproduced: with every real AT-010 citation removed
    elsewhere, the audit still passed, purely because this file's own
    error-message text contains "AT-010"."""
    chunks = []
    for path in _TESTS_DIR.rglob("*.py"):
        if path.resolve() == _THIS_FILE:
            continue
        chunks.append(path.read_text())
    return "\n".join(chunks)


def test_the_spec_defines_exactly_37_acceptance_tests() -> None:
    """Sanity check on the parser itself: if this fails, either the spec
    grew/shrank its AT list (update the expected count) or the heading
    regex stopped matching (fix the regex) — not a silent pass either way."""
    assert _spec_acceptance_test_numbers() == set(range(1, 38))


def test_every_acceptance_test_is_cited_somewhere_under_tests() -> None:
    numbers = _spec_acceptance_test_numbers()
    source = _all_test_source()

    uncited = sorted(
        n for n in numbers if not re.search(_AT_CITATION_RE_TEMPLATE.format(n=n), source, re.I)
    )
    assert not uncited, (
        f"AT-{uncited} have no citation anywhere under tests/ — either add "
        "one (a docstring/comment naming the AT is enough) or, if the "
        "coverage is genuinely and deliberately deferred, cite it in a "
        "comment explaining why (see AT-010's structured-tool-level note)."
    )


def test_meta_removing_every_citation_for_an_at_makes_the_audit_fail_for_it() -> None:
    """HR-027: the audit above is only worth anything if it can actually
    fail — verified here rather than assumed, by scrubbing every
    citation of one real AT (AT-010, the same one the original defect
    was reproduced against: this file's own error-message text used to
    vacuously "cite" it) out of the *real* `_all_test_source()` output
    and confirming the audit's own uncited-detection then flags exactly
    that AT, and no other."""
    target = 10
    numbers = _spec_acceptance_test_numbers()
    assert target in numbers  # sanity: the target is a real, spec-defined AT

    source = _all_test_source()
    scrubbed = re.sub(_AT_CITATION_RE_TEMPLATE.format(n=target), "SCRUBBED", source, flags=re.I)
    assert not re.search(_AT_CITATION_RE_TEMPLATE.format(n=target), scrubbed, re.I)

    uncited = sorted(
        n for n in numbers if not re.search(_AT_CITATION_RE_TEMPLATE.format(n=n), scrubbed, re.I)
    )
    assert uncited == [target], (
        "scrubbing every AT-010 citation should make the audit flag AT-010 "
        f"specifically, not {uncited} — either the scrub over- or "
        "under-matched, or the audit's own detection is broken"
    )
