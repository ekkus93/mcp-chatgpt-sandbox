"""S9: filesystem path validation and Git-metadata exclusion
(§12, §12.1, AT-001, AT-002, AT-033)."""

from pathlib import Path

import pytest

from dev_workspace_mcp.errors import PathGitMetadataError, PathOutsideRepositoryError
from dev_workspace_mcp.filesystem.security import FilesystemSecurity


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text("fn main() {}")
    return root


def test_ordinary_path_resolves_within_the_root(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    assert fs.resolve("src/lib.rs") == (root / "src" / "lib.rs").resolve()


def test_root_itself_resolves(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    assert fs.resolve(".") == root.resolve()
    assert fs.resolve("") == root.resolve()


# --- AT-001: repository boundary ------------------------------------------------


def test_at_001_dotdot_escape_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathOutsideRepositoryError):
        fs.resolve("../../etc/passwd")


def test_at_001_absolute_looking_path_is_treated_as_relative_not_a_bypass(
    tmp_path: Path,
) -> None:
    """A leading "/" must not defeat containment: pathlib's own `/`
    operator replaces the left side entirely when the right side is
    absolute, which would otherwise let `path="/etc/passwd"` resolve to
    the real host file regardless of the repository root."""
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    resolved = fs.resolve("/etc/passwd")
    assert resolved == (root / "etc" / "passwd").resolve()
    assert resolved != Path("/etc/passwd")


@pytest.mark.parametrize(
    "escape",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "a/b/../../../outside.txt",
        "..",
    ],
)
def test_at_001_every_dotdot_shape_is_rejected(tmp_path: Path, escape: str) -> None:
    root = _repo(tmp_path)
    (tmp_path / "work" / "outside.txt").write_text("nope")
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathOutsideRepositoryError):
        fs.resolve(escape)


# --- AT-002: symlink escape --------------------------------------------------------


def test_at_002_escaping_symlink_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "link").symlink_to(outside, target_is_directory=True)

    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathOutsideRepositoryError):
        fs.resolve("link/secret.txt")


def test_symlink_that_stays_under_the_root_is_accepted(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "alias").symlink_to(root / "src", target_is_directory=True)

    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    resolved = fs.resolve("alias/lib.rs")
    assert resolved == (root / "src" / "lib.rs").resolve()


# --- AT-033: Git metadata exclusion -----------------------------------------------


def test_at_033_git_directory_itself_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathGitMetadataError):
        fs.resolve(".git")


def test_at_033_path_inside_git_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathGitMetadataError):
        fs.resolve(".git/hooks/pre-commit")
    with pytest.raises(PathGitMetadataError):
        fs.resolve(".git/config")


def test_at_033_git_component_at_any_depth_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    nested_git = root / "vendor" / "submodule" / ".git"
    nested_git.mkdir(parents=True)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathGitMetadataError):
        fs.resolve("vendor/submodule/.git/config")


def test_ordinary_files_named_similarly_to_git_are_not_rejected(tmp_path: Path) -> None:
    """Only a literal `.git` path *component* is excluded — a file that
    merely contains "git" in its name is an ordinary file."""
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("target/\n")
    (root / "gitutils.py").write_text("# not git metadata")
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    assert fs.resolve(".gitignore") == (root / ".gitignore").resolve()
    assert fs.resolve("gitutils.py") == (root / "gitutils.py").resolve()


def test_at_033_gitfile_target_is_rejected_even_without_literal_git_in_the_request(
    tmp_path: Path,
) -> None:
    """§18.2 rule 3: a linked worktree's real Git directory lives outside
    the checkout. A symlink inside the checkout that resolves into it is
    still Git metadata, even though the client's request never mentions
    ".git" literally."""
    root_dir = tmp_path / "work"
    main_git = root_dir / "main-repo" / ".git"
    main_git.mkdir(parents=True)
    (main_git / "some-internal-file").write_text("internal")

    checkout = root_dir / "feature-worktree"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {main_git}\n")
    # A symlink whose name has nothing to do with git, resolving straight
    # into the real Git directory.
    (checkout / "innocuous").symlink_to(main_git, target_is_directory=True)

    fs = FilesystemSecurity(checkout, authorized_roots=[root_dir])
    with pytest.raises(PathGitMetadataError):
        fs.resolve("innocuous/some-internal-file")


def test_gitfile_resolution_failure_does_not_break_unrelated_requests(
    tmp_path: Path,
) -> None:
    """Rule 3 is best-effort: a malformed gitfile must not make ordinary,
    unrelated filesystem requests fail."""
    root = tmp_path / "checkout"
    root.mkdir(parents=True)
    (root / ".git").write_text("not a valid gitfile at all\n")
    (root / "README.md").write_text("hello")

    fs = FilesystemSecurity(root, authorized_roots=[tmp_path])
    assert fs.resolve("README.md") == (root / "README.md").resolve()
    # The literal ".git" path is still excluded by rule 1/2 regardless.
    with pytest.raises(PathGitMetadataError):
        fs.resolve(".git")
