"""Explicit configuration ownership decisions for the setup wizard.

Every top-level ``ServerConfig`` field must be classified here.  The wizard
may only replace fields the operator actually reviewed in the current
session; valid existing values outside that set are preserved verbatim by the
config planner.  ``filesystem`` and ``execution`` are deliberately advanced
runtime sections and therefore are not touched by the normal guided flow.

This module describes ownership only.  ``ServerConfig`` and ``load_config``
remain authoritative for field types, defaults, cross-field safety rules, and
production validity.
"""

from __future__ import annotations

from collections.abc import Iterable

from dev_workspace_mcp.config import ServerConfig

WIZARD_GUIDED_TOP_LEVEL_FIELDS = frozenset(
    {
        "version",
        "state_db_path",
        "repository_roots",
        "repositories",
        "docker",
        "workspace",
        "container",
        "ssh",
        "github",
        "git_policy",
        "resources",
        "audit",
        "logging",
    }
)

WIZARD_ADVANCED_TOP_LEVEL_FIELDS = frozenset({"filesystem", "execution"})

WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS = (
    WIZARD_GUIDED_TOP_LEVEL_FIELDS | WIZARD_ADVANCED_TOP_LEVEL_FIELDS
)


def unclassified_server_config_fields() -> frozenset[str]:
    """Return newly-added production fields lacking an explicit setup decision."""

    return frozenset(ServerConfig.model_fields) - WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS


def managed_fields_for_session(*, touched_fields: Iterable[str]) -> frozenset[str]:
    """Return reviewed fields the config writer may modify in this session.

    A field being *wizard-owned* does not mean it is overwritten merely by
    opening the wizard.  Existing production-valid values are preserved until
    the operator actually visits/changes the corresponding section and the
    review step includes that field.
    """

    touched = frozenset(touched_fields)
    unknown = touched - WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"setup session contains unclassified config fields: {names}")
    return touched
