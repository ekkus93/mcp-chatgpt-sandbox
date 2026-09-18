from __future__ import annotations

import ast
import inspect
import re
import tomllib
from pathlib import Path

from mcp_chatgpt_sandbox.setup.step_actions import SetupStepActionsMixin

_REPO_ROOT = Path(__file__).parents[2]
_SETUP_ROOT = _REPO_ROOT / "src" / "mcp_chatgpt_sandbox" / "setup"

# SWPH-007: this manifest is deliberately independent of filesystem discovery.
# Adding a presentation module requires an explicit architecture disposition and
# line-budget decision here; removing/renaming one must update the manifest too.
_PRESENTATION_MODULE_LIMITS = {
    "app.py": 750,
    "dialogs_panel.py": 150,
    "step_actions.py": 300,
    "step_actions_common.py": 150,
    "step_actions_core.py": 650,
    "step_actions_docker.py": 200,
    "step_actions_hardened.py": 200,
    "step_panels.py": 450,
    "git_policy_panel.py": 200,
}


def _is_presentation_module(path: Path) -> bool:
    """Discover setup presentation modules by documented naming convention."""

    name = path.name
    return (
        name == "app.py"
        or name == "step_panels.py"
        or name.startswith("step_actions")
        or name.endswith("_panel.py")
    )


def _presentation_files() -> tuple[Path, ...]:
    discovered = tuple(
        sorted(path for path in _SETUP_ROOT.glob("*.py") if _is_presentation_module(path))
    )
    discovered_names = {path.name for path in discovered}
    assert discovered_names == set(_PRESENTATION_MODULE_LIMITS), (
        "setup presentation-module discovery changed without an explicit architecture "
        f"disposition: discovered={sorted(discovered_names)!r}, "
        f"manifest={sorted(_PRESENTATION_MODULE_LIMITS)!r}"
    )
    return discovered


def _python_sources() -> list[Path]:
    return sorted(_SETUP_ROOT.rglob("*.py"))


def _working_directory_reads(source: str) -> list[int]:
    """Line numbers of calls in `source` that read the process working directory.

    Parsed rather than grepped: matching call nodes means an alias such as
    `from pathlib import Path as P` cannot slip past, and a docstring that
    merely mentions `Path.cwd()` is not a violation.

    PSCR2-004 (`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`) added the
    *uncalled* module-qualified reference — `os.getcwd` passed as a callback or
    default reads the working directory just as surely as calling it. It is
    deliberately limited to `os.getcwd`/`os.getcwdb`: extending it to any bare
    `.cwd` attribute would flag an ordinary data field such as `record.cwd`,
    turning the guard into a false-positive generator. Attribute nodes that are
    the target of a call already counted above are skipped, so `os.getcwd()`
    still reports one line rather than two.
    """

    tree = ast.parse(source)
    found: list[int] = []
    counted: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"cwd", "getcwd", "getcwdb"}
            or isinstance(func, ast.Name)
            and func.id in {"getcwd", "getcwdb"}
        ):
            found.append(func.lineno)
            counted.add(id(func))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in {"getcwd", "getcwdb"}
            and id(node) not in counted
        ):
            found.append(node.lineno)
    return sorted(found)


def _line_has_boundary_rationale(path: Path, lineno: int) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(0, lineno - 6)
    end = min(len(lines), lineno)
    window = "\n".join(lines[start:end])
    return "WCR-013:" in window and "fail closed" in window.casefold()


def test_wcr_013_setup_has_no_unjustified_bare_or_broad_exception_swallowing() -> None:
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            assert node.type is not None, f"bare except in {path}:{node.lineno}"
            if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                assert _line_has_boundary_rationale(path, node.lineno), (
                    f"broad except Exception lacks WCR-013 fail-closed rationale in "
                    f"{path}:{node.lineno}"
                )


def test_wcr_013_setup_has_no_pass_statement_swallowing_failures() -> None:
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        passes = [node for node in ast.walk(tree) if isinstance(node, ast.Pass)]
        assert not passes, f"pass statement(s) in setup implementation: {path}"


def test_wcr_013_every_setup_subprocess_run_is_time_bounded() -> None:
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr == "run"
            ):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            assert "timeout" in keywords, f"unbounded subprocess.run in {path}:{node.lineno}"


def test_wcr_013_setup_has_no_retry_or_sleep_framework() -> None:
    forbidden_calls = {("time", "sleep"), ("asyncio", "sleep")}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                assert pair not in forbidden_calls, f"retry/sleep behavior in {path}:{node.lineno}"
        text = path.read_text(encoding="utf-8")
        assert "tenacity" not in text
        assert "backoff" not in text


