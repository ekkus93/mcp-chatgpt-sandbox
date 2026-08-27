"""S30: `.mcp-workspace.yml` parsing (§9, §10)."""

from pathlib import Path

import pytest
import yaml

from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.project_config import load_project_config


def _write(repo_path: Path, data: dict, filename: str = ".mcp-workspace.yml") -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / filename).write_text(yaml.safe_dump(data))


def test_returns_none_when_no_project_config_file_exists(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    assert load_project_config(repo_path) is None


def test_parses_the_full_documented_example(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(
        repo_path,
        {
            "version": 1,
            "image": "ubuntu:24.04",
            "workspace": "/workspace",
            "setup": [
                "sudo apt-get update",
                "sudo apt-get install -y build-essential curl git pkg-config",
            ],
            "environment": {"RUST_BACKTRACE": "1"},
            "caches": [
                {"path": "$HOME/.cargo", "name": "cargo"},
                {"path": "$HOME/.rustup", "name": "rustup"},
            ],
            "github": {"repository": "ekkus93/example-project"},
            "validation": {
                "format": ["cargo fmt --check"],
                "lint": ["cargo clippy --all-targets --all-features -- -D warnings"],
                "test": ["cargo test --all-features"],
            },
        },
    )

    config = load_project_config(repo_path)

    assert config is not None
    assert config.version == 1
    assert config.image == "ubuntu:24.04"
    assert config.setup == [
        "sudo apt-get update",
        "sudo apt-get install -y build-essential curl git pkg-config",
    ]
    assert config.environment == {"RUST_BACKTRACE": "1"}
    assert [c.name for c in config.caches] == ["cargo", "rustup"]
    assert config.github.repository == "ekkus93/example-project"
    assert config.validation.test == ["cargo test --all-features"]


def test_minimal_config_only_requires_version(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1})
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image is None
    assert config.setup == []
    assert config.caches == []


def test_yaml_extension_is_also_accepted(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "image": "alpine"}, filename=".mcp-workspace.yaml")
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image == "alpine"


def test_unknown_top_level_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "not_a_real_field": True})
    with pytest.raises(ContainerCreateFailedError, match="invalid project configuration"):
        load_project_config(repo_path)


def test_missing_required_version_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"image": "alpine"})
    with pytest.raises(ContainerCreateFailedError, match="invalid project configuration"):
        load_project_config(repo_path)


def test_unknown_cache_entry_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(
        repo_path,
        {"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo", "extra": "x"}]},
    )
    with pytest.raises(ContainerCreateFailedError):
        load_project_config(repo_path)


def test_invalid_yaml_is_a_clear_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".mcp-workspace.yml").write_text("not: valid: yaml: [unterminated")
    with pytest.raises(ContainerCreateFailedError, match="not valid YAML"):
        load_project_config(repo_path)


def test_non_mapping_top_level_is_a_clear_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".mcp-workspace.yml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ContainerCreateFailedError, match="must be a mapping"):
        load_project_config(repo_path)


def test_mcp_workspace_yml_takes_precedence_over_yaml_extension(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "image": "yml-wins"}, filename=".mcp-workspace.yml")
    _write(repo_path, {"version": 1, "image": "yaml-loses"}, filename=".mcp-workspace.yaml")
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image == "yml-wins"
