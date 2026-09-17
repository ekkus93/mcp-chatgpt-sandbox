"""Interactive setup support for mcp-chatgpt-sandbox.

The setup package is an operator-facing orchestration layer. Production
configuration models and :func:`mcp_chatgpt_sandbox.config.load_config` remain
the authority for configuration semantics; setup code may collect, review,
and safely persist choices, but must not invent a second configuration model.
"""

from mcp_chatgpt_sandbox.setup.model import SetupState, SetupStep, SetupStepStatus

__all__ = ["SetupState", "SetupStep", "SetupStepStatus"]
