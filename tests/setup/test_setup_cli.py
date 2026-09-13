from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from dev_workspace_mcp.cli import main
from dev_workspace_mcp.setup.controller import SetupControllerError
from dev_workspace_mcp.setup.diagnostics import (
    DiagnosticResult,
    DiagnosticStatus,
    SetupDiagnosticsReport,
)


def test_setup_command_is_registered() -> None:
    result = CliRunner().invoke(main, ["setup", "--help"])

    assert result.exit_code == 0
    assert "--resume" in result.output
    assert "--check" in result.output
    assert "--no-browser" in result.output
    assert "--advanced" in result.output


def test_setup_check_success_returns_zero(monkeypatch: object, tmp_path: Path) -> None:
    report = SetupDiagnosticsReport(
        (
            DiagnosticResult(
                code="config.reload",
                label="Production configuration",
                status=DiagnosticStatus.PASS,
                message="ok",
            ),
        )
    )
    monkeypatch.setattr(
        "dev_workspace_mcp.setup.command._headless_diagnostics",
        lambda _path: report,
    )

    result = CliRunner().invoke(main, ["setup", "--check", "--config", str(tmp_path / "x.yml")])

    assert result.exit_code == 0
    assert "PASS" in result.output
    assert "config.reload" in result.output


def test_setup_check_unresolved_returns_one(monkeypatch: object, tmp_path: Path) -> None:
    report = SetupDiagnosticsReport(
        (
            DiagnosticResult(
                code="docker.daemon",
                label="Docker daemon",
                status=DiagnosticStatus.ACTION_REQUIRED,
                message="Docker is not reachable",
                remediation="Start Docker.",
            ),
        )
    )
    monkeypatch.setattr(
        "dev_workspace_mcp.setup.command._headless_diagnostics",
        lambda _path: report,
    )

    result = CliRunner().invoke(main, ["setup", "--check", "--config", str(tmp_path / "x.yml")])

    assert result.exit_code == 1
    assert "ACTION_REQUIRED" in result.output
    assert "remediation: Start Docker." in result.output


def test_setup_resume_without_state_returns_fatal_two(monkeypatch: object, tmp_path: Path) -> None:
    def fail_create(**_kwargs: object) -> None:
        raise SetupControllerError("No resumable setup state exists")

    monkeypatch.setattr("dev_workspace_mcp.setup.command.SetupController.create", fail_create)

    result = CliRunner().invoke(
        main,
        ["setup", "--resume", "--config", str(tmp_path / "x.yml")],
    )

    assert result.exit_code == 2
    assert "No resumable setup state exists" in result.output
