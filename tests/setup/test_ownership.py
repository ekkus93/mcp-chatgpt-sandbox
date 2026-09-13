from __future__ import annotations

import pytest

from dev_workspace_mcp.config import ServerConfig
from dev_workspace_mcp.setup.ownership import (
    WIZARD_ADVANCED_TOP_LEVEL_FIELDS,
    WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS,
    WIZARD_GUIDED_TOP_LEVEL_FIELDS,
    managed_fields_for_session,
    unclassified_server_config_fields,
)


def test_wizv_001_every_server_config_field_has_explicit_setup_ownership() -> None:
    assert unclassified_server_config_fields() == frozenset()
    assert frozenset(ServerConfig.model_fields) == WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS


def test_wizv_001_advanced_runtime_sections_are_not_guided_by_default() -> None:
    assert {"filesystem", "execution"} == WIZARD_ADVANCED_TOP_LEVEL_FIELDS
    assert not (WIZARD_ADVANCED_TOP_LEVEL_FIELDS & WIZARD_GUIDED_TOP_LEVEL_FIELDS)


def test_wizv_001_session_only_manages_fields_operator_actually_reviewed() -> None:
    assert managed_fields_for_session(touched_fields={"docker", "ssh"}) == {"docker", "ssh"}
    with pytest.raises(ValueError, match="unclassified"):
        managed_fields_for_session(touched_fields={"future_field"})
