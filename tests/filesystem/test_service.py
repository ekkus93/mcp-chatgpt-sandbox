"""S10: filesystem tool primitives — list/read/write/delete
(§13.1-§13.6, §12.2, §47, AT-003, AT-018)."""

import contextlib
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import FileConflictError, InvalidArgumentError
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService, sha256_of


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


# --- read_file --------------------------------------------------------------------


def test_read_file_returns_content_and_sha256(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.read_file("src/lib.rs")
    assert result.content == "fn main() {}\n"
    assert result.sha256 == sha256_of(b"fn main() {}\n")
    assert result.size == len("fn main() {}\n")
    assert result.truncated is False
    assert result.is_binary is False


def test_read_file_byte_range(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.read_file("src/lib.rs", byte_range=(0, 2))
    assert result.content == "fn"
    assert result.size == len("fn main() {}\n")  # full size, not the range


def test_read_file_line_range(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "multi.txt").write_text("a\nb\nc\nd\n")
    svc = _service(root)
    result = svc.read_file("multi.txt", line_range=(1, 3))
    assert result.content == "b\nc\n"


def test_read_file_rejects_both_ranges_at_once(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.read_file("src/lib.rs", byte_range=(0, 1), line_range=(0, 1))


def test_read_file_reports_truncation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "big.txt").write_text("x" * 100)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))
    result = svc.read_file("big.txt")
    assert result.truncated is True
    assert len(result.content) <= 10
    assert result.size == 100  # full size still reported


def test_read_file_sha256_is_of_full_content_even_when_truncated(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    full = "x" * 100
    (root / "big.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))
    result = svc.read_file("big.txt")
    assert result.sha256 == sha256_of(full.encode())


def test_read_file_binary_reports_is_binary_without_content(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "binary.dat").write_bytes(b"\x00\x01\xff\xfe\x02")
    svc = _service(root)
    result = svc.read_file("binary.dat")
    assert result.is_binary is True
    assert result.content is None
    assert result.sha256 == sha256_of(b"\x00\x01\xff\xfe\x02")


def test_read_file_directory_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.read_file("src")


def test_read_file_git_metadata_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    from dev_workspace_mcp.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.read_file(".git/config")


# --- write_file (AT-003, AT-018) ------------------------------------------------


def test_at_003_write_appears_immediately_on_disk(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("new.txt", "hello")
    assert (root / "new.txt").read_text() == "hello"


def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("a/b/c.txt", "nested")
    assert (root / "a" / "b" / "c.txt").read_text() == "nested"


def test_write_file_returns_sha256_and_size(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.write_file("new.txt", "hello")
    assert result.sha256 == sha256_of(b"hello")
    assert result.size == 5


def test_write_file_without_expected_sha_always_overwrites(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("f.txt", "one")
    svc.write_file("f.txt", "two")
    assert (root / "f.txt").read_text() == "two"


def test_write_file_with_matching_expected_sha_succeeds(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    first = svc.write_file("f.txt", "one")
    svc.write_file("f.txt", "two", expected_sha256=first.sha256)
    assert (root / "f.txt").read_text() == "two"


def test_at_018_stale_expected_sha_raises_file_conflict(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("f.txt", "one")
    stale_sha = sha256_of(b"never-written")
    with pytest.raises(FileConflictError) as excinfo:
        svc.write_file("f.txt", "two", expected_sha256=stale_sha)
    assert excinfo.value.recoverable is True
    assert (root / "f.txt").read_text() == "one"  # unchanged


def test_write_file_with_expected_sha_on_nonexistent_file_conflicts(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(FileConflictError):
        svc.write_file("new.txt", "content", expected_sha256=sha256_of(b"anything"))


def test_write_file_exceeding_limit_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root, config=FilesystemConfig(max_write_bytes=5))
    with pytest.raises(InvalidArgumentError):
        svc.write_file("f.txt", "this is too long")


def test_write_file_git_metadata_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    from dev_workspace_mcp.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.write_file(".git/hooks/pre-commit", "#!/bin/sh\n")


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
    from dev_workspace_mcp.errors import PathGitMetadataError

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
    from dev_workspace_mcp.errors import PathGitMetadataError

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
    from dev_workspace_mcp.errors import PathGitMetadataError

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


def test_at_hat009_read_file_never_returns_content_from_outside_the_repository(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """A real regression: with `d` flipping between a real directory and
    a symlink to `outside`, `read_file("d/secret.txt")` returned the
    *host* file's content — a real private key, in the original
    reproduction — on the very first race iteration. Sustained here for
    2000 iterations with no leak, confirmed (by reverting the fix) to
    fail within ~150 iterations without it."""
    root, _d, outside = _symlink_flipper
    (outside / "secret.txt").write_text("OUTSIDE-THE-REPOSITORY\n")
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        try:
            result = service.read_file("d/secret.txt")
        except Exception:
            continue
        assert result.content is None or "OUTSIDE-THE-REPOSITORY" not in result.content


def test_at_hat009_write_file_never_lands_outside_the_repository(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """The write-side counterpart: `write_file("d/pwned.txt", ...)` must
    never create a file under `outside`, and — the bug this test itself
    caught while this task was being written — a *rejected* write must
    not leave a stray file behind outside the repository either."""
    root, _d, outside = _symlink_flipper
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.write_file("d/pwned.txt", "written by the container\n")
        assert not (outside / "pwned.txt").exists()


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


def test_at_h028_write_file_never_mkdirs_through_an_intermediate_symlink(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """H28 (`docs/HARDENING_TODO.md`, HR-030): the mkdir-side gap H9
    (HR-011) documented and deliberately left open — `write_file`'s old
    `path.parent.mkdir(parents=True, exist_ok=True)` walked the
    *resolved path string*, which happily creates directories through
    whatever `d` currently is. A single-level target (`d/pwned.txt`,
    the existing HAT-009 write test above) can't reach this gap at
    all: `resolve()` itself already rejects the call outright if `d` is
    a symlink to `outside` at resolution time, since the immediate
    parent needs no `mkdir` — there's no intermediate component left to
    create. A *nested* target (`d/nested/pwned.txt`) is what actually
    exercises it: `resolve()` can legitimately succeed while `d` is
    still a real, in-bounds directory (the deeper `nested` component
    doesn't exist yet, so nothing to follow), and only *after* that
    does `d` get swapped to a symlink before the parent-directory walk
    runs — reproduced, by reverting `ensure_parent_directories` back to
    the old `mkdir(parents=True)`, as a real `outside/nested/` directory
    left behind on the host."""
    root, _d, outside = _symlink_flipper
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.write_file("d/nested/pwned.txt", "written by the container\n")
        assert not (outside / "nested").exists()
        assert not (outside / "nested" / "pwned.txt").exists()
