"""Interactive setup support for dev-workspace-mcp.

The setup package is an operator-facing orchestration layer. Production
configuration models and :func:`dev_workspace_mcp.config.load_config` remain
the authority for configuration semantics; setup code may collect, review,
and safely persist choices, but must not invent a second configuration model.
"""

from dev_workspace_mcp.setup.model import SetupState, SetupStep, SetupStepStatus

__all__ = ["SetupState", "SetupStep", "SetupStepStatus"]
