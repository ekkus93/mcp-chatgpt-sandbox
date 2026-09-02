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
this module's own series was the one it forgot to audit. All active acceptance
series, including GAP3's `G3AT`, are covered here now, table-driven.

This does NOT prove every assertion is semantically sufficient. It does require
that each active acceptance test is tied to executable pytest evidence, rather
than merely mentioned in prose, so additions/removals/renumbering cannot drift
silently out of sync with the suite.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class _Series:
    """One acceptance-test series declared by a source specification.

    G3R-016: executable coverage comes from an actual ``test_*`` function
    whose name contains the acceptance identifier or whose immediately
    preceding structured comment is ``# Covers: <identifier>``.  A missing
    executable test is allowed only when the acceptance entry itself carries
    a formal ``Coverage status`` marker with a reason and ``owner/task``.
    """

    prefix: str
    spec_path: Path
    heading_re: re.Pattern[str]
    expected_total: int

    def spec_numbers(self) -> set[int]:
        return {int(m) for m in self.heading_re.findall(self.spec_path.read_text())}

    def spec_entries(self) -> dict[int, str]:
        text = self.spec_path.read_text()
        matches = list(self.heading_re.finditer(text))
        entries: dict[int, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            entries[int(match.group(1))] = text[match.start() : end]
        return entries

    def formally_non_automated(self) -> set[int]:
        deferred: set[int] = set()
        for number, entry in self.spec_entries().items():
            status = re.search(
                r"^\*\*Coverage status:\*\*\s*(deferred|non-automated)\b([^\n]*)",
                entry,
                re.MULTILINE | re.IGNORECASE,
            )
            if status is None:
                continue
            tail = status.group(2)
            owner_index = tail.lower().find("owner/task:")
            assert owner_index >= 0, (
                f"{self.prefix}-{number:03d} has a formal coverage status but no owner/task"
            )
            reason = tail[:owner_index].strip(" —;:.")
            assert reason, f"{self.prefix}-{number:03d} coverage status has no reason"
            deferred.add(number)
        return deferred

    def citation_re(self, n: int) -> str:
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
    ),
    _Series(
        prefix="RAT",
        spec_path=_REPO_ROOT / "docs" / "REMEDIATION_SPEC.md",
        heading_re=re.compile(r"^### RAT-(\d{3})\b", re.MULTILINE),
        expected_total=20,
    ),
    _Series(
        prefix="VAT",
        spec_path=_REPO_ROOT / "docs" / "REVIEW_SPEC.md",
        heading_re=re.compile(r"^- \*\*VAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=6,
    ),
    # G2R-019 registered `AAT` and `GAT`; G3R-016 now requires those
    # series to use the same executable-evidence rule as every other
    # active acceptance series rather than relying on a blanket exception.
    _Series(
        prefix="AAT",
        spec_path=_REPO_ROOT / "docs" / "AUDIT_SPEC.md",
        heading_re=re.compile(r"^- \*\*AAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=4,
    ),
    _Series(
        prefix="GAT",
        spec_path=_REPO_ROOT / "docs" / "GAP_SPEC.md",
        heading_re=re.compile(r"^- \*\*GAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=8,
    ),
    _Series(
        prefix="G3AT",
        spec_path=_REPO_ROOT / "docs" / "GAP3_SPEC.md",
        heading_re=re.compile(r"^### G3AT-(\d{3})\b", re.MULTILINE),
        expected_total=17,
    ),
)
_BY_PREFIX = {series.prefix: series for series in _SERIES}


def _all_test_source() -> str:
    """Return only citations attached to actual automated tests.

    G3R-016 closes the old "mention anywhere" loophole: module prose, helper
    comments, audit error messages, and explanatory docstrings do not count.
    A citation is executable evidence only when it appears in a ``test_*``
    function name or in a structured ``# Covers: ...`` comment immediately
    attached to that test (before its decorators/definition).
    """
    chunks: list[str] = []
    for path in _TESTS_DIR.rglob("*.py"):
        text = path.read_text()
        lines = text.splitlines()
        tree = ast.parse(text, filename=str(path))
        candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                candidates.append(node)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                candidates.extend(
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for node in candidates:
            if not node.name.startswith("test_"):
                continue
            chunks.append(node.name)
            first_line = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            index = first_line - 2
            while index >= 0 and lines[index].lstrip().startswith("# Covers:"):
                chunks.append(lines[index].strip())
                index -= 1
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


def test_g3at_016_every_active_acceptance_test_has_executable_coverage() -> None:
    """G3AT-016: unexplained permanent citation gaps are forbidden."""
    source = _all_test_source()
    for series in _SERIES:
        deferred = series.formally_non_automated()
        uncited = sorted(_uncited(series, source) - deferred)
        assert not uncited, (
            f"{series.prefix}-{uncited} have no citation attached to an actual test and are not "
            "formally marked deferred/non-automated in their source spec with a reason and "
            "owner/task. Add executable coverage or make the source-spec status explicit."
        )


def test_formal_non_automated_status_never_masks_existing_executable_coverage() -> None:
    """A formal exception is debt/state, not a permanent blanket allowlist."""
    source = _all_test_source()
    for series in _SERIES:
        overlap = sorted(
            n
            for n in series.formally_non_automated()
            if re.search(series.citation_re(n), source, re.I)
        )
        assert not overlap, (
            f"{series.prefix}-{overlap} are formally deferred/non-automated but now have an "
            "executable citation; remove or update the stale source-spec status."
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
    assert target not in series.formally_non_automated()

    source = _all_test_source()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert not re.search(citation_re, scrubbed, re.I)

    uncited = sorted(_uncited(series, scrubbed) - series.formally_non_automated())
    assert uncited == [target], (
        "scrubbing every VAT-001 citation should make the audit flag VAT-001 "
        f"specifically, not {uncited}"
    )


def test_meta_removing_every_citation_for_an_aat_makes_the_audit_fail_for_it() -> None:
    """G2R-019 (`docs/GAP2_SPEC.md`): the same HR-027 self-verification,
    proven for `AAT` specifically — the series this task's own defect is
    about (`AAT` went unregistered here despite `docs/AUDIT_SPEC.md`
    using the identical "Each SHALL be an automated test" language every
    other series is audited against). `AAT-001` has a real executable
    citation (`test_at_aat001_*`) to scrub,
    so this proves the new registration is genuinely wired up — not
    merely a table entry that happens to look complete."""
    target = 1
    series = _BY_PREFIX["AAT"]
    assert target in series.spec_numbers()
    assert target not in series.formally_non_automated()

    source = _all_test_source()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert not re.search(citation_re, scrubbed, re.I)

    uncited = sorted(_uncited(series, scrubbed) - series.formally_non_automated())
    assert uncited == [target], (
        "scrubbing every AAT-001 citation should make the audit flag AAT-001 "
        f"specifically, not {uncited}"
    )
