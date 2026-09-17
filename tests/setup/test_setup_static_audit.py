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


def test_swphv_007_presentation_manifest_matches_discovery_rule() -> None:
    # Calling the helper is the contract: an unclassified new presentation
    # module fails this test until its ownership and line budget are explicit.
    assert {path.name for path in _presentation_files()} == set(_PRESENTATION_MODULE_LIMITS)


def test_wcr_002_presentation_does_not_own_yaml_or_atomic_config_writes() -> None:
    forbidden = (
        "import yaml",
        "from yaml",
        "atomic_write_text",
        'open("config',
        "write_text(",
        "write_bytes(",
    )
    for path in _presentation_files():
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, (
                f"presentation module {path.name} owns config persistence token {token!r}"
            )


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
