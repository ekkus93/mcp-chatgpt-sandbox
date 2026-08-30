"""S10: `list_files` (§13.1, §47) — including HR-012's escaping-symlink
protection and RR-007's enumeration-survives-a-bad-entry coverage.

Extracted from `test_service.py`, which grew past 800 lines covering all
four filesystem primitives (list/read/write/delete) in one file — split by
operation into `test_service_list.py` (this file), `test_service_read.py`,
`test_service_write.py`, and `test_service_delete.py`, the same way
`tools/git.py` and `tests/workspace/test_git_commit_tool.py` were split
before it. `_service`/`_repo` are duplicated in each file rather than
shared via `conftest.py` — matching this test suite's own existing
convention (`test_search.py` already duplicates its own `_repo`, with no
shared fixture module for it).
"""

import errno
import os
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService


def _service(root: Path, *, config: FilesystemConfig | None = None) -> FilesystemService:
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    return FilesystemService(security, config or FilesystemConfig())


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text("fn main() {}\n")
    return root


# --- list_files -----------------------------------------------------------------


def test_list_files_lists_immediate_children_by_default(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    entries = svc.list_files()
    paths = {e.path for e in entries}
    assert paths == {"src"}  # .git excluded


def test_list_files_recursive_includes_nested_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    entries = svc.list_files(recursive=True)
    paths = {e.path for e in entries}
    assert paths == {"src", "src/lib.rs"}


def test_list_files_excludes_git_metadata(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".git" / "config").write_text("x")
    svc = _service(root)
    entries = svc.list_files(recursive=True)
    assert all(not e.path.startswith(".git") for e in entries)


def test_list_files_reports_type_size_symlink_status(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    entries = {e.path: e for e in svc.list_files(recursive=True)}
    assert entries["src"].type == "directory"
    assert entries["src/lib.rs"].type == "file"
    assert entries["src/lib.rs"].size == len("fn main() {}\n")
    assert entries["src/lib.rs"].is_symlink is False
    assert entries["src/lib.rs"].modified_at is not None


# --- HR-012 (docs/HARDENING_SPEC.md §7): an escaping symlink doesn't break listing ---


def test_at_hat010_an_escaping_symlink_to_a_real_target_does_not_break_listing(
    tmp_path: Path,
) -> None:
    """A real regression: a symlink escaping the repository to a target
    that genuinely exists on the host (`.venv/bin/python ->
    /usr/bin/python3` is the ordinary, unremarkable case this generalizes
    from) used to make `resolve()`'s `PathOutsideRepositoryError`
    propagate straight out of `list_files`'s loop — aborting the *entire*
    listing over one symlink, not just omitting that one entry."""
    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("real host content\n")
    (root / "escape_real").symlink_to(outside)
    svc = _service(root)

    entries = {e.path: e for e in svc.list_files(recursive=True)}

    assert "escape_real" in entries
    assert entries["escape_real"].type == "symlink"
    assert entries["escape_real"].is_symlink is True
    # the host target's real size/mtime is not disclosed either.
    assert entries["escape_real"].size is None
    assert entries["escape_real"].modified_at is None
    # the rest of the listing is unaffected.
    assert entries["src/lib.rs"].type == "file"


def test_at_hat010_a_dangling_symlink_does_not_break_listing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "dangling").symlink_to(tmp_path / "does-not-exist")
    svc = _service(root)

    entries = {e.path: e for e in svc.list_files(recursive=True)}

    assert entries["dangling"].type == "symlink"
    assert entries["dangling"].size is None
    assert entries["src/lib.rs"].type == "file"


def test_at_hat010_search_files_skips_an_escaping_symlink_without_crashing(
    tmp_path: Path,
) -> None:
    """`search_files` has the same gap through a different call site
    (`security.resolve` inside its own filter loop) — an escaping
    symlink used to abort the entire search. Unlike `list_files`, a
    search has no reason to read through the symlink into host content,
    so it's skipped rather than reported."""
    from dev_workspace_mcp.filesystem.search import search_files

    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("findme-in-host-content\n")
    (root / "escape_real").symlink_to(outside)
    (root / "normal.txt").write_text("findme-in-repo-content\n")
    security = FilesystemSecurity(root, authorized_roots=[root.parent])

    results = search_files(security, pattern="findme", mode="text")

    paths = {r.path for r in results}
    assert "normal.txt" in paths
    assert "escape_real" not in paths  # never read through the symlink


def test_list_files_respects_max_entries(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for i in range(10):
        (root / f"file{i}.txt").write_text("x")
    svc = _service(root)
    entries = svc.list_files(max_entries=3)
    assert len(entries) == 3


def test_list_files_on_nonexistent_directory_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.list_files("does-not-exist")


# --- RR-007 (`docs/REMEDIATION_SPEC.md` §5): enumeration survives a bad entry -----


def test_rr007_list_files_survives_a_symlink_loop(tmp_path: Path) -> None:
    """Reproduced empirically that two mutually-referencing symlinks used
    to abort the *entire* listing: `Path.resolve()` raises `RuntimeError`
    on a detected cycle (CPython 3.12) — neither `OSError` nor
    `WorkspaceError`, so `_is_git_metadata`'s handlers didn't catch it."""
    root = _repo(tmp_path)
    svc = _service(root)
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")

    entries = svc.list_files("", recursive=False)
    by_path = {e.path: e for e in entries}
    assert by_path["a"].type == "symlink"
    assert by_path["b"].type == "symlink"
    assert "src" in by_path  # the rest of the listing is unaffected


def test_rr007_list_files_survives_an_unreadable_subdirectory(tmp_path: Path) -> None:
    """A permission-denied subtree must not crash the listing either — the
    locked directory itself is still listed (its own `stat` needs only
    lookup permission on *its parent*, already proven by `iterdir`
    having found it), but nothing inside it can be enumerated."""
    root = _repo(tmp_path)
    svc = _service(root)
    locked = root / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("secret")
    locked.chmod(0o000)
    try:
        entries = svc.list_files("", recursive=True)
    finally:
        locked.chmod(0o755)  # restore so tmp_path cleanup can remove it

    by_path = {e.path: e for e in entries}
    assert by_path["locked"].type == "directory"
    assert "locked/secret.txt" not in by_path
    assert "src/lib.rs" in by_path  # the rest of the listing is unaffected


def test_at_g2r009_list_files_survives_an_entry_deleted_after_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2R-009 (`docs/GAP2_SPEC.md`): `list_files` enumerates the whole
    directory up front (`sorted(start.rglob(...)/iterdir())` fully
    materializes the candidate list before the loop below ever calls
    `_describe`), so a container process (via `exec`, e.g. `rm -rf` on
    the same bind-mounted checkout) can delete an entry in the window
    between enumeration and its own `_describe` call. `Path.stat()` —
    unlike `is_symlink`/`exists`/`is_dir`, which all tolerate a missing
    target and return `False` — raises `FileNotFoundError` there, not a
    `WorkspaceError`, so unguarded this crashed the whole listing
    untyped over one concurrently-deleted entry. Simulated
    deterministically by monkeypatching `Path.stat` to raise for the one
    target file, rather than racing a real background delete against
    this test's own timing."""
    root = _repo(tmp_path)
    svc = _service(root)
    doomed = root / "doomed.txt"
    doomed.write_text("gone before description\n")

    real_stat = Path.stat

    def _stat_raising_for_doomed(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == doomed:
            # A properly-formed `errno=ENOENT`, not a bare constructor:
            # `is_symlink`/`exists` call `self.stat(follow_symlinks=…)`
            # internally and catch `OSError` themselves, but only
            # recognize it as "missing, not a real error" by errno — an
            # improperly-formed exception here would make *this test*
            # crash inside `is_symlink()`, before ever reaching the
            # `_describe`/`path.stat()` call this test targets.
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(self))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _stat_raising_for_doomed)

    entries = svc.list_files("", recursive=False)  # SHALL NOT raise

    by_path = {e.path: e for e in entries}
    assert "doomed.txt" not in by_path
    assert "src" in by_path  # the rest of the listing is unaffected
