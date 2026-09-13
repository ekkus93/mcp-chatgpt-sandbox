from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from dev_workspace_mcp.config import ServerConfig
from dev_workspace_mcp.entrypoint import main
from dev_workspace_mcp.setup.diagnostics import (
    DiagnosticsReport,
    DiagnosticStatus,
    format_diagnostics,
    run_diagnostics,
)
from dev_workspace_mcp.setup.tunnel import (
    SetupTunnelError,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
    check_tunnel_profile,
    open_setup_documentation,
)


def test_wizv_014_profile_plan_contains_exact_server_command_without_secret(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config dir" / "config.yml"
    project_root = tmp_path / "project dir"
    plan = build_tunnel_profile_plan(
        profile="chatgpt-sandbox",
        tunnel_id="tunnel-example",
        config_path=config_path,
        project_root=project_root,
    )
    expected_argv = [
        "uv",
        "run",
        "--project",
        str(project_root.resolve(strict=False)),
        "dev-workspace-mcp",
        "serve",
        "--config",
        str(config_path.resolve(strict=False)),
    ]
    assert shlex.split(plan.server_command) == expected_argv
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

    blocked = DiagnosticsReport(
        (
            DiagnosticResult(
                DiagnosticCode.CONFIG_RELOAD,
                "config",
                DiagnosticStatus.PASS,
                "ok",
            ),
            DiagnosticResult(
                DiagnosticCode.DOCKER_DAEMON,
                "docker",
                DiagnosticStatus.ACTION_REQUIRED,
                "operator action needed",
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
    assert not blocked.ready_to_finish
    assert blocked.exit_code == 1


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


def test_swhv_002_audit_path_rejects_symlink_and_directory_targets(
    tmp_path: Path,
) -> None:
    from dev_workspace_mcp.setup.diagnostics import _audit_check

    missing = tmp_path / "missing-audit.jsonl"
    broken_link = tmp_path / "broken-audit"
    broken_link.symlink_to(missing)
    assert _audit_check(broken_link).status is DiagnosticStatus.FAIL

    real = tmp_path / "real-audit.jsonl"
    real.write_text("{}\n")
    link = tmp_path / "audit-link"
    link.symlink_to(real)
    assert _audit_check(link).status is DiagnosticStatus.FAIL

    directory = tmp_path / "audit-directory"
    directory.mkdir()
    assert _audit_check(directory).status is DiagnosticStatus.FAIL

    absent = tmp_path / "new-audit.jsonl"
    assert _audit_check(absent).status is DiagnosticStatus.PASS


def test_swhv_002_state_db_path_rejects_symlink_targets(tmp_path: Path) -> None:
    from dev_workspace_mcp.setup.diagnostics import _state_db_check

    real = tmp_path / "state.db"
    real.write_text("not-a-real-db")
    link = tmp_path / "state-link.db"
    link.symlink_to(real)

    assert _state_db_check(link).status is DiagnosticStatus.FAIL
    assert _state_db_check(tmp_path / "absent-state.db").status is DiagnosticStatus.PASS


def test_wizv_018_docker_unavailable_is_action_required_not_false_pass(
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dev_workspace_mcp.setup.diagnostics import _docker_checks

    def unavailable() -> object:
        raise OSError("simulated docker outage")

    monkeypatch.setattr("dev_workspace_mcp.setup.diagnostics.docker.from_env", unavailable)
    daemon, image = _docker_checks(minimal_config)
    assert daemon.status is DiagnosticStatus.ACTION_REQUIRED
    assert image.status is DiagnosticStatus.SKIPPED


def test_wizv_015_unexpected_diagnostic_exception_is_secret_safe_and_fail_closed(
    tmp_path: Path,
    minimal_config: ServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dev_workspace_mcp.setup.diagnostics import DiagnosticCode

    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(minimal_config.model_dump(mode="json"), sort_keys=False))
    path.chmod(0o600)

    def unexpected(_: ServerConfig) -> tuple[()]:
        raise RuntimeError("sensitive-diagnostic-sentinel")

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.diagnostics._repository_checks",
        unexpected,
    )
    report = run_diagnostics(path)
    failures = [
        result
        for result in report.results
        if result.code is DiagnosticCode.SERVER_CONSTRUCTION
        and result.status is DiagnosticStatus.FAIL
    ]
    assert failures
    assert not report.ready_to_finish
    assert all("sensitive-diagnostic-sentinel" not in result.message for result in failures)
