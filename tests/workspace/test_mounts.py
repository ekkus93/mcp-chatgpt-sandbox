"""S8: mount construction — Git-directory resolution and the mount list
built from `git_dir_mode` (§5.1, §5.4, §18.2). Pure filesystem logic, no
Docker needed."""

from pathlib import Path

import pytest

from dev_workspace_mcp.errors import PathOutsideRepositoryError
from dev_workspace_mcp.workspace.container_config import SSH_KNOWN_HOSTS_CONTAINER_PATH
from dev_workspace_mcp.workspace.mounts import (
    build_known_hosts_mount,
    build_workspace_mounts,
    resolve_git_dir,
)


def _init_ordinary_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()


# --- resolve_git_dir: ordinary repository -----------------------------------------


def test_ordinary_repository_resolves_to_its_own_git_directory(tmp_path: Path) -> None:
    repo = tmp_path / "work" / "example"
    _init_ordinary_repo(repo)

    info = resolve_git_dir(repo, workspace_path="/workspace", authorized_roots=[tmp_path / "work"])

    assert info.host_path == (repo / ".git").resolve()
    assert info.container_path == "/workspace/.git"
    assert info.is_ordinary_in_tree is True


# --- resolve_git_dir: gitfile (linked worktree / submodule) ------------------------


def test_gitfile_with_absolute_gitdir_resolves(tmp_path: Path) -> None:
    root = tmp_path / "work"
    main_git = root / "main-repo" / ".git"
    main_git.mkdir(parents=True)
    worktree_gitdir = main_git / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")

    checkout = root / "feature-worktree"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {worktree_gitdir}\n")

    info = resolve_git_dir(checkout, workspace_path="/workspace", authorized_roots=[root])

    assert info.host_path == main_git.resolve()
    assert info.container_path == str(main_git.resolve())
    assert info.is_ordinary_in_tree is False


def test_gitfile_with_relative_gitdir_resolves_relative_to_the_gitfile(tmp_path: Path) -> None:
    root = tmp_path / "work"
    main_git = root / "main-repo" / ".git"
    worktree_gitdir = main_git / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")

    checkout = root / "feature-worktree"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: ../main-repo/.git/worktrees/feature\n")

    info = resolve_git_dir(checkout, workspace_path="/workspace", authorized_roots=[root])
    assert info.host_path == main_git.resolve()


def test_gitfile_without_commondir_uses_the_gitdir_itself(tmp_path: Path) -> None:
    """Git's own default when no `commondir` file is present: the gitdir
    already is the common directory (typical for a plain submodule)."""
    root = tmp_path / "work"
    submodule_gitdir = root / "main-repo" / ".git" / "modules" / "sub"
    submodule_gitdir.mkdir(parents=True)

    checkout = root / "main-repo" / "sub"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {submodule_gitdir}\n")

    info = resolve_git_dir(checkout, workspace_path="/workspace", authorized_roots=[root])
    assert info.host_path == submodule_gitdir.resolve()


def test_gitfile_target_outside_authorized_roots_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    outside = tmp_path / "outside" / ".git"
    outside.mkdir(parents=True)

    checkout = root / "checkout"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {outside}\n")

    with pytest.raises(PathOutsideRepositoryError):
        resolve_git_dir(checkout, workspace_path="/workspace", authorized_roots=[root])


def test_missing_or_malformed_git_entry_is_rejected(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("not a gitfile at all\n")

    with pytest.raises(PathOutsideRepositoryError):
        resolve_git_dir(checkout, workspace_path="/workspace", authorized_roots=[tmp_path])


# --- build_workspace_mounts ---------------------------------------------------------


def test_read_write_ordinary_repo_needs_only_the_working_tree_mount(tmp_path: Path) -> None:
    repo = tmp_path / "work" / "example"
    _init_ordinary_repo(repo)

    mounts = build_workspace_mounts(
        host_repository_path=repo,
        workspace_path="/workspace",
        git_dir_mode="read-write",
        authorized_roots=[tmp_path / "work"],
    )

    assert len(mounts) == 1
    assert mounts[0]["Target"] == "/workspace"
    assert mounts[0]["ReadOnly"] is False


def test_read_write_gitfile_repo_needs_a_second_readwrite_mount(tmp_path: Path) -> None:
    root = tmp_path / "work"
    main_git = root / "main-repo" / ".git"
    main_git.mkdir(parents=True)
    checkout = root / "worktree"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {main_git}\n")

    mounts = build_workspace_mounts(
        host_repository_path=checkout,
        workspace_path="/workspace",
        git_dir_mode="read-write",
        authorized_roots=[root],
    )

    assert len(mounts) == 2
    git_mount = mounts[1]
    assert git_mount["Target"] == str(main_git.resolve())
    assert git_mount["ReadOnly"] is False


def test_read_only_mode_adds_a_readonly_git_mount(tmp_path: Path) -> None:
    repo = tmp_path / "work" / "example"
    _init_ordinary_repo(repo)

    mounts = build_workspace_mounts(
        host_repository_path=repo,
        workspace_path="/workspace",
        git_dir_mode="read-only",
        authorized_roots=[tmp_path / "work"],
    )

    assert len(mounts) == 2
    assert mounts[1]["Target"] == "/workspace/.git"
    assert mounts[1]["ReadOnly"] is True
    assert mounts[1]["Type"] == "bind"


def test_none_mode_masks_git_with_an_empty_readonly_tmpfs(tmp_path: Path) -> None:
    repo = tmp_path / "work" / "example"
    _init_ordinary_repo(repo)

    mounts = build_workspace_mounts(
        host_repository_path=repo,
        workspace_path="/workspace",
        git_dir_mode="none",
        authorized_roots=[tmp_path / "work"],
    )

    assert len(mounts) == 2
    assert mounts[1]["Target"] == "/workspace/.git"
    assert mounts[1]["Type"] == "tmpfs"
    assert mounts[1]["ReadOnly"] is True


def test_mounts_never_exceed_working_tree_plus_one_git_mount(tmp_path: Path) -> None:
    """§5.4: mount nothing beyond the explicit list — no more than the
    working tree and at most one Git-directory mount."""
    repo = tmp_path / "work" / "example"
    _init_ordinary_repo(repo)
    for mode in ("read-write", "read-only", "none"):
        mounts = build_workspace_mounts(
            host_repository_path=repo,
            workspace_path="/workspace",
            git_dir_mode=mode,  # type: ignore[arg-type]
            authorized_roots=[tmp_path / "work"],
        )
        assert len(mounts) <= 2


# --- build_known_hosts_mount (§19.3, S24) ------------------------------------------


def test_build_known_hosts_mount_is_read_only_at_the_fixed_path(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("github.com ssh-ed25519 AAAA...\n")

    mount = build_known_hosts_mount(known_hosts)

    assert mount["Target"] == SSH_KNOWN_HOSTS_CONTAINER_PATH
    assert mount["Source"] == str(known_hosts.resolve())
    assert mount["Type"] == "bind"
    assert mount["ReadOnly"] is True


def test_build_known_hosts_mount_container_path_is_outside_the_workspace() -> None:
    """§19.3: never inside the bind mount."""
    mount = build_known_hosts_mount(Path("/somewhere/known_hosts"))
    assert not mount["Target"].startswith("/workspace")
