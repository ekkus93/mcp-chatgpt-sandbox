"""S25: remote identity validation (§20.2, §7.3).

`validate_remote_identity` is the mechanism a future structured
`git_pull`/`git_push` (S26/S27) will call as their first precondition —
these tests exercise it directly, the same way §20.2 itself describes the
check ("before any structured operation that contacts a remote"), without
needing a running container or a real network. AT-009 (unauthorized
remote), AT-026 (`ext::` transport), and the validation half of AT-027
(key-scope mismatch) and AT-028 (SSH-form remote accepted) are covered
this way; the SSH-connectivity/GitHub-authentication halves of AT-027/
AT-028, and AT-030 (submodule fetch), require S23's key provisioning (D3,
blocked) and S26/S27's actual fetch/push respectively — see docs/TODO.md.
"""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import GitRemoteNotAllowedError
from dev_workspace_mcp.git.remote import (
    classify_url_scheme,
    normalize_to_owner_repo,
    resolve_remote_url,
    validate_remote_identity,
)


def _repo_with_remote(path: Path, url: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=path, check=True)
    return path


# --- classify_url_scheme -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo.git", "https"),
        ("https://github.com/owner/repo", "https"),
        ("ssh://git@github.com/owner/repo.git", "ssh"),
        ("git@github.com:owner/repo.git", "ssh"),
        ("ext::sh -c 'touch /tmp/pwned'", "ext"),
        ("file:///etc/passwd", "file"),
        ("git://github.com/owner/repo.git", "other"),
        ("not-a-url-at-all", "other"),
    ],
)
def test_classify_url_scheme(url: str, expected: str) -> None:
    assert classify_url_scheme(url) == expected


# --- normalize_to_owner_repo (§7.3's three documented forms) -----------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo",
        "git@github.com:owner/repo.git",
        "git@github.com:owner/repo",
        "ssh://git@github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo",
    ],
)
def test_normalize_to_owner_repo_accepts_every_documented_form(url: str) -> None:
    assert normalize_to_owner_repo(url) == "owner/repo"


def test_normalize_to_owner_repo_rejects_a_non_github_host() -> None:
    assert normalize_to_owner_repo("https://gitlab.com/owner/repo.git") is None


def test_normalize_to_owner_repo_rejects_ext_transport() -> None:
    assert normalize_to_owner_repo("ext::sh -c 'echo hi'") is None


# --- resolve_remote_url -------------------------------------------------------------


def test_resolve_remote_url_reads_the_configured_url(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "git@github.com:owner/repo.git")
    assert resolve_remote_url(repo, "origin") == "git@github.com:owner/repo.git"


def test_resolve_remote_url_raises_for_an_unconfigured_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    with pytest.raises(GitRemoteNotAllowedError):
        resolve_remote_url(repo, "origin")


# --- validate_remote_identity --------------------------------------------------------


def test_matching_https_remote_is_accepted(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    identity = validate_remote_identity(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=[],
        expected_remote_repository=None,
    )
    assert identity == "owner/repo"


def test_at_028_matching_ssh_form_remote_is_accepted(tmp_path: Path) -> None:
    """AT-028 (validation half): the SCP-like SSH form is normalized and
    accepted exactly like the other two forms, not treated as unusual."""
    repo = _repo_with_remote(tmp_path / "repo", "git@github.com:owner/repo.git")
    identity = validate_remote_identity(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=[],
        expected_remote_repository=None,
    )
    assert identity == "owner/repo"


def test_remote_matching_the_github_repositories_allowlist_is_accepted(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/other-owner/other-repo.git")
    identity = validate_remote_identity(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=["other-owner/other-repo"],
        expected_remote_repository=None,
    )
    assert identity == "other-owner/other-repo"


def test_at_009_and_at_027_unauthorized_remote_is_rejected(tmp_path: Path) -> None:
    """AT-009 (unauthorized remote) and AT-027's validation half (key
    scope mismatch on a second repository): both manifest identically at
    this layer — a remote that resolves to an owner/repo the repository
    is not configured for fails *before* anything is invoked against it,
    which is what makes a structured push fail at all under AT-009/027."""
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/attacker/unrelated.git")
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
    assert excinfo.value.details["resolved"] == "attacker/unrelated"


def test_at_026_ext_transport_is_rejected_before_any_authenticated_operation(
    tmp_path: Path,
) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "ext::sh -c 'touch /tmp/pwned'")
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
    assert excinfo.value.details["scheme"] == "ext"


def test_file_transport_is_rejected(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "file:///etc/passwd")
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
    assert excinfo.value.details["scheme"] == "file"


def test_scheme_outside_the_configured_allowlist_is_rejected(tmp_path: Path) -> None:
    """Even a matching, well-formed owner/repo is refused if its scheme
    isn't in `git_policy.allowed_url_schemes` — an operator narrowing that
    list to `["ssh"]` SHALL actually stop an HTTPS remote."""
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    with pytest.raises(GitRemoteNotAllowedError):
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )


def test_expected_remote_repository_mismatch_fails_even_when_server_allows_it(
    tmp_path: Path,
) -> None:
    """§20.2: a client-supplied `expected_remote_repository` mismatch
    SHALL fail regardless of server-side configuration — here the remote
    matches the server's own configured identity, and validation still
    fails because it doesn't match what the caller expected."""
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository="owner/a-different-repo",
        )
    assert excinfo.value.details["expected_remote_repository"] == "owner/a-different-repo"


def test_expected_remote_repository_match_is_accepted(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    identity = validate_remote_identity(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=[],
        expected_remote_repository="owner/repo",
    )
    assert identity == "owner/repo"


def test_non_github_host_is_rejected_even_with_an_allowed_scheme(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "https://gitlab.com/owner/repo.git")
    with pytest.raises(GitRemoteNotAllowedError):
        validate_remote_identity(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
