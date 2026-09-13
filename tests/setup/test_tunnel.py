from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from dev_workspace_mcp.setup.tunnel import (
    SetupTunnelError,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
)


def _plan(tmp_path: Path):
    return build_tunnel_profile_plan(
        profile="chatgpt-sandbox",
        tunnel_id="tun_example",
        config_path=tmp_path / "config.yml",
        project_root=tmp_path,
    )


def test_swf_004_tunnel_runtime_environment_none_inherits_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "/bin/true")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    apply_tunnel_profile(_plan(tmp_path), approved=True)

    assert captured["env"] is None


def test_swf_004_tunnel_runtime_environment_merges_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setenv("SWF_AMBIENT", "ambient")
    monkeypatch.setenv("SWF_OVERRIDE", "ambient")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "/bin/true")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    apply_tunnel_profile(
        _plan(tmp_path),
        approved=True,
        runtime_environment={"SWF_OVERRIDE": "runtime", "SWF_RUNTIME_ONLY": "runtime"},
    )

    assert captured["env"]["SWF_AMBIENT"] == "ambient"
    assert captured["env"]["SWF_OVERRIDE"] == "runtime"
    assert captured["env"]["SWF_RUNTIME_ONLY"] == "runtime"


def test_swf_004_tunnel_profile_failure_message_is_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(args[0], 1, "raw-secret-stdout", "raw-secret-stderr")

    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "/bin/true")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    with pytest.raises(SetupTunnelError) as exc_info:
        apply_tunnel_profile(
            _plan(tmp_path),
            approved=True,
            runtime_environment={"CONTROL_PLANE_API_KEY": "raw-secret-env"},
        )

    message = str(exc_info.value)
    assert "raw-secret" not in message
    assert "CONTROL_PLANE_API_KEY" not in message
