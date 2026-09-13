"""Secure MCP Tunnel setup boundaries."""

from __future__ import annotations

from pathlib import Path

from dev_workspace_mcp.setup.tunnel import (
    CONTROL_PLANE_API_KEY_ENV,
    build_tunnel_profile_plan,
)


def test_tunnel_plan_contains_no_runtime_secret(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    plan = build_tunnel_profile_plan(config, "tunnel_example")
    assert plan.tunnel_id == "tunnel_example"
    assert "dev-workspace-mcp serve" in plan.mcp_command
    assert str(config) in plan.mcp_command
    assert CONTROL_PLANE_API_KEY_ENV not in plan.display_command
    assert "sk-" not in plan.display_command
