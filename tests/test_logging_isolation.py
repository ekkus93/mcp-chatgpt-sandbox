"""GR-011 (`docs/GAP_SPEC.md`): `tests/conftest.py`'s
`_restore_root_logging_state` autouse fixture, exercised end to end via
`pytester` — a real, separate pytest subprocess, not a hand-simulated
call, since the property under test (root logging state surviving from
one *test* into the next *test*, within one session) only exists at that
level. `runpytest_subprocess` (not the in-process `runpytest`) is
required here for the same reason `test_skip_policy.py`'s own tests need
it: `logging`'s root logger is process-global state, and an in-process
run would let this outer test's own logging setup interfere with the
inner one's, or vice versa.
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_logging_isolation_fixture(pytester: pytest.Pytester) -> None:
    """Puts the real repo root on `sys.path` and installs the real
    `_restore_root_logging_state` fixture from `tests/conftest.py` — not
    a copy of its logic, so this test can never silently drift from what
    `conftest.py` actually runs."""
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from tests.conftest import _restore_root_logging_state
        """
    )


def test_a_leaky_configure_logging_call_does_not_alter_state_for_later_tests(
    pytester: pytest.Pytester,
) -> None:
    """G9's own reproduction, generalized: `logging.basicConfig(...,
    force=True)` — what `configure_logging` calls, and what
    `tests/test_cli.py`'s `test_serve_*` tests trigger via the real CLI
    `serve` command — replaces root's level *and* handlers. Without the
    fixture this leaks into every test that happens to run afterward in
    the same session; `pytest`'s own default (alphabetical) collection
    order made that leak permanent rather than occasional, since
    `tests/test_cli.py` always collects before `tests/workspace/`."""
    _install_logging_isolation_fixture(pytester)
    pytester.makepyfile(
        """
        import logging

        def test_0_baseline_handler_count():
            # pytest's own logging plugin already has handlers on root
            # by the time any test runs (live-log, file-log, caplog's
            # own capturing handler) — comparing against a captured
            # baseline, not a hardcoded 0, is what actually proves
            # "restored," independent of which plugins are active.
            global _baseline_handlers
            _baseline_handlers = len(logging.getLogger().handlers)

        def test_1_a_leaky_call_changes_roots_level_and_handlers():
            root = logging.getLogger()
            assert root.level != logging.CRITICAL, "test isolation broken before this even runs"
            logging.basicConfig(level="CRITICAL", force=True)
            assert root.level == logging.CRITICAL
            assert len(root.handlers) >= 1  # basicConfig always adds one

        def test_2_a_later_test_sees_roots_original_level_and_handler_count():
            root = logging.getLogger()
            assert root.level != logging.CRITICAL
            assert len(root.handlers) == _baseline_handlers
        """
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=3)


def test_without_the_fixture_the_leak_reproduces(pytester: pytest.Pytester) -> None:
    """The negative control: the same two tests, with no isolation
    fixture installed at all, demonstrate the defect the fixture above
    closes — the second test SHALL fail, proving this is a real,
    reproducible leak and not a property that would hold regardless."""
    pytester.makepyfile(
        """
        import logging

        def test_a_leaky_call_changes_roots_level_and_handlers():
            root = logging.getLogger()
            assert root.level != logging.CRITICAL, "test isolation broken before this even runs"
            logging.basicConfig(level="CRITICAL", force=True)
            assert root.level == logging.CRITICAL

        def test_a_later_test_sees_the_leaked_level():
            assert logging.getLogger().level == logging.CRITICAL
        """
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=2)
