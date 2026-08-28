"""S25: remote identity validation (§20.2, §7.3).

`validate_remote_identity` is the mechanism `git_pull`/`git_push` call as
their first precondition — these tests exercise it directly, the same
way §20.2 itself describes the check ("before any structured operation
that contacts a remote"), without needing a running container or a real
network. AT-009 (unauthorized remote), AT-026 (`ext::` transport), and
the validation half of AT-027 (key-scope mismatch) and AT-028 (SSH-form
remote accepted) are covered this way; the SSH-connectivity/GitHub-
authentication halves of AT-027/AT-028, and AT-030 (submodule fetch),
require S23's key provisioning (D3, blocked) and S26/S27's actual
fetch/push respectively — see docs/TODO.md.

H36 (`docs/HARDENING_TODO.md`, 7/8): `resolve_remote_url`/
`resolve_remote_push_url` themselves now need a running workspace's
container context (they run `git remote get-url` in-container).
`_resolve_url_of`/`_resolve_push_url_of` below read the same
configuration directly, host-side, real `git remote get-url [--push]`
calls — these tests are about the *validation* logic
(`_validate_url_identity`, imported directly the same way this test
suite already imports other underscore-prefixed internals like
`git/service.py`'s `_discover_code_executing_overrides`), not about
where the URL read happens, so replicating that one trivial read
host-side keeps the whole suite fast and Docker-free.
`tests/workspace/test_git_pull_tool.py`/`test_git_push_tool.py` cover
`validate_remote_identity`/`validate_remote_identity_for_push`
end-to-end, in-container.
"""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.errors import GitRemoteNotAllowedError
from dev_workspace_mcp.git.remote import (
    ValidatedPushRemote,
    _validate_url_identity,
    classify_url_scheme,
    normalize_to_owner_repo,
)


def _repo_with_remote(path: Path, url: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=path, check=True)
    return path


def _resolve_url_of(repo: Path, remote: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", remote], cwd=repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} is not configured: {result.stderr.strip()}",
            details={"remote": remote},
        )
    return result.stdout.strip()


def _resolve_push_url_of(repo: Path, remote: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "--push", remote], cwd=repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} has no push URL configured: {result.stderr.strip()}",
            details={"remote": remote},
        )
    return result.stdout.strip()


def _validate_remote_identity_of(
    repo: Path,
    *,
    remote: str,
    allowed_url_schemes: list[str],
    configured_github: str,
    allowlisted_repositories: list[str],
    expected_remote_repository: str | None,
) -> str:
    url = _resolve_url_of(repo, remote)
    return _validate_url_identity(
        url,
        remote=remote,
        allowed_url_schemes=allowed_url_schemes,
        configured_github=configured_github,
        allowlisted_repositories=allowlisted_repositories,
        expected_remote_repository=expected_remote_repository,
    )


def _validate_remote_identity_for_push_of(
    repo: Path,
    *,
    remote: str,
    allowed_url_schemes: list[str],
    configured_github: str,
    allowlisted_repositories: list[str],
    expected_remote_repository: str | None,
) -> ValidatedPushRemote:
    fetch_url = _resolve_url_of(repo, remote)
    fetch_normalized = _validate_url_identity(
        fetch_url,
        remote=remote,
        allowed_url_schemes=allowed_url_schemes,
        configured_github=configured_github,
        allowlisted_repositories=allowlisted_repositories,
        expected_remote_repository=expected_remote_repository,
    )
    push_url = _resolve_push_url_of(repo, remote)
    if push_url == fetch_url:
        return ValidatedPushRemote(repository=fetch_normalized, push_url=push_url)
    push_normalized = _validate_url_identity(
        push_url,
        remote=remote,
        allowed_url_schemes=allowed_url_schemes,
        configured_github=configured_github,
        allowlisted_repositories=allowlisted_repositories,
        expected_remote_repository=expected_remote_repository,
    )
    return ValidatedPushRemote(repository=push_normalized, push_url=push_url)


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
    assert _resolve_url_of(repo, "origin") == "git@github.com:owner/repo.git"


def test_resolve_remote_url_raises_for_an_unconfigured_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    with pytest.raises(GitRemoteNotAllowedError):
        _resolve_url_of(repo, "origin")


# --- validate_remote_identity --------------------------------------------------------


