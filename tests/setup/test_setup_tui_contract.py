"""Static TUI support/exhaustiveness contracts."""

from dev_workspace_mcp.config.models import GitPolicyConfig
from dev_workspace_mcp.setup.app import GIT_POLICY_UI_FIELDS


def test_git_policy_editor_is_exhaustive() -> None:
    assert set(GIT_POLICY_UI_FIELDS) == set(GitPolicyConfig.model_fields)
