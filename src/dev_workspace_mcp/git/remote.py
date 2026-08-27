"""Remote identity validation (§20.2, §7.3) — S25.

Before any structured operation that contacts a remote (`git_pull`,
`git_push`, S26/S27), the daemon validates the configured remote the same
way `git_commit`'s own preconditions are validated (S22): host-side,
against the bind-mounted working tree, before anything runs in-container
or touches the network. This module is that validation's shared core — it
registers no MCP tool of its own; `git_pull`/`git_push` call
`validate_remote_identity` as their first precondition, then invoke Git
in-container only once it returns.

There is deliberately no parameter here (or anywhere in this module) for
a raw `--upload-pack`, `--receive-pack`, or `--exec` value (§20.2's final
paragraph) — validation only ever takes a remote *name*, never
client-supplied Git flags, so there is no path through this module for
one to reach a real `git` invocation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from dev_workspace_mcp.errors import GitRemoteNotAllowedError
from dev_workspace_mcp.git.service import run_git_host_side

#: §20.3's own backstop, reused here defensively: passed on every git
#: invocation this module makes, so a scheme-check bug could not itself
#: let `ext::` reach a local command. `git remote get-url` never invokes a
#: transport in the first place, but there is no reason to omit it.
PROTOCOL_EXT_DISABLED_ARGS = ["-c", "protocol.ext.allow=never"]

#: The three documented remote forms (§7.3), anchored to `github.com`
#: specifically — repositories (§7) are GitHub-hosted, and validation only
#: ever needs to recognize *that* host's own forms, not arbitrary ones.
_HTTPS_RE = re.compile(
    r"^https://(?:[^@/]+@)?github\.com/(?P<owner_repo>[^/]+/[^/]+?)(?:\.git)?/?$"
)
_SSH_SCHEME_RE = re.compile(
    r"^ssh://(?:[^@/]+@)?github\.com(?::\d+)?/(?P<owner_repo>[^/]+/[^/]+?)(?:\.git)?/?$"
)
_SCP_LIKE_RE = re.compile(r"^(?:[^@/]+@)?github\.com:(?P<owner_repo>[^/]+/[^/]+?)(?:\.git)?/?$")

UrlScheme = Literal["https", "ssh", "ext", "file", "other"]


def resolve_remote_url(repo_path: Path, remote: str) -> str:
    """§20.2 step 1: `git remote get-url <name>`, host-side — the same
    precondition-read pattern `git_commit` already uses for status and
    interrupted-operation checks (§20.1), against the bind-mounted working
    tree rather than starting a container just to read config.

    Raises `GitRemoteNotAllowedError` if `remote` is not configured at
    all: there is nothing to validate, and a structured operation has no
    business inventing a remote."""
    result = run_git_host_side(
        repo_path, [*PROTOCOL_EXT_DISABLED_ARGS, "remote", "get-url", remote]
    )
    if not result.ok:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} is not configured: {result.stderr.strip()}",
            details={"remote": remote},
        )
    return result.stdout.strip()


def classify_url_scheme(url: str) -> UrlScheme:
    """§20.2 steps 2-3: what transport this URL string would invoke.
    `ext` and `file` are named explicitly in the spec and rejected
    unconditionally by every caller of this module, regardless of
    `allowed_url_schemes` — either can run an arbitrary local command or
    read an arbitrary local path, which is a different (and worse) hazard
    than merely reaching an unauthorized *remote* repository."""
    if url.startswith("ext::"):
        return "ext"
    if url.startswith("file://"):
        return "file"
    if url.startswith("https://"):
        return "https"
    if url.startswith("ssh://"):
        return "ssh"
    if _SCP_LIKE_RE.match(url):
        # `git@github.com:owner/repo.git` — no scheme prefix at all, but
        # Git itself treats this as SSH (§7.3's second example form).
        return "ssh"
    return "other"


def normalize_to_owner_repo(url: str) -> str | None:
    """§7.3: normalize any of the three documented remote forms to
    `owner/repo`. `None` for a URL that is not a recognized GitHub form —
    a caller SHALL treat that the same as a mismatch, not as "unknown, so
    allow"."""
    for pattern in (_HTTPS_RE, _SSH_SCHEME_RE, _SCP_LIKE_RE):
        m = pattern.match(url)
        if m:
            return m.group("owner_repo")
    return None


def validate_remote_identity(
    repo_path: Path,
    *,
    remote: str,
    allowed_url_schemes: list[Literal["ssh", "https"]],
    configured_github: str,
    allowlisted_repositories: list[str],
    expected_remote_repository: str | None,
) -> str:
    """§20.2 steps 1-6, run before any structured operation contacts
    `remote`. Returns the normalized `owner/repo` identity on success;
    raises `GitRemoteNotAllowedError` on the first check that fails.
    Callers invoke Git in-container only after this returns — exactly how
    `git_commit` runs its mutating commands only once every precondition
    in §20.6 has already passed.
    """
    url = resolve_remote_url(repo_path, remote)

    scheme = classify_url_scheme(url)
    if scheme in ("ext", "file", "other") or scheme not in allowed_url_schemes:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} ({url!r}) uses a transport not permitted (§20.2): {scheme!r}",
            details={"remote": remote, "url": url, "scheme": scheme},
        )

    normalized = normalize_to_owner_repo(url)
    if normalized is None:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} ({url!r}) does not resolve to a recognized "
            "owner/repo identity (§7.3)",
            details={"remote": remote, "url": url},
        )

    authorized = {configured_github, *allowlisted_repositories}
    if normalized not in authorized:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} resolves to {normalized!r}, which is neither "
            f"the repository's configured identity ({configured_github!r}) "
            f"nor listed in github.repositories (§20.2)",
            details={
                "remote": remote,
                "resolved": normalized,
                "configured_github": configured_github,
                "allowlisted_repositories": allowlisted_repositories,
            },
        )

    if expected_remote_repository is not None and normalized != expected_remote_repository:
        raise GitRemoteNotAllowedError(
            f"remote {remote!r} resolves to {normalized!r}, which does not "
            f"match the caller-supplied expected_remote_repository "
            f"({expected_remote_repository!r}) — honoured regardless of "
            "server-side configuration (§20.2)",
            details={
                "remote": remote,
                "resolved": normalized,
                "expected_remote_repository": expected_remote_repository,
            },
        )

    return normalized
