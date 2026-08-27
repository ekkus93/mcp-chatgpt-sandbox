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


def _all_test_source() -> str:
    chunks = []
    for path in _TESTS_DIR.rglob("*.py"):
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
