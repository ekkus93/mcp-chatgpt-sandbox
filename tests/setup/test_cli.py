from __future__ import annotations

from click.testing import CliRunner

from dev_workspace_mcp.entrypoint import main


def test_wizv_001_installed_cli_composition_preserves_existing_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "export-schemas" in result.output
    assert "setup" in result.output


def test_wizv_006_setup_help_exposes_lifecycle_options() -> None:
    result = CliRunner().invoke(main, ["setup", "--help"])
    assert result.exit_code == 0
    for option in ("--config", "--resume", "--check", "--no-browser", "--advanced"):
        assert option in result.output
