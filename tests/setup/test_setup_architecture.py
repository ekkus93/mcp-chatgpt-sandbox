"""Architecture contracts for the interactive setup package."""

from pathlib import Path

from dev_workspace_mcp.config.models import GitPolicyConfig, ServerConfig
from dev_workspace_mcp.setup.config_ownership import (
    CONFIG_FIELD_OWNERSHIP,
    NESTED_CONFIG_FIELD_OWNERSHIP,
    ConfigDisposition,
    validate_config_ownership,
)
from dev_workspace_mcp.setup.model import SetupRepository
from dev_workspace_mcp.setup.steps.views import StepViewBuilder

_SRC_ROOT = Path(__file__).parents[2] / "src" / "dev_workspace_mcp"
_SETUP_ROOT = _SRC_ROOT / "setup"


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
    # WIZ-001/WCR-001 intentionally keep the package root presentation-free.
    import dev_workspace_mcp.setup as setup

    assert setup.ConfigDisposition is ConfigDisposition


def test_textual_shell_cannot_bypass_reviewed_config_writer() -> None:
    source = (_SETUP_ROOT / "app.py").read_text(encoding="utf-8")
    forbidden = (
        "atomic_write_text",
        "commit_config_update",
        "yaml.safe_dump",
        "yaml.safe_load",
        "Path.write_text",
        ".write_bytes(",
    )
    assert all(token not in source for token in forbidden)
    assert "write_reviewed_configuration" in source


def test_step_views_are_presentation_only() -> None:
    source = (_SETUP_ROOT / "steps" / "views.py").read_text(encoding="utf-8")
    forbidden = (
        "import yaml",
        "config_writer",
        "atomic_write_text",
        "subprocess.",
        "docker.from_env",
        "urllib.request",
    )
    assert all(token not in source for token in forbidden)


def test_production_config_package_does_not_depend_on_setup() -> None:
    config_root = _SRC_ROOT / "config"
    for path in config_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "dev_workspace_mcp.setup" not in source, path


def test_setup_coordinators_have_growth_guards() -> None:
    # These are deliberately generous tripwires, not style targets. Crossing
    # them requires an explicit architecture decision instead of silently
    # rebuilding the pre-remediation monolith.
    app_lines = (_SETUP_ROOT / "app.py").read_text(encoding="utf-8").splitlines()
    view_lines = (_SETUP_ROOT / "steps" / "views.py").read_text(encoding="utf-8").splitlines()
    assert len(app_lines) <= 700
    assert len(view_lines) <= 400


def test_step_view_logic_is_testable_without_running_textual_app() -> None:
    repositories = [
        SetupRepository(
            id="example",
            path="/workspace/example",
            github="owner/example",
        )
    ]
    builder = StepViewBuilder(
        controller=object(),  # type: ignore[arg-type]
        field=lambda *_args, **_kwargs: [],
        repositories=repositories,
        repository_roots=[],
        advanced_visible=False,
    )

    assert builder.repository_list_text() == "example: /workspace/example -> owner/example"
