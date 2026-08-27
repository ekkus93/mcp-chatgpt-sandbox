"""HR-029 (`docs/HARDENING_SPEC.md` §12), HAT-022: the skip-detection
hooks in `_skip_policy.py` (installed for real by `conftest.py`),
exercised end to end via pytest's own `pytester` plugin — a real,
separate pytest subprocess, not a hand-simulated call, since the
property under test (the *process exit code*) only exists at that
level. `runpytest_subprocess` (not the in-process `runpytest`) is
required here specifically: `_skip_policy.py`'s `_skip_reports` list is
module-level state, and an in-process run would share that module
object with *this* outer test run, polluting it.
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_skip_policy(pytester: pytest.Pytester) -> None:
    """Puts the real repo root on `sys.path` and installs the real
    `_skip_policy.py` hooks — not a copy of their logic, so this test
    can never silently drift from what `conftest.py` actually runs."""
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from tests._skip_policy import pytest_runtest_logreport, pytest_sessionfinish
        """
    )


def test_a_clean_pass_exits_zero(pytester: pytest.Pytester) -> None:
    """Sanity check: the hook must not turn an ordinary, skip-free pass
    into a failure."""
    _install_skip_policy(pytester)
    pytester.makepyfile("def test_ok():\n    assert True\n")

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=1)
    assert result.ret == 0


def test_at_hat022_a_skip_fails_the_run(pytester: pytest.Pytester) -> None:
    """HAT-022, literally: a skip (standing in for "Docker unreachable")
    SHALL make the run exit non-zero, even though pytest's own default
    behavior treats a skip as neither a pass nor a failure — this is
    exactly the property that let CI report success while 187 of 566
    test functions never ran."""
    _install_skip_policy(pytester)
    pytester.makepyfile(
        """
        import pytest

        def test_skipped():
            pytest.skip("Docker is not reachable, skipping integration tests")
        """
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(skipped=1)
    assert result.ret != 0


def test_a_mix_of_pass_and_skip_still_fails(pytester: pytest.Pytester) -> None:
    """The real-world shape: most tests pass, a handful skip — one skip
    anywhere is enough, it doesn't need to be every test."""
    _install_skip_policy(pytester)
    pytester.makepyfile(
        """
        import pytest

        def test_ok():
            assert True

        def test_skipped():
            pytest.skip("Docker is not reachable")
        """
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret != 0


def test_a_genuine_failure_still_fails_without_the_hooks_help(
    pytester: pytest.Pytester,
) -> None:
    """Not a regression the hook could introduce, but worth confirming
    directly: an ordinary assertion failure exits non-zero on its own —
    the hook only needs to act when *every* failing-to-run test would
    otherwise have looked like nothing was wrong."""
    _install_skip_policy(pytester)
    pytester.makepyfile("def test_fails():\n    assert False\n")

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(failed=1)
    assert result.ret != 0
