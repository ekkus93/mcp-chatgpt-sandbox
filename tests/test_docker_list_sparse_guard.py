"""PSCR2-010 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`): every production
`containers.list(...)` call must pass `sparse=False` explicitly.

Each such call's results have `.labels` read, for ownership or test-namespace
filtering, and docker-py raises on `.labels` for sparse objects ("Label data is
not available for sparse objects"). `sparse=False` is the library default, so the
code worked without saying so; a later `sparse=True` -- a natural optimisation for
a listing that looks like it only needs ids -- would have broken reconcile,
adoption, and inventory at a distance from the change. PSCR2-003 pinned the same
dependency in the test helper; this pins it in production.

`volumes.list` is deliberately out of scope: docker-py's `VolumeCollection.list`
has no sparse mode at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PRODUCTION_ROOT = Path(__file__).parents[1] / "src" / "mcp_chatgpt_sandbox"


def _unpinned_container_lists(source: str) -> list[int]:
    """Lines of `<x>.containers.list(...)` calls that do not pass `sparse=False`.

    **Known boundaries — recorded, not fixed** (PSCR3-003,
    `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`). The detector matches only
    the literal shape `<expr>.containers.list(...)` with a literal `sparse=False`:

    - *Evasion.* An aliased collection — `cs = client.containers; cs.list(...)`
      — is not flagged. Seeing through the alias needs data-flow analysis an AST
      walk does not have.
    - *False positives.* `containers.list(**opts)` and `sparse=<variable>` are
      flagged even if they resolve to `sparse=False` at runtime. Treating either
      as pinned would be unsound, since the guard exists to make the pin visible
      at the call site.

    Neither shape exists in the production package today. If one appears, write
    the call in the literal shape rather than loosening this check.
    """

    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "list"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "containers"
        ):
            continue
        pinned = any(
            keyword.arg == "sparse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        )
        if not pinned:
            found.append(node.lineno)
    return found


def test_pscr2v_010_every_production_container_list_pins_sparse_false() -> None:
    offenders = [
        f"{path.relative_to(_PRODUCTION_ROOT)}:{lineno}"
        for path in sorted(_PRODUCTION_ROOT.rglob("*.py"))
        for lineno in _unpinned_container_lists(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "containers.list(...) must pass sparse=False, because the results' .labels are "
        f"read and docker-py refuses .labels on sparse objects: {offenders}"
    )


def test_pscr2v_010_guard_detects_an_unpinned_call() -> None:
    """Prove the guard can fail, so an empty result means a clean package."""

    assert _unpinned_container_lists("c.containers.list(all=True)\n") == [1]
    assert _unpinned_container_lists("c.containers.list(all=True, sparse=True)\n") == [1]
    assert _unpinned_container_lists("self._docker.containers.list()\n") == [1]
    assert _unpinned_container_lists("c.containers.list(all=True, sparse=False)\n") == []
    # PSCR3-003: the known boundaries, pinned so a change to the detector cannot
    # move them unnoticed. These describe current behavior; they pass before and
    # after this task by design, and are not evidence of discrimination.
    aliased = "cs = client.containers\ncs.list(all=True)\n"
    assert _unpinned_container_lists(aliased) == []  # evasion: not seen
    assert _unpinned_container_lists("c.containers.list(**opts)\n") == [1]  # false positive
    assert _unpinned_container_lists("c.containers.list(sparse=flag)\n") == [1]  # false positive
    # Other `.list` calls are not in scope; volumes have no sparse mode.
    assert _unpinned_container_lists("c.volumes.list(filters={})\n") == []
    assert _unpinned_container_lists("registry.list()\n") == []
