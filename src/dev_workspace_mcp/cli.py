"""Command-line entrypoint.

Startup follows §73's sequence. Steps 1 (config validation) and 2 (Docker
connectivity, reported not enforced) exist as of S2; step 8 (start
transport) as of S1. Steps 3-7 are added by the tasks that implement the
state database (S3), job reconciliation (S14), Docker reconciliation
(S6), and SSH-agent reconciliation (S23), each inserted into `serve()` in
the order §73 specifies.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from dev_workspace_mcp import __version__
from dev_workspace_mcp.config import ConfigError, docker_available, load_config
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.jobs.reconcile import reconcile_jobs
from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.schema_export import export_schemas
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
        config = load_config(config_path)
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

    # §73 steps 3-4: open the state database, then reconcile job records
    # left non-terminal by an unclean shutdown (§15.4). A short-lived
    # connection, separate from the one `create_server` opens below for
    # its own lifetime — reconciliation is a one-shot startup action, not
    # something later tool calls need this connection for.
    reconcile_db = open_db(config.state_db_path)
    try:
        lost = reconcile_jobs(JobStore(reconcile_db))
    finally:
        reconcile_db.close()
    for job_id, pid_still_alive in lost:
        detail = (
            " (host process may still be running; check and clean up by hand)"
            if pid_still_alive
            else ""
        )
        click.echo(
            f"warning: job {job_id} was not terminal at shutdown; marked lost{detail}", err=True
        )

    # Step 5 (reconcile managed containers, §69) happens lazily on each
    # workspace's next operation (S6), not eagerly here. Step 6 (SSH-agent
    # reconciliation) lands with S23. Step 7 (validate repository roots)
    # is enforced at config-load time (step 1) rather than as a separate
    # pass.

    # §73 step 8: start the MCP transport.
    server = create_server(config)
    run_server(server, transport=transport)


@main.command(name="export-schemas")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the server configuration file (§52).",
)
@click.option(
    "--out",
    "out_path",
    default=Path("schemas/tools.json"),
    show_default=True,
    type=click.Path(path_type=Path),
    help="Where to write the exported tool contract (§33.1).",
)
def export_schemas_command(config_path: Path, out_path: Path) -> None:
    """Export the tool contract to a JSON file.

    Commit the result. CI (§71) fails if a fresh export differs from the
    committed copy — a tool's contract SHALL NOT change silently.
    """
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    server = create_server(config)
    asyncio.run(export_schemas(server, out_path))
    click.echo(f"wrote {out_path}")
