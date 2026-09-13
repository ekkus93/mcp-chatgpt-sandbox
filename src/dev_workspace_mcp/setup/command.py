"""Click adapter for interactive setup and headless setup diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dev_workspace_mcp.setup.controller import (
    DEFAULT_CONFIG_PATH,
    SetupController,
    SetupControllerError,
)
from dev_workspace_mcp.setup.diagnostics import SetupDiagnosticsReport, run_setup_diagnostics


def _resolved_config_path(config_path: Path | None) -> Path:
    return (config_path or DEFAULT_CONFIG_PATH).expanduser().absolute()


def _render_diagnostics(report: SetupDiagnosticsReport) -> None:
    for item in report.diagnostics:
        click.echo(
            f"{item.status.value.upper():15} {item.code:28} {item.message}",
            err=not report.valid,
        )
        if item.remediation is not None:
            click.echo(f"  remediation: {item.remediation}", err=not report.valid)


@click.command(name="setup")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    help="Target server configuration path; uses the documented operator default when omitted.",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Resume an incomplete non-secret setup state.",
)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Run shared setup diagnostics without opening the interactive TUI.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Never launch external URLs; display them for manual use instead.",
)
@click.option(
    "--advanced",
    is_flag=True,
    help="Expose advanced runtime/resource controls immediately.",
)
def setup_command(
    config_path: Path | None,
    resume: bool,
    check_only: bool,
    no_browser: bool,
    advanced: bool,
) -> None:
    """Configure or validate dev-workspace-mcp interactively."""

    target = _resolved_config_path(config_path)
    if check_only:
        report = run_setup_diagnostics(target)
        _render_diagnostics(report)
        if not report.valid:
            raise click.exceptions.Exit(1)
        return

    try:
        controller = SetupController.create(
            config_path=target,
            resume=resume,
            advanced=advanced,
            no_browser=no_browser,
        )
    except SetupControllerError as exc:
        click.echo(f"error: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc

    try:
        from dev_workspace_mcp.setup.app import run_setup_app

        exit_code = run_setup_app(controller)
    except SetupControllerError as exc:
        click.echo(f"error: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    except Exception as exc:
        click.echo("error: interactive setup failed unexpectedly", err=True)
        raise click.exceptions.Exit(2) from exc

    if exit_code:
        sys.exit(exit_code)


__all__ = ["setup_command"]
