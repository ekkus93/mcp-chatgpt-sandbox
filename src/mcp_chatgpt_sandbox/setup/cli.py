"""Click integration for interactive setup and shared diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from mcp_chatgpt_sandbox.setup.app import run_setup_app
from mcp_chatgpt_sandbox.setup.diagnostics import run_check_cli

DEFAULT_CONFIG_PATH = Path("~/.config/mcp-chatgpt-sandbox/config.yml")


def _config_option[F: Callable[..., Any]](function: F) -> F:
    decorator = click.option(
        "--config",
        "config_path",
        type=click.Path(path_type=Path),
        default=DEFAULT_CONFIG_PATH,
        show_default=True,
        help="Configuration file to create, repair, edit, or diagnose.",
    )
    return decorator(function)


@click.command(name="setup")
@_config_option
@click.option("--resume", is_flag=True, help="Resume valid incomplete non-secret setup state.")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Run setup diagnostics without launching the interactive wizard.",
)
@click.option("--no-browser", is_flag=True, help="Never launch external browser URLs.")
@click.option("--advanced", is_flag=True, help="Expose advanced runtime controls immediately.")
def setup_command(
    config_path: Path,
    resume: bool,
    check_only: bool,
    no_browser: bool,
    advanced: bool,
) -> None:
    """Guide configuration of a development-workspace MCP server."""

    if check_only:
        raise click.exceptions.Exit(run_check_cli(config_path.expanduser()))

    code = run_setup_app(
        config_path=config_path.expanduser(),
        resume=resume,
        no_browser=no_browser,
        advanced=advanced,
    )
    if code:
        raise click.exceptions.Exit(code)


@click.command(name="doctor")
@_config_option
def doctor_command(config_path: Path) -> None:
    """Run the same machine-verifiable diagnostics used by setup --check."""

    raise click.exceptions.Exit(run_check_cli(config_path.expanduser()))
