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


def test_at_vat002_byte_range_truncated_reflects_bytes_withheld_not_requested(
    tmp_path: Path,
) -> None:
    """VR-002 (`docs/REVIEW_SPEC.md` §4): the old code computed
    `truncated = span_len > limit` from the *requested* span
    (`end - start`), not from how much of the file actually remained. A
    client whose `end` runs past EOF, or who omits a client-side
    `max_bytes` cap smaller than the requested span, got `truncated:
    True` on content that was already complete — reproduced here with a
    10-byte file and a huge explicit `end` standing in for "the rest of
    the file" (the documented, correct way to get that is `end=None`)."""
    root = _repo(tmp_path)
    (root / "small.txt").write_text("0123456789")
    svc = _service(root)  # default max_read_bytes: 10 MiB

    result = svc.read_file("small.txt", byte_range=(0, 20_000_000))
    assert result.content == "0123456789"
    assert result.truncated is False

    # The documented idiom for "to EOF" already worked correctly — this
    # asserts the fix didn't regress it.
    result_end_none = svc.read_file("small.txt", byte_range=(0, None))
    assert result_end_none.truncated is False


def test_at_vat002_byte_range_truncated_still_true_when_bytes_are_genuinely_withheld(
    tmp_path: Path,
) -> None:
    """The other half of VR-002: `truncated` SHALL still be `True` when
    content actually was withheld — the fix narrows the flag to bytes
    withheld, it does not weaken it to never firing."""
    root = _repo(tmp_path)
    (root / "small.txt").write_text("0123456789")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=5))

    result = svc.read_file("small.txt", byte_range=(0, 20_000_000))
    assert result.content == "01234"
    assert result.truncated is True

    # A requested span narrower than the withheld remainder is also still
    # correctly flagged.
    result_narrow = svc.read_file("small.txt", byte_range=(0, 8), max_bytes=3)
    assert result_narrow.content == "012"
    assert result_narrow.truncated is True


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


# --- H29 (`docs/HARDENING_TODO.md`, HD3): stat before reading, not after --------


