"""GAP3 Y8 / G3AT-008: recursive listing prunes excluded work and is bounded."""

import os
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService


def _service(root: Path) -> FilesystemService:
    return FilesystemService(
        FilesystemSecurity(root, authorized_roots=[root.parent]),
        FilesystemConfig(),
    )


def _repo_with_large_git_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    objects = root / ".git" / "objects"
    objects.mkdir(parents=True)
    # Large enough to make accidental descent obvious without making the
    # regression test expensive. G3AT-008 cares about enumerated work, not
    # wall-clock time.
    for index in range(128):
        shard = objects / f"{index:02x}"
        shard.mkdir()
        (shard / "object").write_text("x")
    visible = root / "src"
    visible.mkdir()
    (visible / "main.py").write_text("print('ok')\n")
    return root


def test_g3at_008_recursive_listing_does_not_delegate_descent_to_rglob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The traversal primitive itself must support pruning before descent."""
    root = _repo_with_large_git_tree(tmp_path)
    service = _service(root)

    def _forbidden_rglob(self: Path, pattern: str, **kwargs: object) -> object:
        raise AssertionError("G3AT-008: recursive list_files still delegates descent to Path.rglob")

    monkeypatch.setattr(Path, "rglob", _forbidden_rglob)

    entries = service.list_files("", recursive=True, max_entries=2)

    assert {entry.path for entry in entries} == {"src", "src/main.py"}


def test_g3at_008_git_subtree_is_pruned_before_any_scandir_descent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large excluded .git tree costs one parent candidate, not a walk."""
    root = _repo_with_large_git_tree(tmp_path)
    service = _service(root)
    real_scandir = os.scandir
    scanned_git_dirs: list[Path] = []

    def _counting_scandir(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]):  # type: ignore[no-untyped-def]
        candidate = Path(path)
        try:
            candidate.relative_to(root / ".git")
        except ValueError:
            pass
        else:
            scanned_git_dirs.append(candidate)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", _counting_scandir)

    entries = service.list_files("", recursive=True, max_entries=1000)

    assert {entry.path for entry in entries} == {"src", "src/main.py"}
    assert scanned_git_dirs == []
