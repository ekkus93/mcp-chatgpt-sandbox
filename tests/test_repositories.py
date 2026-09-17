"""S5: repository registry — resolution, authorization, and discovery
(§7, §7.1, FR-001, FR-002, AT-022)."""

from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.errors import RepositoryNotAllowedError
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry


def _make_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()


def test_configured_repository_resolves(minimal_config: ServerConfig, tmp_path: Path) -> None:
    repo_path = tmp_path / "work" / "example"
    _make_git_repo(repo_path)
    config = minimal_config.model_copy(
        update={
            "repositories": [RepositoryConfig(id="example", path=repo_path, github="owner/example")]
        }
    )
    registry = RepositoryRegistry(config)
    record = registry.get("example")
    assert record.canonical_path == repo_path.resolve()
    assert record.github == "owner/example"


def test_unauthorized_repository_id_fails_closed(minimal_config: ServerConfig) -> None:
    registry = RepositoryRegistry(minimal_config)
    with pytest.raises(RepositoryNotAllowedError) as excinfo:
        registry.get("does-not-exist")
    assert excinfo.value.recoverable is False
    assert excinfo.value.details == {"repository_id": "does-not-exist"}


def test_list_all_is_sorted_by_id(minimal_config: ServerConfig, tmp_path: Path) -> None:
    b_path = tmp_path / "work" / "b"
    a_path = tmp_path / "work" / "a"
    for p in (b_path, a_path):
        _make_git_repo(p)
    config = minimal_config.model_copy(
        update={
            "repositories": [
                RepositoryConfig(id="b-repo", path=b_path, github="owner/b"),
                RepositoryConfig(id="a-repo", path=a_path, github="owner/a"),
            ]
        }
    )
    registry = RepositoryRegistry(config)
    assert [r.id for r in registry.list_all()] == ["a-repo", "b-repo"]


# --- discovery (§7.1) -------------------------------------------------------------


def test_discovery_finds_immediate_git_subdirectories(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    root = tmp_path / "work"
    _make_git_repo(root / "project-a")
    _make_git_repo(root / "project_B")  # exercises slugification
    config = minimal_config.model_copy(update={"repository_roots": [root]})
    registry = RepositoryRegistry(config)

    ids = {r.id for r in registry.list_all()}
    assert ids == {"project-a", "project-b"}
    discovered = registry.get("project-a")
    assert discovered.canonical_path == (root / "project-a").resolve()
    assert discovered.github is None  # unknown without reading Git state


def test_discovery_skips_non_git_directories(minimal_config: ServerConfig, tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir(parents=True)
    (root / "not-a-repo").mkdir()
    config = minimal_config.model_copy(update={"repository_roots": [root]})
    registry = RepositoryRegistry(config)
    assert registry.list_all() == []


def test_discovery_skips_plain_files_at_root(minimal_config: ServerConfig, tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir(parents=True)
    (root / "README.md").write_text("not a directory")
    config = minimal_config.model_copy(update={"repository_roots": [root]})
    registry = RepositoryRegistry(config)
    assert registry.list_all() == []


def test_discovery_ignores_a_root_that_does_not_exist(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config = minimal_config.model_copy(update={"repository_roots": [tmp_path / "missing"]})
    registry = RepositoryRegistry(config)
    assert registry.list_all() == []


def test_explicit_config_wins_over_discovery_on_id_collision(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    root = tmp_path / "work"
    configured_path = tmp_path / "elsewhere" / "example"
    discovered_path = root / "example"
    _make_git_repo(configured_path)
    _make_git_repo(discovered_path)

    config = minimal_config.model_copy(
        update={
            "repository_roots": [root],
            "repositories": [
                RepositoryConfig(id="example", path=configured_path, github="owner/configured")
            ],
        }
    )
    registry = RepositoryRegistry(config)
    record = registry.get("example")
    assert record.canonical_path == configured_path.resolve()
    assert record.github == "owner/configured"


def test_at_022_symlink_escape_is_not_treated_as_authorized(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """A repository symlink beneath an allowed root, pointing outside it,
    SHALL NOT be treated as an authorized repository (§7.1, AT-022)."""
    root = tmp_path / "work"
    root.mkdir(parents=True)
    outside = tmp_path / "outside-the-root"
    _make_git_repo(outside)
    (root / "escape").symlink_to(outside, target_is_directory=True)

    config = minimal_config.model_copy(update={"repository_roots": [root]})
    registry = RepositoryRegistry(config)

    assert registry.list_all() == []
    with pytest.raises(RepositoryNotAllowedError):
        registry.get("escape")


def test_discovery_accepts_a_symlink_that_stays_under_the_root(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """Only escaping symlinks are rejected — one that still resolves under
    the root is an ordinary repository."""
    root = tmp_path / "work"
    real = root / "real-project"
    _make_git_repo(real)
    (root / "alias").symlink_to(real, target_is_directory=True)

    config = minimal_config.model_copy(update={"repository_roots": [root]})
    registry = RepositoryRegistry(config)

    ids = {r.id for r in registry.list_all()}
    assert ids == {"real-project", "alias"}
