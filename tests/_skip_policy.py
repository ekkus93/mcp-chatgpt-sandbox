"""HR-029 (`docs/HARDENING_SPEC.md` §12): the skip-detection pytest
hooks, factored out of `tests/conftest.py` so `test_skip_policy.py` can
exercise the exact hook implementation `conftest.py` installs for real
(via `pytester`, a real separate pytest subprocess) rather than a
hand-duplicated copy that could silently drift from it.

A plain pytest skip doesn't affect the process exit code — a CI runner
with no Docker daemon reachable would otherwise "pass" while every
Docker-dependent test (187 of 566 test functions at the time this was
written) silently never ran. `pytest_runtest_logreport`/
`pytest_sessionfinish` below turn any skip into a hard failure. Safe as
a blanket policy today because `pytest.skip(...)` has exactly two call
sites in this whole suite, both in `workspace/conftest.py`, and both
mean "Docker is not reachable" — nothing else in this suite skips for
any other reason. A future, genuinely unrelated skip reason would need
this file revisited, not silently tolerated.
"""

from __future__ import annotations

import pytest

_skip_reports: list[pytest.TestReport] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped:
        _skip_reports.append(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _skip_reports:
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(
            f"HR-029: {len(_skip_reports)} test(s) skipped — failing the run rather "
            "than reporting false success (docs/HARDENING_SPEC.md §12). Every skip "
            "in this suite means Docker was unreachable.",
            red=True,
        )
    session.exitstatus = 1
