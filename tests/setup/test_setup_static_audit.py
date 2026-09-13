from __future__ import annotations

import ast
import re
from pathlib import Path

_SETUP_ROOT = Path(__file__).parents[2] / "src" / "dev_workspace_mcp" / "setup"
_PRESENTATION_FILES = (_SETUP_ROOT / "app.py", _SETUP_ROOT / "step_panels.py")


def _python_sources() -> list[Path]:
    return sorted(_SETUP_ROOT.rglob("*.py"))


def test_wcr_013_setup_has_no_bare_or_broad_exception_swallowing() -> None:
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            assert node.type is not None, f"bare except in {path}:{node.lineno}"
            if isinstance(node.type, ast.Name):
                assert node.type.id != "Exception", (
                    f"broad except Exception in {path}:{node.lineno}; "
                    "use a typed fail-closed boundary"
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


def test_wcr_013_machine_diagnostics_do_not_consume_user_attestation() -> None:
    source = (_SETUP_ROOT / "diagnostics.py").read_text(encoding="utf-8")
    assert "chatgpt_user_attested" not in source
    assert "USER-ATTESTED" not in source


def test_wcr_013_state_errors_do_not_embed_serialized_payloads() -> None:
    source = (_SETUP_ROOT / "model.py").read_text(encoding="utf-8")
    assert not re.search(r"raise SetupStateError\([^\n]*(raw|payload)", source)
