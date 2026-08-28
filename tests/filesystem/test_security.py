"""S9: filesystem path validation and Git-metadata exclusion
(§12, §12.1, AT-001, AT-002, AT-033)."""

import os
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import (
    InvalidArgumentError,
    PathGitMetadataError,
    PathOutsideRepositoryError,
)
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


def test_h37_repositories_only_authorization_downgrades_rule_3_to_plain_containment(
    tmp_path: Path,
) -> None:
    """H37 (`docs/HARDENING_TODO.md`): a linked worktree and its main
    repo, both authorized only through `repositories:` (no
    `repository_roots:`). With the pre-fix `authorized_roots` (just
    `repository_roots`, empty here), `_external_git_dir` silently
    resolves to `None` and rule 3 goes inert — but the escaping symlink
    is still refused, just downgraded from `PathGitMetadataError` to the
    generic `PathOutsideRepositoryError`, confirming this is a
    classification gap, not an access-control bypass. With the fix
    (`ServerConfig.authorized_git_dir_roots`, including the main repo's
    own configured path — under which the worktree's real Git directory
    lives), rule 3 correctly fires again."""
    root_dir = tmp_path / "host"
    main_repo = root_dir / "main-repo"
    main_git = main_repo / ".git"
    main_git.mkdir(parents=True)
    (main_git / "some-internal-file").write_text("internal")

    checkout = root_dir / "worktree"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {main_git}\n")
    (checkout / "innocuous").symlink_to(main_git, target_is_directory=True)

    # Pre-fix: `repository_roots` alone (empty) does not authorize
    # `main_git` as an external Git-directory target — rule 3 goes
    # inert, but the symlink still escapes `checkout`'s own root, so the
    # plain containment check still refuses it.
    fs_unfixed = FilesystemSecurity(checkout, authorized_roots=[])
    with pytest.raises(PathOutsideRepositoryError):
        fs_unfixed.resolve("innocuous/some-internal-file")

    # Fixed: with the main repo's own path included in `authorized_roots`
    # (as `ServerConfig.authorized_git_dir_roots` now ensures), rule 3
    # recognizes the target as Git metadata and reports the more
    # specific classification.
    fs_fixed = FilesystemSecurity(checkout, authorized_roots=[main_repo])
    with pytest.raises(PathGitMetadataError):
        fs_fixed.resolve("innocuous/some-internal-file")


# --- RR-006 (`docs/REMEDIATION_SPEC.md` §5): typed errors, not crashes -------------


def test_rr006_open_validated_missing_file_raises_invalid_argument(tmp_path: Path) -> None:
    """Reproduced empirically that this used to propagate a raw, untranslated
    `FileNotFoundError` — `translate_workspace_errors` only catches
    `WorkspaceError`, so the tool call crashed instead of failing cleanly."""
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(InvalidArgumentError):
        fs.open_validated("nope.txt", os.O_RDONLY)


def test_rr006_open_parent_validated_missing_parent_raises_invalid_argument(
    tmp_path: Path,
) -> None:
    """The exact `delete_file(missing parent)` reproduction: a raw
    `FileNotFoundError` from opening a nonexistent parent directory used
    to crash the tool call instead of failing cleanly."""
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(InvalidArgumentError):
        fs.open_parent_validated("nosuchdir/file.txt")


def test_rr006_open_parent_validated_returns_the_symlinks_own_literal_name(
    tmp_path: Path,
) -> None:
    """The root cause of `delete_file` on a symlink deleting its *target*:
    the old implementation ran the whole path (including the final
    component) through `self.resolve()`, which follows symlinks — so for
    `relative_path="link.txt"` it silently returned the *target*'s own
    parent and name, not the link's. Reproduced empirically before this
    fix; the returned name here must be the client's literal final
    component regardless of what it points to."""
    root = _repo(tmp_path)
    target = root / "target.txt"
    target.write_text("real target content")
    link = root / "link.txt"
    link.symlink_to(target)

    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    parent_fd, name = fs.open_parent_validated("link.txt")
    try:
        assert name == "link.txt"
    finally:
        os.close(parent_fd)


def test_rr006_open_parent_validated_rejects_git_as_the_final_component(
    tmp_path: Path,
) -> None:
    """Defense in depth: `delete_file` (this method's one current caller)
    already runs the full `resolve()` first, which independently rejects
    `.git`, but `open_parent_validated` no longer runs the final
    component through that same check (that is the whole point of this
    method) — a future caller that skips the up-front `resolve()` should
    not silently regain access to `.git` through this path."""
    root = _repo(tmp_path)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathGitMetadataError):
        fs.open_parent_validated(".git")


def test_rr006_ensure_parent_directories_a_plain_file_blocking_is_invalid_argument(
    tmp_path: Path,
) -> None:
    """`ENOTDIR` from a plain file already sitting where a directory
    component is needed is a benign client mistake, not an escape —
    reproduced empirically that the old code reported this identically
    to a real symlink-substitution attack (`PathOutsideRepositoryError`,
    "path escapes the repository root"), which is misleading."""
    root = _repo(tmp_path)
    (root / "notadir.txt").write_text("hi")
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(InvalidArgumentError):
        fs.ensure_parent_directories("notadir.txt/nested/thing.txt")


def test_rr006_ensure_parent_directories_a_symlink_blocking_is_still_an_escape(
    tmp_path: Path,
) -> None:
    """The other half of the same distinction: a symlink at an
    intermediate component — real attack or benign pre-existing layout,
    HR-011 doesn't try to tell those apart — is still reported as
    `PathOutsideRepositoryError`, not downgraded to `InvalidArgumentError`
    just because both raise the identical `NotADirectoryError`/`ENOTDIR`
    at the `O_NOFOLLOW` open."""
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "d").symlink_to(outside, target_is_directory=True)
    fs = FilesystemSecurity(root, authorized_roots=[tmp_path / "work"])
    with pytest.raises(PathOutsideRepositoryError):
        fs.ensure_parent_directories("d/nested/thing.txt")