def test_pscrv_009_production_has_no_layout_dependent_build_context() -> None:
    """PSCR-009: no production module may derive a path by parent-index arithmetic.

    `setup/docker_setup.py` resolved `parents[3] / "docker" / "default"` and
    `workspace/container_config.py` did the same; both were correct only for this
    repository's `src/` layout and resolved outside the package root once
    installed. Scoped to the whole production package rather than to the two
    known sites, so a third one cannot appear unnoticed.
    """

    production_root = _REPO_ROOT / "src" / "mcp_chatgpt_sandbox"
    offenders: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Subscript):
                continue
            value = node.value
            if not (isinstance(value, ast.Attribute) and value.attr == "parents"):
                continue
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, int):
                offenders.append(f"{path.relative_to(production_root)}:{node.lineno}")

    assert offenders == [], (
        f"production modules must not index `.parents[...]` for a repository path: {offenders}"
    )


def test_pscrv_004_setup_never_reads_the_process_working_directory() -> None:
    """PSCR-004: the setup package must not consult the caller's cwd.

    SWPH-001 stopped the tunnel profile from *depending* on `Path.cwd()` but
    left the call itself in place, feeding a parameter the plan builder
    discarded. This guard is what makes the property greppable and durable
    rather than a fact about one call site.
    """

    offenders = [
        f"{path.name}:{lineno}"
        for path in _python_sources()
        for lineno in _working_directory_reads(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        f"setup package must not read the process working directory: {offenders}"
    )


def test_pscrv_004_working_directory_guard_detects_a_violation() -> None:
    """Prove the guard above can actually fail.

    A static guard whose detection never matches anything passes for the wrong
    reason, which is the same defect PSCR-006 exists to remove from the
    Git-policy mutation tests. These cases pin the detector itself instead of
    trusting that an empty result means a clean package.
    """

    assert _working_directory_reads("from pathlib import Path\nx = Path.cwd()\n") == [2]
    assert _working_directory_reads("import os\nx = os.getcwd()\n") == [2]
    assert _working_directory_reads("from pathlib import Path as P\nx = P.cwd()\n") == [2]
    # A prose mention must not count; two setup modules now document that they
    # never consult the working directory, and a substring check would flag them.
    assert _working_directory_reads('x = "never consults Path.cwd() here"\n') == []


def test_pscr2v_004_uncalled_os_getcwd_is_flagged_but_a_cwd_field_is_not() -> None:
    """PSCR2-004: a passed `os.getcwd` reads the cwd; a `record.cwd` field does not."""

    # Uncalled references, which the call-only detector missed.
    assert _working_directory_reads("import os\nf = os.getcwd\n") == [2]
    assert _working_directory_reads("import os\nrun(callback=os.getcwdb)\n") == [2]
    # The deliberate boundary: an ordinary field named `cwd` is not a read.
    assert _working_directory_reads("x = record.cwd\n") == []
    # A call is still counted exactly once, not once as a call and again as an
    # attribute reference.
    assert _working_directory_reads("import os\nx = os.getcwd()\n") == [2]


def test_swphv_007_presentation_manifest_matches_discovery_rule() -> None:
    # Calling the helper is the contract: an unclassified new presentation
    # module fails this test until its ownership and line budget are explicit.
    assert {path.name for path in _presentation_files()} == set(_PRESENTATION_MODULE_LIMITS)


_PERSISTENCE_MODULES = frozenset({"yaml"})
_PERSISTENCE_NAMES = frozenset({"atomic_write_text"})
_WRITE_METHODS = frozenset({"write_text", "write_bytes"})
_MODE_CHARACTERS = set("rwxabt+")


def _looks_like_write_mode(node: ast.expr) -> bool:
    """Whether `node` is a file-mode string constant that opens for writing."""

    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    mode = node.value
    if not mode or len(mode) > 3 or not set(mode) <= _MODE_CHARACTERS:
        return False
    return any(character in mode for character in "wax+")


def _opens_for_writing(call: ast.Call) -> bool:
    """Whether `call` is an `open(...)` that writes.

    Both shapes have to be recognized: the builtin puts the mode second
    (`open(path, "w")`) while `Path.open` puts it first (`path.open("w")`), so
    every positional argument and the `mode=` keyword are examined rather than
    one fixed position.
    """

    if any(_looks_like_write_mode(argument) for argument in call.args):
        return True
    return any(
        keyword.arg == "mode" and _looks_like_write_mode(keyword.value) for keyword in call.keywords
    )


def _persistence_violations(source: str) -> list[str]:
    """Config-persistence primitives used by `source`, located by parsing.

    PSCR-007 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`) replaced a raw
    substring scan for tokens such as `write_text(` and `import yaml`. That scan
    tripped on a comment that merely mentioned a token and could be walked past
    with an aliased import or a `Path.open("w")`, so it was weaker than the
    property it claimed to enforce.
    """

    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _PERSISTENCE_MODULES:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in _PERSISTENCE_MODULES:
                violations.append(f"from {module} import ...")
            violations.extend(
                f"from {module} import {alias.name}"
                for alias in node.names
                if alias.name in _PERSISTENCE_NAMES
            )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                # `_PERSISTENCE_NAMES` is checked here as well as on bare names:
                # a module-qualified `filesystem.atomic_write_text(...)` is the
                # same violation, and omitting it would make this guard weaker
                # than the substring scan it replaced rather than stronger.
                if func.attr in _WRITE_METHODS or func.attr in _PERSISTENCE_NAMES:
                    violations.append(f"{func.attr}() call")
                elif func.attr == "open" and _opens_for_writing(node):
                    violations.append("open() for writing")
            elif isinstance(func, ast.Name):
                if func.id in _PERSISTENCE_NAMES:
                    violations.append(f"{func.id}() call")
                elif func.id == "open" and _opens_for_writing(node):
                    violations.append("open() for writing")
    return violations


def test_wcr_002_presentation_does_not_own_yaml_or_atomic_config_writes() -> None:
    for path in _presentation_files():
        violations = _persistence_violations(path.read_text(encoding="utf-8"))
        assert violations == [], (
            f"presentation module {path.name} owns config persistence: {violations}"
        )


def test_pscrv_007_persistence_guard_catches_what_substring_matching_missed() -> None:
    """Pin the detector, including the cases the previous scan got wrong.

    A guard that silently matched nothing would pass for the wrong reason, and
    the two bypasses below are exactly why substring matching was replaced.
    """

    # Previously a false positive: prose is not persistence.
    assert _persistence_violations('"""Never calls write_text() here."""\n') == []
    assert _persistence_violations("# import yaml is deliberately avoided\nx = 1\n") == []

    # Previously bypassable: aliases defeat a literal token search.
    assert _persistence_violations("import yaml as y\n") == ["import yaml"]
    assert _persistence_violations("from yaml import safe_dump\n") == ["from yaml import ..."]
    assert _persistence_violations(
        "from mcp_chatgpt_sandbox.setup.filesystem import atomic_write_text as aw\n"
    ) == ["from mcp_chatgpt_sandbox.setup.filesystem import atomic_write_text"]

    # Previously invisible: a direct file handle is still persistence.
    assert _persistence_violations('p.open("w").write("x")\n') == ["open() for writing"]
    assert _persistence_violations('open(path, "w")\n') == ["open() for writing"]
    assert _persistence_violations('open(path, mode="a")\n') == ["open() for writing"]

    # A module-qualified call is the same violation as a bare one. The substring
    # scan caught this shape; an AST guard that only matched bare names would be
    # a regression in strength, not an improvement.
    assert _persistence_violations("filesystem.atomic_write_text(p, t)\n") == [
        "atomic_write_text() call"
    ]

    # Reading is not persistence, and must not be flagged.
    assert _persistence_violations('p.open("r")\n') == []
    assert _persistence_violations("p.read_text()\n") == []


def test_wcr_002_coordinator_growth_requires_explicit_architecture_change() -> None:
    # Limits are intentionally per module rather than one aggregate allowance.
    # A new module cannot bypass this guard because discovery must first match
    # the independent manifest above.
    for path in _presentation_files():
        limit = _PRESENTATION_MODULE_LIMITS[path.name]
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= limit, (
            f"{path.name} grew past architecture guard: {len(lines)} > {limit}"
        )


def test_swf_003_setup_app_has_no_broad_e501_per_file_exemption() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tool_section = pyproject.get("tool", {})
    ruff_section = tool_section.get("ruff", {}) if isinstance(tool_section, dict) else {}
    lint_section = ruff_section.get("lint", {}) if isinstance(ruff_section, dict) else {}
    per_file_ignores = (
        lint_section.get("per-file-ignores", {}) if isinstance(lint_section, dict) else {}
    )
    setup_app_ignores = per_file_ignores.get("src/mcp_chatgpt_sandbox/setup/app.py", [])
    assert "E501" not in setup_app_ignores


def test_swf_001_reviewed_mutation_handlers_are_two_step() -> None:
    docker_prepare = inspect.getsource(SetupStepActionsMixin.prepare_docker_build)
    docker_apply = inspect.getsource(SetupStepActionsMixin.build_docker_image)
    tunnel_prepare = inspect.getsource(SetupStepActionsMixin.prepare_tunnel)
    tunnel_apply = inspect.getsource(SetupStepActionsMixin.apply_tunnel)

    assert "build_default_image(" not in docker_prepare
    assert "apply_tunnel_profile(" not in tunnel_prepare
    assert "prepare_default_image_build_plan" not in docker_apply
    assert "build_tunnel_profile_plan" not in tunnel_apply


def test_wcr_013_machine_diagnostics_do_not_consume_user_attestation() -> None:
    source = (_SETUP_ROOT / "diagnostics.py").read_text(encoding="utf-8")
    assert "chatgpt_user_attested" not in source
    assert "USER-ATTESTED" not in source


def test_wcr_013_state_errors_do_not_embed_serialized_payloads() -> None:
    source = (_SETUP_ROOT / "model.py").read_text(encoding="utf-8")
    assert not re.search(r"raise SetupStateError\([^\n]*(raw|payload)", source)