def test_at_h029_full_sha_and_size_are_correct_for_a_multi_megabyte_file(
    tmp_path: Path,
) -> None:
    """H29: `read_file` now bounds its *content* buffer to `max_read_bytes`
    before reading, streaming the remainder through the hasher in
    `_READ_FILE_HASH_CHUNK_BYTES`-sized chunks rather than holding the
    whole file in memory — a file genuinely larger than one such chunk
    (unlike the pre-existing 100-byte truncation tests above, too small
    to exercise the streaming loop at all) is what actually proves the
    multi-chunk path computes the same `sha256`/`size` a full read
    would."""
    from dev_workspace_mcp.filesystem.service import _READ_FILE_HASH_CHUNK_BYTES

    root = _repo(tmp_path)
    # A few chunks' worth, so the streaming loop actually iterates more
    # than once — not just one bounded head read.
    full = ("0123456789" * 100_000)[: _READ_FILE_HASH_CHUNK_BYTES * 3 + 12345]
    (root / "huge.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=64))

    result = svc.read_file("huge.txt")

    assert result.truncated is True
    assert len(result.content.encode()) <= 64
    assert result.size == len(full.encode())
    assert result.sha256 == sha256_of(full.encode())


def test_at_h029_a_byte_range_deep_in_a_large_file_still_works(tmp_path: Path) -> None:
    """RR-020 (`docs/REMEDIATION_SPEC.md` §13): `byte_range` now `seek`s
    directly to the requested span rather than reading the whole file
    (the pre-R13 behavior this test's own docstring used to describe) —
    confirmed still correct, including `sha256`/`size` for the whole
    file (computed via the same streamed hash pass the no-range path
    uses), for a file larger than the streaming chunk size."""
    from dev_workspace_mcp.filesystem.service import _READ_FILE_HASH_CHUNK_BYTES

    root = _repo(tmp_path)
    full = "0123456789" * (_READ_FILE_HASH_CHUNK_BYTES // 5)
    (root / "huge.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=len(full.encode())))

    deep_offset = _READ_FILE_HASH_CHUNK_BYTES * 2
    result = svc.read_file("huge.txt", byte_range=(deep_offset, deep_offset + 10))

    assert result.content == full[deep_offset : deep_offset + 10]
    assert result.sha256 == sha256_of(full.encode())
    assert result.size == len(full.encode())


# --- RR-018/RR-020 (`docs/REMEDIATION_SPEC.md` §13): ranged reads are
# character-safe and bounded --------------------------------------------------


def test_rr018_a_multibyte_character_at_the_limit_boundary_is_truncated_text(
    tmp_path: Path,
) -> None:
    """The checklist's own reproduction: `max_read_bytes` landing exactly
    where a multi-byte character starts used to decode the *whole*
    (peek-byte-included) buffer in one shot, fail on that lone leading
    byte of the split character, and misreport an ordinary text file as
    binary — with `content=None` and, worse, `truncated=False`, denying
    the client even the signal to retry with a range."""
    root = _repo(tmp_path)
    (root / "f.txt").write_bytes(b"a" * 10 + "é".encode() + b"tail")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))

    result = svc.read_file("f.txt")

    assert result.is_binary is False
    assert result.content == "a" * 10
    assert result.truncated is True
    assert result.size == 16


def test_rr018_a_character_split_exactly_at_the_limit_is_dropped_not_corrupted(
    tmp_path: Path,
) -> None:
    """The limit itself, not just the peek byte, can land mid-character —
    here `max_read_bytes=11` leaves the window holding exactly the first
    of `é`'s 2 bytes. That lone byte must be trimmed off (deferred, not
    surfaced as U+FFFD or as a decode failure), the same way `git_diff`'s
    own pagination already handles this (RR-016)."""
    root = _repo(tmp_path)
    (root / "f.txt").write_bytes(b"a" * 10 + "é".encode() + b"tail")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=11))

    result = svc.read_file("f.txt")

    assert result.is_binary is False
    assert result.content == "a" * 10
    assert "�" not in (result.content or "")
    assert result.truncated is True


def test_rr018_a_genuinely_binary_file_larger_than_the_limit_is_still_truncated(
    tmp_path: Path,
) -> None:
    """The other half of the same fix: `truncated` must be reported
    correctly even down the `is_binary=True` path — a real regression
    hardcoded `truncated=False` there unconditionally."""
    root = _repo(tmp_path)
    (root / "f.bin").write_bytes(b"\x00\x01\x02\xff" * 50)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))

    result = svc.read_file("f.bin")

    assert result.is_binary is True
    assert result.content is None
    assert result.truncated is True


def test_rr018_a_genuinely_incomplete_trailing_sequence_at_true_eof_is_binary(
    tmp_path: Path,
) -> None:
    """The converse of the trim fix: when the read reaches the *genuine*
    end of the file (not truncated by `limit`), a dangling partial UTF-8
    sequence there is not a windowing artifact — the file's own content
    truly isn't valid UTF-8, and trimming must not hide that."""
    root = _repo(tmp_path)
    (root / "f.bin").write_bytes(b"hello " + "é".encode()[:1])  # a real, permanent partial byte
    svc = _service(root, config=FilesystemConfig(max_read_bytes=1000))

    result = svc.read_file("f.bin")

    assert result.is_binary is True
    assert result.truncated is False  # this genuinely is the whole file


