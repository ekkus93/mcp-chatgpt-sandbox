"""Installed CLI composition.

The historical operator commands remain implemented by ``cli.py``. Setup and
its diagnostics alias are registered here so the installed console-script name
remains stable while the setup package stays isolated from daemon/runtime code.
"""

from mcp_chatgpt_sandbox.cli import main
from mcp_chatgpt_sandbox.setup.cli import doctor_command, setup_command

main.add_command(setup_command)
main.add_command(doctor_command)

__all__ = ["main"]
