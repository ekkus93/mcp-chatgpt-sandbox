"""Installed CLI composition.

The historical operator commands remain implemented by ``cli.py``.  Setup is
registered here so the installed console-script name remains stable while the
interactive setup package stays isolated from daemon/runtime code.
"""

from dev_workspace_mcp.cli import main
from dev_workspace_mcp.setup.cli import setup_command

main.add_command(setup_command)

__all__ = ["main"]
