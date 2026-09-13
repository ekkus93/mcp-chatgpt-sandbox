from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from dev_workspace_mcp.entrypoint import main
from dev_workspace_mcp.setup.diagnostics import (
    DiagnosticStatus,
    DiagnosticsReport,
    format_diagnostics,
)
from dev_workspace_mcp.setup.tunnel import (
    SetupTunnelError,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
    check_tunnel_profile,
    open_setup_documentation,
)


def test_wizv_014_profile_plan_contains_exact_server_command_without_secret(tmp_path: Path) -> None:
    plan = build_tunnel_profile_plan(
        profile="chatgpt-sandbox",
        tunnel_id="tunnel-example",
        config_path=tmp_path / "config.yml",
        project_root=tmp_path / "project",
    )
    assert "dev-workspace-mcp serve --config" in plan.server_command
    assert "tunnel-example" in plan.argv
    assert all("secret" not in argument.casefold() for argument in plan.argv)
    with pytest.raises(SetupTunnelError, match="approval"):
        apply_tunnel_profile(plan, approved=False)


def test_wizv_014_no_browser_never_launches(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url) or True)
    assert not open_setup_documentation(
        "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels",
        no_browser=True,
        approved=True,
    )
    assert opened == []


def test_wizv_014_doctor_classifies_external_profile_without_exposing_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/tunnel-client")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "runtime-secret", "oops"),
    )
    health = check_tunnel_profile("chatgpt-sandbox")
    assert not health.healthy
    assert "runtime-secret" not in health.message


def test_wizv_015_report_finish_gate_and_rendering() -> None:
    from dev_workspace_mcp.setup.diagnostics import DiagnosticCode, DiagnosticResult

    report = DiagnosticsReport(
        (
            DiagnosticResult(
                DiagnosticCode.CONFIG_RELOAD,
                "config",
                DiagnosticStatus.PASS,
                "ok",
            ),
            DiagnosticResult(
                DiagnosticCode.TUNNEL_PROFILE,
                "tunnel",
                DiagnosticStatus.SKIPPED,
                "optional",
                mandatory=False,
            ),
        )
    )
    assert report.ready_to_finish
    assert report.exit_code == 0
    rendered = format_diagnostics(report)
    assert "PASS" in rendered
    assert "SKIPPED" in rendered


def test_wizv_015_doctor_alias_and_setup_check_share_cli_contract(tmp_path: Path) -> None:
    config = tmp_path / "missing.yml"
    runner = CliRunner()
    setup_result = runner.invoke(main, ["setup", "--check", "--config", str(config)])
    doctor_result = runner.invoke(main, ["doctor", "--config", str(config)])
    assert setup_result.exit_code == doctor_result.exit_code == 2
    assert "config.reload" in setup_result.output
    assert setup_result.output == doctor_result.output


def test_wizv_015_config_mode_check_is_owner_only(tmp_path: Path) -> None:
    from dev_workspace_mcp.setup.diagnostics import _config_safety

    path = tmp_path / "config.yml"
    path.write_text("version: 1\n")
    path.chmod(0o644)
    result = _config_safety(path)
    assert result.status is DiagnosticStatus.ACTION_REQUIRED
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    result = _config_safety(path)
    assert result.status is DiagnosticStatus.PASS
