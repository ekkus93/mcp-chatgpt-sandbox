"""Interactive setup support for dev-workspace-mcp.

The setup package deliberately keeps presentation separate from production
configuration semantics. The :mod:`dev_workspace_mcp.config` models and
``load_config()`` remain authoritative; setup modules only guide operators,
plan reviewed changes, and validate them through those production APIs.

Importing this package must not start Textual, Docker, subprocesses, network
activity, or filesystem mutation.
"""

from dev_workspace_mcp.setup.config_ownership import (
    CONFIG_FIELD_OWNERSHIP,
    NESTED_CONFIG_FIELD_OWNERSHIP,
    ConfigDisposition,
    validate_config_ownership,
)

__all__ = [
    "CONFIG_FIELD_OWNERSHIP",
    "NESTED_CONFIG_FIELD_OWNERSHIP",
    "ConfigDisposition",
    "validate_config_ownership",
]
