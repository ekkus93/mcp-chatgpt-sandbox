"""Shared setup diagnostics interface.

WIZ-015 expands this into the complete machine-verifiable matrix.  The CLI
contract exists from WIZ-006 so ``setup --check`` never needs a second command
shape.
"""

from __future__ import annotations

from pathlib import Path

import click

from dev_workspace_mcp.config import ConfigError, docker_available, load_config


def run_check_cli(config_path: Path) -> int:
    """Run the initial production-config/Docker setup checks."""

    try:
        load_config(config_path)
    except ConfigError as exc:
        click.echo(f"FAIL config: {exc}")
        return 2
    click.echo("PASS config: production configuration reload succeeded")
    if docker_available():
        click.echo("PASS docker: daemon reachable")
        return 0
    click.echo("ACTION REQUIRED docker: daemon is not reachable")
    return 1
