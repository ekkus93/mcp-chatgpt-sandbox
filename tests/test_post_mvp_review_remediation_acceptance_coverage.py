"""R4 / PMORRAT-003 acceptance-coverage enforcement."""

from __future__ import annotations

import re
import runpy
from pathlib import Path

_AUDIT_PATH = Path(__file__).with_name("test_acceptance_test_coverage.py")


def test_r4_missing_and_unknown_acceptance_ids_fail_closed(tmp_path: Path) -> None:
    """PMORRAT-003: PMOAT/PMORRAT missing or unknown IDs must fail closed."""
    audit = runpy.run_path(str(_AUDIT_PATH))
    by_prefix = audit["_BY_PREFIX"]

    assert "PMOAT" in by_prefix, "PMOAT acceptance series is not enforced"
    assert "PMORRAT" in by_prefix, "PMORRAT acceptance series is not enforced"

    source, node_ids = audit["_iter_test_nodes"]()
    uncovered = audit["_uncovered"]
    executable_coverage = audit["_EXECUTABLE_COVERAGE"]

    pmoat = by_prefix["PMOAT"]
    scrubbed_pmoat = re.sub(pmoat.citation_re(1), "SCRUBBED", source, flags=re.I)
    assert 1 in uncovered(pmoat, scrubbed_pmoat, node_ids)

    pmorrat = by_prefix["PMORRAT"]
    pmorrat_targets = executable_coverage[("PMORRAT", 1)]
    without_pmorrat_target = node_ids - set(pmorrat_targets)
    assert 1 in uncovered(pmorrat, source, without_pmorrat_target)

    claims = tmp_path / "claims.md"
    claims.write_text("Verified by PMOAT-013 and PMORRAT-008\n")
    unknown_claims = audit["_unknown_claims"]
    assert unknown_claims(pmoat, paths=(claims,)) == {13}
    assert unknown_claims(pmorrat, paths=(claims,)) == {8}

    assert not re.search(by_prefix["AT"].citation_re(6), "PMOAT-006", re.I)
    assert not re.search(by_prefix["RAT"].citation_re(3), "PMORRAT-003", re.I)
