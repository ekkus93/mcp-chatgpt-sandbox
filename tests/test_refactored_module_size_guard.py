from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]
_MAX_LINES_EXCLUSIVE = 800
_REFACTORED_MODULES = (
    "src/dev_workspace_mcp/tools/workspace.py",
    "src/dev_workspace_mcp/setup/step_actions.py",
    "src/dev_workspace_mcp/tools/execution.py",
    "src/dev_workspace_mcp/setup/app.py",
)


def test_refactored_coordinators_remain_under_800_lines() -> None:
    for relative_path in _REFACTORED_MODULES:
        path = _REPO_ROOT / relative_path
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count < _MAX_LINES_EXCLUSIVE, (
            f"{relative_path} grew to {line_count} lines; keep refactored coordinators "
            f"under {_MAX_LINES_EXCLUSIVE} lines"
        )
