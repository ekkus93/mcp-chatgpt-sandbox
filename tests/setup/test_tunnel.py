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


def test_swf_004_runtime_environment_preserves_ambient_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("PATH", "/ambient/bin")
    monkeypatch.setenv("AMBIENT_ONLY", "keep-me")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "tunnel-client")

    def fake_run(
        argv: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
        env: dict[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, timeout
        assert argv[0] == "tunnel-client"
        assert env is not None
        captured.update(env)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    apply_tunnel_profile(
        _plan(tmp_path),
        approved=True,
        runtime_environment={"RUNTIME_ONLY": "supplied"},
    )

    assert captured["PATH"] == "/ambient/bin"
    assert captured["AMBIENT_ONLY"] == "keep-me"
    assert captured["RUNTIME_ONLY"] == "supplied"


def test_swf_004_runtime_environment_overrides_ambient_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("PATH", "/ambient/bin")
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "tunnel-client")

    def fake_run(
        argv: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
        env: dict[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, timeout
        assert env is not None
        captured.update(env)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    apply_tunnel_profile(
        _plan(tmp_path),
        approved=True,
        runtime_environment={"PATH": "/override/bin"},
    )

    assert captured["PATH"] == "/override/bin"


def test_swf_004_tunnel_failure_message_does_not_echo_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.tunnel_client_path", lambda: "tunnel-client")

    def fake_run(
        argv: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
        env: dict[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, timeout, env
        return subprocess.CompletedProcess(argv, 1, stderr="runtime-secret-value")

    monkeypatch.setattr("dev_workspace_mcp.setup.tunnel.subprocess.run", fake_run)

    with pytest.raises(SetupTunnelError) as exc_info:
        apply_tunnel_profile(
            _plan(tmp_path),
            approved=True,
            runtime_environment={"RUNTIME_KEY": "runtime-secret-value"},
        )

    assert "runtime-secret-value" not in str(exc_info.value)
