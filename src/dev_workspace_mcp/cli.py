"""Command-line entrypoint.

Startup follows §73's sequence. Only step 1 (partial — existence, not full
validation) and step 7 (start transport) exist yet; steps 2-6 are added by
the tasks that implement configuration validation (S2), the state database
(S3), Docker reconciliation (S6), and SSH-agent reconciliation (S23), each
inserted into `serve()` in the order §73 specifies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dev_workspace_mcp import __version__
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
    # §73 step 1 (partial): configuration existence. Full schema validation
    # is S2's responsibility and replaces this check when it lands.
    if not config_path.exists():
        click.echo(f"error: config file not found: {config_path}", err=True)
        sys.exit(1)
    if not config_path.is_file():
        click.echo(f"error: config path is not a file: {config_path}", err=True)
        sys.exit(1)

    # §73 step 7: start the MCP transport.
    server = create_server()
    run_server(server, transport=transport)