def test_matching_https_remote_is_accepted(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    identity = _validate_remote_identity_of(
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
    identity = _validate_remote_identity_of(
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
    identity = _validate_remote_identity_of(
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
        _validate_remote_identity_of(
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
        _validate_remote_identity_of(
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
        _validate_remote_identity_of(
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
        _validate_remote_identity_of(
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
        _validate_remote_identity_of(
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
    identity = _validate_remote_identity_of(
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
        _validate_remote_identity_of(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )


# --- resolve_remote_push_url / validate_remote_identity_for_push (HR-014) ----------


def _repo_with_pushurl(path: Path, *, fetch_url: str, push_url: str) -> Path:
    repo = _repo_with_remote(path, fetch_url)
    subprocess.run(
        ["git", "config", "--local", "remote.origin.pushurl", push_url], cwd=repo, check=True
    )
    return repo


def test_resolve_remote_push_url_reads_the_configured_pushurl(tmp_path: Path) -> None:
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://github.com/owner/different-repo.git",
    )
    assert _resolve_push_url_of(repo, "origin") == "https://github.com/owner/different-repo.git"


def test_resolve_remote_push_url_falls_back_to_fetch_url_when_unset(tmp_path: Path) -> None:
    """Git itself falls back to the fetch URL for `get-url --push` when no
    `remote.<name>.pushurl` is configured — the common case, where push
    and fetch legitimately go to the same place."""
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    assert _resolve_push_url_of(repo, "origin") == "https://github.com/owner/repo.git"


def test_h12_pushurl_redirect_to_an_unauthorized_host_is_rejected(tmp_path: Path) -> None:
    """A real regression: `remote.origin.pushurl` pointed at a different
    host than the validated fetch URL. The old `validate_remote_identity`
    (fetch-only) approved this remote; `git push` would have actually
    contacted the pushurl host instead. `validate_remote_identity_for_push`
    SHALL reject it."""
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://evil.example.com/evil/other.git",
    )
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        _validate_remote_identity_for_push_of(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
    assert excinfo.value.details["url"] == "https://evil.example.com/evil/other.git"


def test_h12_pushurl_redirect_to_an_unauthorized_sibling_repo_is_rejected(tmp_path: Path) -> None:
    """Same host, same owner, different repo — a subtler redirect than a
    different host entirely, and still SHALL be rejected."""
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://github.com/owner/different-repo.git",
    )
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        _validate_remote_identity_for_push_of(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=[],
            expected_remote_repository=None,
        )
    assert excinfo.value.details["resolved"] == "owner/different-repo"


def test_h12_pushurl_redirect_to_an_allowlisted_repo_is_accepted(tmp_path: Path) -> None:
    """The push URL differs from the fetch URL but resolves to an
    identity the operator has explicitly allowlisted — SHALL be accepted,
    and the returned identity SHALL be the push destination's, not the
    fetch URL's (the caller uses this to name the actual destination in
    the audit record, per HR-014)."""
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://github.com/other-owner/other-repo.git",
    )
    validated = _validate_remote_identity_for_push_of(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=["other-owner/other-repo"],
        expected_remote_repository=None,
    )
    assert validated.repository == "other-owner/other-repo"
    assert validated.push_url == "https://github.com/other-owner/other-repo.git"


def test_h12_matching_pushurl_is_accepted_same_as_plain_case(tmp_path: Path) -> None:
    """Push URL explicitly configured but identical to the fetch URL —
    ordinary case, not a redirect — SHALL still validate normally."""
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://github.com/owner/repo.git",
    )
    validated = _validate_remote_identity_for_push_of(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=[],
        expected_remote_repository=None,
    )
    assert validated.repository == "owner/repo"
    assert validated.push_url == "https://github.com/owner/repo.git"


def test_h12_no_pushurl_configured_behaves_like_plain_validate_remote_identity(
    tmp_path: Path,
) -> None:
    """No `pushurl` at all (the common case) — push and fetch URL are the
    same string, and validation behaves exactly like
    `validate_remote_identity`."""
    repo = _repo_with_remote(tmp_path / "repo", "https://github.com/owner/repo.git")
    validated = _validate_remote_identity_for_push_of(
        repo,
        remote="origin",
        allowed_url_schemes=["ssh", "https"],
        configured_github="owner/repo",
        allowlisted_repositories=[],
        expected_remote_repository=None,
    )
    assert validated.repository == "owner/repo"
    assert validated.push_url == "https://github.com/owner/repo.git"


def test_h12_expected_remote_repository_checked_against_push_destination(
    tmp_path: Path,
) -> None:
    """`expected_remote_repository` (client-supplied, honoured regardless
    of server config) SHALL be checked against the actual push
    destination, not just the fetch URL — otherwise a caller who pinned
    an expected repository could still be redirected by a pushurl."""
    repo = _repo_with_pushurl(
        tmp_path / "repo",
        fetch_url="https://github.com/owner/repo.git",
        push_url="https://github.com/owner/different-repo.git",
    )
    with pytest.raises(GitRemoteNotAllowedError) as excinfo:
        _validate_remote_identity_for_push_of(
            repo,
            remote="origin",
            allowed_url_schemes=["ssh", "https"],
            configured_github="owner/repo",
            allowlisted_repositories=["owner/different-repo"],
            expected_remote_repository="owner/repo",
        )
    assert excinfo.value.details["expected_remote_repository"] == "owner/repo"
