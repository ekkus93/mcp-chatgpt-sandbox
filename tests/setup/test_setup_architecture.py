"""Architecture contracts for the interactive setup package."""

from dev_workspace_mcp.config.models import GitPolicyConfig, ServerConfig
from dev_workspace_mcp.setup.config_ownership import (
    CONFIG_FIELD_OWNERSHIP,
    NESTED_CONFIG_FIELD_OWNERSHIP,
    ConfigDisposition,
    validate_config_ownership,
)


def test_setup_ownership_is_exhaustive_for_server_config() -> None:
    assert set(CONFIG_FIELD_OWNERSHIP) == set(ServerConfig.model_fields)
    validate_config_ownership()


def test_setup_ownership_is_exhaustive_for_git_policy() -> None:
    ownership = NESTED_CONFIG_FIELD_OWNERSHIP[GitPolicyConfig]
    assert set(ownership) == set(GitPolicyConfig.model_fields)


def test_every_declared_field_has_an_explicit_disposition() -> None:
    manifests = [CONFIG_FIELD_OWNERSHIP, *NESTED_CONFIG_FIELD_OWNERSHIP.values()]
    for manifest in manifests:
        assert manifest
        assert all(isinstance(value, ConfigDisposition) for value in manifest.values())


def test_importing_setup_has_no_textual_side_effect_dependency() -> None:
    # WIZ-001 intentionally keeps the package root presentation-free.  Import
    # consumers can inspect/validate setup architecture without starting a TUI.
    import dev_workspace_mcp.setup as setup

    assert setup.ConfigDisposition is ConfigDisposition
