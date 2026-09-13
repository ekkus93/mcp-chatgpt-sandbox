from __future__ import annotations

import ast
import inspect
import re
import tomllib
from pathlib import Path

from dev_workspace_mcp.setup.step_actions import SetupStepActionsMixin

_REPO_ROOT = Path(__file__).parents[2]
_SETUP_ROOT = _REPO_ROOT / "src" / "dev_workspace_mcp" / "setup"
_PRESENTATION_FILES = (
    _SETUP_ROOT / "app.py",
    _SETUP_ROOT / "step_actions.py",
    _SETUP_ROOT / "step_panels.py",
)


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


def test_wcr_002_presentation_does_not_own_yaml_or_atomic_config_writes() -> None:
    forbidden = (
        "import yaml",
        "from yaml",
        "atomic_write_text",
        'open("config',
        "write_text(",
        "write_bytes(",
    )
    for path in _PRESENTATION_FILES:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"presentation owns config persistence token {token!r}"


def test_wcr_002_coordinator_growth_requires_explicit_architecture_change() -> None:
    limits = {
        "app.py": 750,
        "step_actions.py": 900,
        "step_panels.py": 450,
    }
    for filename, limit in limits.items():
        lines = (_SETUP_ROOT / filename).read_text(encoding="utf-8").splitlines()
        assert len(lines) <= limit, (
            f"{filename} grew past architecture guard: {len(lines)} > {limit}"
        )


def test_swf_003_setup_app_has_no_broad_e501_per_file_exemption() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    per_file_ignores = (
        pyproject.get("tool", {})
        .get("ruff", {})
        .get("lint", {})
        .get("per-file-ignores", {})
    )
    setup_app_ignores = per_file_ignores.get("src/dev_workspace_mcp/setup/app.py", [])
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
