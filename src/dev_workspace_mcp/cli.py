"""Command-line entrypoint.

Startup follows §73's sequence. Steps 1 (config validation) and 2 (Docker
connectivity, reported not enforced) exist as of S2; step 7 (start
transport) as of S1. Steps 3-6 are added by the tasks that implement the
state database (S3), Docker reconciliation (S6), and SSH-agent
reconciliation (S23), each inserted into `serve()` in the order §73
specifies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dev_workspace_mcp import __version__
from dev_workspace_mcp.config import ConfigError, docker_available, load_config
from dev_workspace_mcp.server import Transport, create_server, run_server


@click.group()
@click.version_option(version=__version__, prog_name="dev-workspace-mcp")
def main() -> None:
    """Development Workspace MCP Server."""


@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the server configuration file (§52).",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="MCP transport to serve on (§56).",
)
def serve(config_path: Path, transport: Transport) -> None:
    """Start the MCP server."""
    # §73 step 1: validate configuration. Refuse to serve on invalid config.
    try:
        load_config(config_path)
    except ConfigError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    # §73 step 2: verify Docker connectivity. A missing daemon is reported,
    # not fatal — the daemon starts degraded rather than refusing to serve
    # tools that do not need Docker (e.g. reading a stopped workspace's
    # files, once the filesystem service is host-side per §6.3).
    if not docker_available():
        click.echo(
            "warning: Docker is not reachable; starting in a degraded state (§73)",
            err=True,
        )

    # §73 step 7: start the MCP transport.
    server = create_server()
    run_server(server, transport=transport)
