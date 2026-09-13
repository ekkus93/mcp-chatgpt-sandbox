from __future__ import annotations

import pytest

from dev_workspace_mcp.config import ServerConfig
from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    GithubConfig,
    GitPolicyConfig,
)
from dev_workspace_mcp.setup.ownership import (
    NESTED_FIELD_DISPOSITIONS,
    TOP_LEVEL_FIELD_DISPOSITIONS,
    WIZARD_ADVANCED_TOP_LEVEL_FIELDS,
    WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS,
    WIZARD_GUIDED_TOP_LEVEL_FIELDS,
    SetupFieldDisposition,
    managed_fields_for_session,
    ownership_drift,
    unclassified_server_config_fields,
    validate_ownership_manifests,
)


def test_wizv_001_every_server_config_field_has_explicit_setup_ownership() -> None:
    assert unclassified_server_config_fields() == frozenset()
    assert frozenset(ServerConfig.model_fields) == WIZARD_CLASSIFIED_TOP_LEVEL_FIELDS
    assert set(TOP_LEVEL_FIELD_DISPOSITIONS) == set(ServerConfig.model_fields)
    assert ownership_drift() == ()
    validate_ownership_manifests()


def test_wcr_001_every_nested_manifest_is_exhaustive() -> None:
    for model, dispositions in NESTED_FIELD_DISPOSITIONS.items():
        assert set(dispositions) == set(model.model_fields), model.__name__
        assert all(isinstance(value, SetupFieldDisposition) for value in dispositions.values())


def test_wcr_001_preserved_nested_fields_are_truthful() -> None:
    assert NESTED_FIELD_DISPOSITIONS[ContainerConfig]["sudo"] is SetupFieldDisposition.PRESERVED
    assert (
        NESTED_FIELD_DISPOSITIONS[GithubConfig]["repositories"] is SetupFieldDisposition.PRESERVED
    )
    assert NESTED_FIELD_DISPOSITIONS[AuditConfig]["enabled"] is SetupFieldDisposition.PRESERVED


def test_wcr_008_git_policy_ownership_is_exhaustive_and_managed() -> None:
    dispositions = NESTED_FIELD_DISPOSITIONS[GitPolicyConfig]
    assert set(dispositions) == set(GitPolicyConfig.model_fields)
    assert set(dispositions.values()) == {SetupFieldDisposition.MANAGED}


def test_wizv_001_advanced_runtime_sections_are_not_guided_by_default() -> None:
    assert {"filesystem", "execution"} == WIZARD_ADVANCED_TOP_LEVEL_FIELDS
    assert not (WIZARD_ADVANCED_TOP_LEVEL_FIELDS & WIZARD_GUIDED_TOP_LEVEL_FIELDS)


def test_wizv_001_session_only_manages_fields_operator_actually_reviewed() -> None:
    assert managed_fields_for_session(touched_fields={"docker", "ssh"}) == {"docker", "ssh"}
    with pytest.raises(ValueError, match="unclassified"):
        managed_fields_for_session(touched_fields={"future_field"})
