"""Remote protection verification against the GitHub REST API (§18.5,
§19.5, §20.4) — S28.

Uses `urllib.request` (stdlib) rather than adding an HTTP client
dependency for what is, in the MVP, one GET request. `api_auth: "app"`
(a GitHub App installation) is not implemented — it needs JWT signing
and an installation-token exchange, real work with no way to verify it
here (D2 was decided "no live GitHub credential for now"); it degrades
to `unverified` with a clear reason, the same honest degradation an
absent credential already gets.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dev_workspace_mcp.config.models import GithubConfig

_TIMEOUT_SECONDS = 10.0

RemoteProtectionStatus = Literal["verified", "missing", "unverified"]


@dataclass(frozen=True, slots=True)
class RemoteProtectionResult:
    """§20.4's exact reported shape, plus `reason` — populated only for
    `missing`/`unverified`, explaining *why* rather than leaving a client
    to guess (§18.5: "the reason reported")."""

    status: RemoteProtectionStatus
    default_branch: str | None
    force_push_blocked: bool
    deletion_blocked: bool
    requires_pull_request: bool
    checked_at: str
    reason: str | None = None


def _unverified(default_branch: str | None, reason: str) -> RemoteProtectionResult:
    return RemoteProtectionResult(
        status="unverified",
        default_branch=default_branch,
        force_push_blocked=False,
        deletion_blocked=False,
        requires_pull_request=False,
        checked_at=datetime.now(UTC).isoformat(),
        reason=reason,
    )


@dataclass
class RemoteProtectionReportOutput:
    """§20.4's exact reported `remote_protection` shape — the MCP
    tool-output form, shared by `workspace_info`/`workspace_open`
    (`tools/workspace.py`) and `git_push` (`tools/git.py`) rather than
    each tool file defining its own copy."""

    status: str
    default_branch: str | None = None
    force_push_blocked: bool = False
    deletion_blocked: bool = False
    requires_pull_request: bool = False
    checked_at: str | None = None
    reason: str | None = None
    """Populated for "missing"/"unverified" — why, not just that (§18.5)."""


def to_report_output(result: RemoteProtectionResult | None) -> RemoteProtectionReportOutput:
    """`None` (never verified for this repository yet, e.g. `git_push`
    called before any `workspace_open`) reports the same honest
    "unverified" shape a missing credential does."""
    if result is None:
        return RemoteProtectionReportOutput(status="unverified", reason="not yet checked")
    return RemoteProtectionReportOutput(
        status=result.status,
        default_branch=result.default_branch,
        force_push_blocked=result.force_push_blocked,
        deletion_blocked=result.deletion_blocked,
        requires_pull_request=result.requires_pull_request,
        checked_at=result.checked_at,
        reason=result.reason,
    )


def read_api_token(path: Path) -> str:
    return path.read_text().strip()


def serialize_remote_protection(result: RemoteProtectionResult) -> str:
    """`repository.remote_protection`'s on-disk form (§67-style JSON
    text column, matching `audit_event.metadata`'s own convention)."""
    return json.dumps(asdict(result), sort_keys=True)


def deserialize_remote_protection(raw: str | None) -> RemoteProtectionResult | None:
    """`None` for a repository that has never had `workspace_open` run
    against it yet (the column itself, or the row, may not exist) — the
    honest "nothing verified yet" state, not an error."""
    if raw is None:
        return None
    return RemoteProtectionResult(**json.loads(raw))


def _query_branch_protection(
    *, api_base_url: str, token: str, owner_repo: str, branch: str
) -> dict[str, Any] | None:
    """`GET /repos/{owner}/{repo}/branches/{branch}/protection`. Returns
    the parsed JSON body on `200` (protection configured), `None` on
    `404` (GitHub's own signal for "not protected" on this endpoint —
    not an error). Any other status, or a network failure, raises
    `urllib.error.URLError`/`HTTPError` for the caller to degrade on."""
    url = f"{api_base_url}/repos/{owner_repo}/branches/{branch}/protection"
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return json.loads(body)  # type: ignore[no-any-return]


def verify_remote_protection(
    *,
    github_config: GithubConfig,
    owner_repo: str | None,
    default_branch: str | None,
    checks: Sequence[str],
) -> RemoteProtectionResult:
    """§18.5/§20.4: the daemon-side verification `workspace_open` calls.
    Degrades to `unverified` — never raises — for every reason short of a
    genuine "protection confirmed absent" answer from a real, credentialed
    query: no credential configured, no resolvable GitHub identity, no
    resolvable default branch, an unimplemented auth mode, or the query
    itself failing for any reason (auth rejected, network error, GitHub
    down). A diagnostic feature SHALL NOT be able to fail `workspace_open`
    by itself — `require_remote_protection: deny`'s own enforcement, not
    this function, is what may still refuse the operation, and only for
    a *confirmed* `missing`, never for `unverified` (§18.5's explicit
    "deny SHALL be treated as warn" rule for the no-credential case)."""
    if owner_repo is None:
        return _unverified(default_branch, "repository has no configured GitHub identity")
    if default_branch is None:
        return _unverified(default_branch, "default branch could not be determined")
    if github_config.api_auth == "none":
        return _unverified(default_branch, "no API credential configured (§19.5)")
    if github_config.api_auth == "app":
        return _unverified(default_branch, "api_auth: app is not implemented")
    if github_config.api_token_path is None:
        return _unverified(default_branch, "api_auth: token but no api_token_path configured")

    try:
        token = read_api_token(github_config.api_token_path)
    except OSError as exc:
        return _unverified(default_branch, f"could not read api_token_path: {exc}")

    try:
        protection = _query_branch_protection(
            api_base_url=github_config.api_base_url,
            token=token,
            owner_repo=owner_repo,
            branch=default_branch,
        )
    except urllib.error.HTTPError as exc:
        return _unverified(default_branch, f"GitHub API returned HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _unverified(default_branch, f"GitHub API request failed: {exc}")

    checked_at = datetime.now(UTC).isoformat()
    if protection is None:
        return RemoteProtectionResult(
            status="missing",
            default_branch=default_branch,
            force_push_blocked=False,
            deletion_blocked=False,
            requires_pull_request=False,
            checked_at=checked_at,
            reason="no branch protection or ruleset configured for the default branch",
        )

    force_push_blocked = not bool(
        (protection.get("allow_force_pushes") or {}).get("enabled", False)
    )
    deletion_blocked = not bool((protection.get("allow_deletions") or {}).get("enabled", False))
    requires_pull_request = protection.get("required_pull_request_reviews") is not None

    #: `checks` (`git_policy.remote_protection_checks`) narrows which of
    #: the *configured* protections actually count toward "verified" —
    #: an operator who only cares about force-push protection should not
    #: get `missing` just because deletion happens to be unblocked.
    relevant = {
        "force_push": force_push_blocked,
        "deletion": deletion_blocked,
    }
    all_relevant_checks_satisfied = all(relevant.get(check, True) for check in checks)

    return RemoteProtectionResult(
        status="verified" if all_relevant_checks_satisfied else "missing",
        default_branch=default_branch,
        force_push_blocked=force_push_blocked,
        deletion_blocked=deletion_blocked,
        requires_pull_request=requires_pull_request,
        checked_at=checked_at,
        reason=None
        if all_relevant_checks_satisfied
        else "configured protection does not cover every checked operation",
    )
