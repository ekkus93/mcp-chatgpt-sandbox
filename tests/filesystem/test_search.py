"""S10: `search_files` — glob, text, regex, `.gitignore` awareness
(§13.7, §48, §49)."""

from pathlib import Path

import pytest

from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.filesystem.search import search_files
from dev_workspace_mcp.filesystem.security import FilesystemSecurity


def _security(root: Path) -> FilesystemSecurity:
    return FilesystemSecurity(root, authorized_roots=[root.parent])


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text('fn main() {\n    println!("hello");\n}\n')
    (root / "src" / "util.rs").write_text("pub fn helper() {}\n")
    (root / "README.md").write_text("# Example\nhello world\n")
    return root


# --- glob mode --------------------------------------------------------------------


def test_glob_matches_by_filename(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    results = search_files(_security(root), pattern="*.rs", mode="glob")
    paths = {r.path for r in results}
    assert paths == {"src/lib.rs", "src/util.rs"}
    assert all(r.line_number is None for r in results)


def test_glob_matches_by_full_relative_path(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    results = search_files(_security(root), pattern="src/*.rs", mode="glob")
    assert {r.path for r in results} == {"src/lib.rs", "src/util.rs"}


# --- text mode --------------------------------------------------------------------


def test_text_search_finds_substring_with_line_numbers(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    results = search_files(_security(root), pattern="hello", mode="text")
    matches = {(r.path, r.line_number) for r in results}
    assert (str(Path("src") / "lib.rs"), 2) in matches
    assert (str(Path("README.md")), 2) in matches


def test_text_search_scoped_to_a_path(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    results = search_files(_security(root), pattern="hello", mode="text", path="src")
    assert all(r.path.startswith("src/") for r in results)
    assert len(results) == 1


# --- regex mode -------------------------------------------------------------------


def test_regex_search_matches_pattern(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    results = search_files(_security(root), pattern=r"fn \w+\(", mode="regex")
    paths = {r.path for r in results}
    assert "src/lib.rs" in paths
    assert "src/util.rs" in paths


# --- git metadata and default-ignored directories (§49) ---------------------------


def test_search_excludes_git_metadata(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".git" / "config").write_text("hello inside git config")
    results = search_files(_security(root), pattern="hello", mode="text")
    assert all(not r.path.startswith(".git") for r in results)


def test_search_excludes_default_ignored_directories(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    node_modules = root / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("hello from node_modules")
    results = search_files(_security(root), pattern="hello", mode="text")
    assert all("node_modules" not in r.path for r in results)


def test_include_ignored_permits_default_ignored_directories(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = root / "target" / "debug"
    target.mkdir(parents=True)
    (target / "build.log").write_text("hello from target")
    results = search_files(_security(root), pattern="hello", mode="text", include_ignored=True)
    assert any("target" in r.path for r in results)


def test_search_honors_gitignore_by_default(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("*.generated\n")
    (root / "output.generated").write_text("hello generated")
    results = search_files(_security(root), pattern="hello", mode="text")
    assert all("output.generated" not in r.path for r in results)


def test_include_ignored_overrides_gitignore(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("*.generated\n")
    (root / "output.generated").write_text("hello generated")
    results = search_files(_security(root), pattern="hello", mode="text", include_ignored=True)
    assert any("output.generated" in r.path for r in results)


# --- limits and misc ----------------------------------------------------------------


def test_search_respects_max_results(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for i in range(20):
        (root / f"file{i}.txt").write_text("hello\n")
    results = search_files(_security(root), pattern="hello", mode="text", max_results=5)
    assert len(results) == 5


def test_search_skips_binary_files_without_error(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "binary.dat").write_bytes(b"\x00\x01hello\xff")
    results = search_files(_security(root), pattern="hello", mode="text")
    assert all(r.path != "binary.dat" for r in results)


def test_search_on_nonexistent_path_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(InvalidArgumentError):
        search_files(_security(root), pattern="x", mode="text", path="does-not-exist")
