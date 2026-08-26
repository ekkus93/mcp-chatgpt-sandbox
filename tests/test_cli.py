"""S1: `serve --config <path>` CLI shape per §73."""

from pathlib import Path

from click.testing import CliRunner

from dev_workspace_mcp.cli import main


def test_serve_requires_config_option() -> None:
    result = CliRunner().invoke(main, ["serve"])
    assert result.exit_code != 0
    assert "--config" in result.output


def test_serve_rejects_missing_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yml"
    result = CliRunner().invoke(main, ["serve", "--config", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_serve_rejects_directory_as_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["serve", "--config", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a file" in result.output


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "dev-workspace-mcp" in result.output
