"""S10: filesystem tool primitives — list/read/write/delete
(§13.1-§13.6, §12.2, §47, AT-003, AT-018)."""

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