def test_rr020_byte_range_does_not_hold_the_whole_file_in_memory(tmp_path: Path) -> None:
    """RR-020: reproduced (via code inspection of the pre-fix
    `f.read()`) that `byte_range=(0, 1)` on a large file loaded the
    entire thing into memory just to return one byte. A real, sizable
    file here — peak RSS growth from serving a 5-byte range must stay
    far below the file's own size, which it would not if the whole file
    were still being read into a Python `bytes` object at any point."""
    import resource

    root = _repo(tmp_path)
    path = root / "big.bin"
    size = 200 * 1024 * 1024
    with path.open("wb") as f:
        f.seek(size - 1)
        f.write(b"\0")
        f.seek(0)
        f.write(b"hello")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=1000))

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB, Linux

    result = svc.read_file("big.bin", byte_range=(0, 5))

    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert result.content == "hello"
    assert result.size == size
    # Generous headroom (50 MiB) above whatever this process already had
    # resident — nowhere near the file's own 200 MiB, which is what the
    # old `f.read()` behavior would have added to RSS.
    assert (after - before) < 50 * 1024, (
        f"peak RSS grew by {(after - before) / 1024:.1f} MiB serving a 5-byte "
        f"range from a {size / 1024 / 1024:.0f} MiB file — the whole file was "
        "likely still read into memory"
    )


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


# --- RR-006 (`docs/REMEDIATION_SPEC.md` §5): typed errors, not crashes -------------


def test_rr006_write_file_onto_a_directory_raises_invalid_argument(tmp_path: Path) -> None:
    """Reproduced empirically that this used to propagate a raw,
    untranslated `IsADirectoryError` all the way out of `write_file` —
    `translate_workspace_errors` only catches `WorkspaceError`, so the
    tool call crashed instead of failing cleanly. Also confirms the
    `stat.S_ISDIR` check this replaces really was dead code: opening an
    existing directory with `O_RDWR` already raises `IsADirectoryError`
    before that check could ever run against it."""
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.write_file("src", "hello")


def test_rr006_write_file_permission_denied_raises_invalid_argument(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    target = root / "locked.txt"
    target.write_text("original")
    target.chmod(0o000)
    try:
        with pytest.raises(InvalidArgumentError):
            svc.write_file("locked.txt", "new content")
    finally:
        target.chmod(0o644)  # restore so tmp_path cleanup can remove it


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


def test_rr006_ensure_parent_directories_plain_file_blocking_via_write_file(
    tmp_path: Path,
) -> None:
    """The same distinction as `test_security.py`'s direct test, exercised
    through the actual `write_file` client-facing entry point: a plain
    file blocking an intermediate component is a benign mistake, not
    reported as an escape."""
    root = _repo(tmp_path)
    svc = _service(root)
    (root / "notadir.txt").write_text("hi")
    with pytest.raises(InvalidArgumentError):
        svc.write_file("notadir.txt/nested/thing.txt", "content")


def test_rr006_hash_pread_loops_across_multiple_chunks(tmp_path: Path) -> None:
    """`os.pread` can transfer fewer bytes than requested in a single
    call — confirmed the kernel caps one call at `0x7ffff000` bytes (just
    under 2 GiB) — so a real file that size can't be used as a test
    fixture. `chunk_size` lets this test force the same multi-call path
    with an ordinary small file instead: the looped, chunked result must
    equal a plain whole-file hash."""
    from dev_workspace_mcp.filesystem.service import _hash_pread

    content = os.urandom(10_000)
    path = tmp_path / "data.bin"
    path.write_bytes(content)

    fd = os.open(path, os.O_RDONLY)
    try:
        looped = _hash_pread(fd, len(content), chunk_size=777)  # forces ~13 iterations
    finally:
        os.close(fd)

    assert looped == sha256_of(content)


def test_rr006_write_file_expected_sha_matches_across_a_multi_chunk_hash(
    tmp_path: Path,
) -> None:
    """End-to-end version of the same fix, through `write_file`'s own
    `expected_sha256` conflict check — using the real (large, default)
    chunk size, so this specifically guards against a future regression
    that reintroduces a single unbounded `os.pread` call."""
    root = _repo(tmp_path)
    svc = _service(root)
    original = "x" * 50_000
    svc.write_file("big.txt", original)
    correct_sha = sha256_of(original.encode("utf-8"))

    # Matching expected_sha256 succeeds...
    svc.write_file("big.txt", "replaced", expected_sha256=correct_sha)
    # ...and a stale one is correctly rejected as a real conflict, not
    # masked by a mis-hashed "current" value.
    with pytest.raises(FileConflictError):
        svc.write_file("big.txt", "again", expected_sha256=correct_sha)


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
