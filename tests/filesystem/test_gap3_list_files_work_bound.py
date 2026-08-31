"""GAP3 Y8 / G3AT-008: recursive listing prunes excluded work and is bounded."""

import os
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService


def _service(root: Path, *, config: FilesystemConfig | None = None) -> FilesystemService:
    return FilesystemService(
        FilesystemSecurity(root, authorized_roots=[root.parent]),
        config or FilesystemConfig(),
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

    def _forbidden_rglob(_self: Path, _pattern: str, **_kwargs: object) -> object:
        raise AssertionError("G3AT-008: recursive list_files still delegates descent to Path.rglob")

    monkeypatch.setattr(Path, "rglob", _forbidden_rglob)

    entries = service.list_files("", recursive=True, max_entries=1)

    assert {entry.path for entry in entries} == {"src"}


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


def test_g3at_008_many_skipped_candidates_exhaust_work_budget_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped/vanished candidates consume work without consuming result slots."""
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(10):
        (root / f"candidate-{index}.txt").write_text("x")

    service = _service(
        root,
        config=FilesystemConfig(max_listing_entries=1),
    )
    described = 0

    def _all_vanished(_self: FilesystemService, _path: Path):  # type: ignore[no-untyped-def]
        nonlocal described
        described += 1
        return None

    monkeypatch.setattr(FilesystemService, "_describe", _all_vanished)

    with pytest.raises(InvalidArgumentError) as exc_info:
        service.list_files("", recursive=True, max_entries=1)

    error = exc_info.value
    assert error.code.value == "INVALID_ARGUMENT"
    assert error.details == {
        "path": "",
        "recursive": True,
        "max_entries": 1,
        "entries_collected": 0,
        "candidate_limit": 10,
        "candidates_examined": 10,
    }
    assert described == 10
