"""S10: `delete_file` (§13.4, §12.2, §47, AT-033) — including HR-003's
repository-root-cannot-be-deleted guard, RR-006's typed-error coverage, and
HR-011's race-free-access guarantee against deleting outside the repository.

Extracted from `test_service.py` — see `test_service_list.py`'s module
docstring for the split rationale. `_symlink_flipper` is duplicated here
(and in `test_service_read.py`/`test_service_write.py`) rather than
shared, matching this file's own `_service`/`_repo` duplication.
"""

import contextlib
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.config.models import FilesystemConfig
from mcp_chatgpt_sandbox.errors import InvalidArgumentError
from mcp_chatgpt_sandbox.filesystem.security import FilesystemSecurity
from mcp_chatgpt_sandbox.filesystem.service import FilesystemService


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


# --- delete_file (AT-033 coverage for delete) -----------------------------------


def test_delete_file_removes_a_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.delete_file("src/lib.rs")
    assert not (root / "src" / "lib.rs").exists()


def test_delete_file_directory_requires_explicit_recursive(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.delete_file("src")
    assert (root / "src").exists()


def test_delete_file_recursive_removes_directory(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.delete_file("src", recursive=True)
    assert not (root / "src").exists()


def test_delete_file_nonexistent_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.delete_file("does-not-exist.txt")


def test_delete_file_git_metadata_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    from mcp_chatgpt_sandbox.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.delete_file(".git", recursive=True)


# --- HR-003 (docs/HARDENING_SPEC.md §5): the repository root cannot be deleted --------


@pytest.mark.parametrize("relative_path", ["", ".", "./", "src/..", "src/../."])
def test_at_hat003_the_repository_root_cannot_be_deleted(
    tmp_path: Path, relative_path: str
) -> None:
    """A real regression: `delete_file(path="", recursive=True)` used to
    resolve straight to the repository root — whose own relative path
    has zero components, so the `.git`-path-component guard never
    fired — and `shutil.rmtree` took the working tree and `.git`
    together, reported as `{'deleted': True}`. Every variant here
    resolves to the same root."""
    root = _repo(tmp_path)
    svc = _service(root)

    with pytest.raises(InvalidArgumentError):
        svc.delete_file(relative_path, recursive=True)

    assert root.exists()
    assert (root / ".git").exists()
    assert (root / "src" / "lib.rs").exists()


def test_at_hat003_a_deep_traversal_that_lands_back_on_the_root_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    """A path that starts two levels down and walks back up to the root
    via `..` — not just the trivial `""`/`.`/`./`/`src/..` forms already
    parametrized above — resolves to the same root and is refused the
    same way."""
    root = _repo(tmp_path)
    (root / "src" / "nested").mkdir(exist_ok=True)
    svc = _service(root)

    with pytest.raises(InvalidArgumentError):
        svc.delete_file("src/nested/../..", recursive=True)

    assert root.exists()


def test_at_hat003_non_recursive_root_delete_is_also_rejected_as_the_root(
    tmp_path: Path,
) -> None:
    """The root check fires before the recursive/non-recursive branch —
    the error is specifically "the root cannot be deleted," not a
    generic "pass recursive=True" prompt that would then let a retry
    through."""
    root = _repo(tmp_path)
    svc = _service(root)

    with pytest.raises(InvalidArgumentError, match="root"):
        svc.delete_file("", recursive=False)

    assert root.exists()


def test_at_hat003_a_nested_git_directory_blocks_the_recursive_delete_of_its_parent(
    tmp_path: Path,
) -> None:
    """A submodule or vendored checkout inside a directory being
    recursively deleted: `_reject_in_tree_git_metadata` already blocks
    *targeting* the nested `.git` directly, but not sweeping it up as
    part of deleting its containing directory."""
    root = _repo(tmp_path)
    (root / "vendored").mkdir()
    (root / "vendored" / ".git").mkdir()
    (root / "vendored" / "file.txt").write_text("hi\n")
    svc = _service(root)
    from mcp_chatgpt_sandbox.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.delete_file("vendored", recursive=True)

    assert (root / "vendored" / ".git").exists()
    assert (root / "vendored" / "file.txt").exists()


def test_at_hat003_a_nested_gitfile_also_blocks_the_recursive_delete(tmp_path: Path) -> None:
    """A linked worktree or submodule may have `.git` as a *file*
    (a gitfile pointing elsewhere), not a directory — the scan for a
    nested `.git` has to catch that shape too, not just a directory."""
    root = _repo(tmp_path)
    (root / "linked-worktree").mkdir()
    (root / "linked-worktree" / ".git").write_text("gitdir: /somewhere/else\n")
    svc = _service(root)
    from mcp_chatgpt_sandbox.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.delete_file("linked-worktree", recursive=True)


def test_delete_file_recursive_without_nested_git_is_unaffected(tmp_path: Path) -> None:
    """The nested-`.git` scan must not become a blanket regression: an
    ordinary directory with no `.git` anywhere inside it still deletes
    normally."""
    root = _repo(tmp_path)
    (root / "plain").mkdir()
    (root / "plain" / "x.txt").write_text("x\n")
    svc = _service(root)

    svc.delete_file("plain", recursive=True)
    assert not (root / "plain").exists()


# --- RR-006 (`docs/REMEDIATION_SPEC.md` §5): typed errors, not crashes -------------


def test_rr006_delete_file_missing_parent_raises_invalid_argument(tmp_path: Path) -> None:
    """The exact `delete_file(missing parent)` reproduction: a raw
    `FileNotFoundError` from `open_parent_validated` used to crash the
    tool call instead of failing cleanly."""
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.delete_file("nosuchdir/file.txt")


def test_rr006_delete_file_on_a_symlink_deletes_the_link_not_the_target(
    tmp_path: Path,
) -> None:
    """The root-cause bug: `open_parent_validated` used to resolve the
    *whole* path (symlink-following) rather than just the parent,
    silently substituting the target's own parent+name for the link's —
    so deleting an in-repo symlink deleted the file it pointed to and
    left the link dangling, the opposite of `os.unlink`'s (never follows
    a symlink to delete) actual semantics. Reproduced empirically before
    this fix: `target.txt` was the one that disappeared."""
    root = _repo(tmp_path)
    svc = _service(root)
    target = root / "target.txt"
    target.write_text("real target content")
    link = root / "link.txt"
    link.symlink_to(target)

    svc.delete_file("link.txt")

    assert target.exists()
    assert target.read_text() == "real target content"
    assert not link.exists()  # the link itself is gone
    assert not os.path.lexists(link)  # not merely dangling — the directory entry too


# --- HR-011 (docs/HARDENING_SPEC.md §7): race-free file access -----------------------


@pytest.fixture
def _symlink_flipper(tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
    """A background thread that continuously flips a directory `d`
    between a real directory and a symlink pointing outside the
    repository — standing in for the container racing the daemon via
    `exec`, the same threat `open_validated`'s own docstring names.
    Yields `(root, d, outside)`; stops the thread on teardown."""
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    d = root / "d"
    d.mkdir()

    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                if d.is_dir() and not d.is_symlink():
                    shutil.rmtree(str(d))
            except OSError:
                pass
            with contextlib.suppress(FileExistsError):
                os.symlink(str(outside), str(d))
            with contextlib.suppress(OSError):
                os.unlink(str(d))
            with contextlib.suppress(FileExistsError):
                d.mkdir()

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        yield root, d, outside
    finally:
        stop.set()
        thread.join(timeout=2)


def test_at_hat009_delete_file_never_deletes_outside_the_repository(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    root, _d, outside = _symlink_flipper
    important = outside / "important-host-file.txt"
    important.write_text("do not delete me\n")
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.delete_file("d/important-host-file.txt")
        assert important.exists()
