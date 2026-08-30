"""S33: a self-verifying audit that every Acceptance Test in `docs/SPEC.md`
is actually cited by name somewhere under `tests/`.

AR-004 (`docs/AUDIT_SPEC.md` §4): this used to audit `docs/SPEC.md`'s `AT`
series alone. `docs/HARDENING_SPEC.md`'s `HAT`, `docs/REMEDIATION_SPEC.md`'s
`RAT`, and `docs/REVIEW_SPEC.md`'s `VAT` series each define their own set of
required acceptance tests the same way `AT` does ("Each SHALL be an
automated test", `REMEDIATION_SPEC.md`'s own words) but had no equivalent
audit — a deleted `VAT` citation, for instance, would have gone unnoticed
indefinitely.

G2R-019 (`docs/GAP2_SPEC.md`): `docs/AUDIT_SPEC.md`'s own `AAT` series and
`docs/GAP_SPEC.md`'s `GAT` series used the identical language too and were
missed the same way — the pleasing irony being that `AAT-004` (§4 below)
is itself "removing every `VAT` citation SHALL make this audit fail," so
this module's own series was the one it forgot to audit. All six series
are covered here now, table-driven.

This does NOT judge whether a test's assertions are adequate — only that
each acceptance test has at least one test file that mentions its number,
so a future spec edit that adds/removes/renumbers one is caught here
instead of silently drifting out of sync with the suite.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = Path(__file__).resolve().parent
_THIS_FILE = Path(__file__).resolve()


@dataclass(frozen=True)
class _Series:
    """One acceptance-test series: `prefix`-`NNN` headings in `spec_path`,
    expected to number `expected_total` total, contiguously from 1.

    `known_uncited` (AR-004): numbers already, pre-existingly uncited by
    their own series identifier under `tests/` — recorded explicitly
    rather than silently passed over or back-filled. `HAT`/`RAT` both
    turned out to have real gaps: their properties are tested, but the
    tests cite the *requirement* number (`HR-014`, `RR-005`, ...) in
    their docstrings, never the *acceptance-test* number (`HAT-012`,
    `RAT-005`, ...) the two series don't otherwise correspond to 1:1.
    Backfilling ~20 citations across other files' tests is a separate
    body of work this task's own instructions say not to fold in
    (`docs/AUDIT_TODO.md` A4) — recording the exact gap here means a
    *regression* (a citation disappearing) is still caught, and closing
    a gap for real requires deliberately shrinking this set, not just
    happening to make the count line up."""

    prefix: str
    spec_path: Path
    heading_re: re.Pattern[str]
    expected_total: int
    known_uncited: frozenset[int] = frozenset()

    def spec_numbers(self) -> set[int]:
        return {int(m) for m in self.heading_re.findall(self.spec_path.read_text())}

    def citation_re(self, n: int) -> str:
        # `AT` alone requires a `-`/`_` between the prefix and the digits
        # (`AT[-_]0*{n}`) — deliberately strict, since "AT" as a bare
        # substring is common English and HR-027's own history is a real
        # accidental-self-citation bug this file already had to fix once.
        # `HAT`/`RAT`/`VAT` don't need that same caution (the three-letter
        # prefixes are not common English fragments followed by digits —
        # verified empirically against this file's own corpus before
        # relying on it) and, unlike `AT`, this codebase's own test names
        # cite them with *no* separator at all (`test_at_vat005_...`,
        # `test_at_hat019_...` — the number directly follows the prefix),
        # so the separator is optional here, not required.
        sep = r"[-_]" if self.prefix == "AT" else r"[-_]?"
        return rf"{self.prefix}{sep}0*{n}(?!\d)"


_SERIES = (
    _Series(
        prefix="AT",
        spec_path=_REPO_ROOT / "docs" / "SPEC.md",
        heading_re=re.compile(r"^### AT-(\d{3})\b", re.MULTILINE),
        expected_total=37,
    ),
    _Series(
        prefix="HAT",
        spec_path=_REPO_ROOT / "docs" / "HARDENING_SPEC.md",
        heading_re=re.compile(r"^### HAT-(\d{3})\b", re.MULTILINE),
        expected_total=22,
        known_uncited=frozenset({12, 18}),
    ),
    _Series(
        prefix="RAT",
        spec_path=_REPO_ROOT / "docs" / "REMEDIATION_SPEC.md",
        heading_re=re.compile(r"^### RAT-(\d{3})\b", re.MULTILINE),
        expected_total=20,
        known_uncited=frozenset(range(1, 20)),  # every RAT except RAT-020
    ),
    _Series(
        prefix="VAT",
        spec_path=_REPO_ROOT / "docs" / "REVIEW_SPEC.md",
        heading_re=re.compile(r"^- \*\*VAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=6,
    ),
    # G2R-019 (`docs/GAP2_SPEC.md`): `AAT` (`docs/AUDIT_SPEC.md`) and
    # `GAT` (`docs/GAP_SPEC.md`) use the identical "Each SHALL be an
    # automated test" language this module's own docstring cites as its
    # justification for auditing `HAT`/`RAT`/`VAT` — both were simply
    # never added. Measured before writing `known_uncited` below, not
    # assumed: `GAT` has zero citations anywhere under `tests/` at all
    # (this codebase names its GAP tests after the *requirement* —
    # `test_at_gr004_*` — never the acceptance-test number, the same
    # precedent `HR`/`RR`-prefixed tests already established, and not
    # itself a defect worth back-filling here). `AAT-001`/`002`/`003`
    # are cited (`test_at_aat001_*`, `test_at_aat002_*`,
    # `test_at_aat003_*`); `AAT-004` is not.
    _Series(
        prefix="AAT",
        spec_path=_REPO_ROOT / "docs" / "AUDIT_SPEC.md",
        heading_re=re.compile(r"^- \*\*AAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=4,
        # AAT-004 is itself "Removing every citation of one `VAT` SHALL
        # make the coverage audit fail" — this module's own series was
        # the one it forgot to audit, until this task.
        known_uncited=frozenset({4}),
    ),
    _Series(
        prefix="GAT",
        spec_path=_REPO_ROOT / "docs" / "GAP_SPEC.md",
        heading_re=re.compile(r"^- \*\*GAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=8,
        known_uncited=frozenset(range(1, 9)),  # every GAT — see comment above
    ),
)
_BY_PREFIX = {series.prefix: series for series in _SERIES}


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


def _uncited(series: _Series, source: str) -> set[int]:
    return {n for n in series.spec_numbers() if not re.search(series.citation_re(n), source, re.I)}


def test_the_spec_defines_exactly_37_acceptance_tests() -> None:
    """Sanity check on the parser itself: if this fails, either the spec
    grew/shrank its AT list (update the expected count) or the heading
    regex stopped matching (fix the regex) — not a silent pass either way."""
    assert _BY_PREFIX["AT"].spec_numbers() == set(range(1, 38))


def test_every_acceptance_series_defines_its_expected_contiguous_count() -> None:
    """The `AT`-only sanity check above, generalized to `HAT`/`RAT`/`VAT`:
    each series' own heading regex SHALL find exactly `expected_total`
    entries, numbered contiguously from 1 — the same signal, a wrong
    count means either the spec changed (update `expected_total`) or a
    heading regex broke (fix it), not a silent pass either way."""
    for series in _SERIES:
        assert series.spec_numbers() == set(range(1, series.expected_total + 1)), series.prefix


def test_every_acceptance_test_is_cited_somewhere_under_tests() -> None:
    source = _all_test_source()
    for series in _SERIES:
        uncited = sorted(_uncited(series, source) - series.known_uncited)
        assert not uncited, (
            f"{series.prefix}-{uncited} have no citation anywhere under tests/ and are not in "
            f"this file's own `known_uncited` allowlist for {series.prefix} — either add a "
            "citation (a docstring/comment naming it is enough) or, if the coverage is "
            "genuinely and deliberately deferred, add it to `known_uncited` with a comment "
            "explaining why (see AT-010's structured-tool-level note, or this module's own "
            f"`_Series` docstring for the pre-existing {series.prefix} gaps)."
        )


def test_the_known_uncited_allowlist_does_not_understate_real_coverage() -> None:
    """The complement of the test above: `known_uncited` records a gap,
    not a ceiling — if a number in the allowlist actually *does* have a
    citation now (someone closed the gap without updating this file),
    that SHALL be caught too, so the allowlist stays an accurate account
    of what's really missing rather than a stale one nobody trusts."""
    source = _all_test_source()
    for series in _SERIES:
        now_cited = sorted(
            n for n in series.known_uncited if re.search(series.citation_re(n), source, re.I)
        )
        assert not now_cited, (
            f"{series.prefix}-{now_cited} are in this file's `known_uncited` allowlist but are "
            "actually cited now — shrink the allowlist to match reality."
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
    series = _BY_PREFIX["AT"]
    assert target in series.spec_numbers()  # sanity: a real, spec-defined AT

    source = _all_test_source()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert not re.search(citation_re, scrubbed, re.I)

    uncited = sorted(_uncited(series, scrubbed))
    assert uncited == [target], (
        "scrubbing every AT-010 citation should make the audit flag AT-010 "
        f"specifically, not {uncited} — either the scrub over- or "
        "under-matched, or the audit's own detection is broken"
    )


def test_meta_removing_every_citation_for_a_vat_makes_the_audit_fail_for_it() -> None:
    """The same HR-027 self-verification as the `AT` meta-test above,
    proven once for one of the three newly-audited series too — `VAT`,
    since it (unlike `HAT`/`RAT`) currently has zero pre-existing gaps
    to complicate the picture: scrubbing VAT-001 SHALL make the audit
    flag exactly VAT-001, confirming the optional-separator citation
    regex (this module's own `_Series.citation_re`) actually detects a
    real removal, not just a coincidentally-already-passing state."""
    target = 1
    series = _BY_PREFIX["VAT"]
    assert target in series.spec_numbers()
    assert target not in series.known_uncited

    source = _all_test_source()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert not re.search(citation_re, scrubbed, re.I)

    uncited = sorted(_uncited(series, scrubbed) - series.known_uncited)
    assert uncited == [target], (
        "scrubbing every VAT-001 citation should make the audit flag VAT-001 "
        f"specifically, not {uncited}"
    )


def test_meta_removing_every_citation_for_an_aat_makes_the_audit_fail_for_it() -> None:
    """G2R-019 (`docs/GAP2_SPEC.md`): the same HR-027 self-verification,
    proven for `AAT` specifically — the series this task's own defect is
    about (`AAT` went unregistered here despite `docs/AUDIT_SPEC.md`
    using the identical "Each SHALL be an automated test" language every
    other series is audited against). `AAT-001` (unlike `AAT-004`, in
    `known_uncited`) has a real citation (`test_at_aat001_*`) to scrub,
    so this proves the new registration is genuinely wired up — not
    merely a table entry that happens to look complete."""
    target = 1
    series = _BY_PREFIX["AAT"]
    assert target in series.spec_numbers()
    assert target not in series.known_uncited

    source = _all_test_source()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert not re.search(citation_re, scrubbed, re.I)

    uncited = sorted(_uncited(series, scrubbed) - series.known_uncited)
    assert uncited == [target], (
        "scrubbing every AAT-001 citation should make the audit flag AAT-001 "
        f"specifically, not {uncited}"
    )
