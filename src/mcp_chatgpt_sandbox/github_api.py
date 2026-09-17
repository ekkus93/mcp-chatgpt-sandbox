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

from mcp_chatgpt_sandbox.config.models import GithubConfig

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


def _query_branch_rules(
    *, api_base_url: str, token: str, owner_repo: str, branch: str
) -> list[dict[str, Any]]:
    """`GET /repos/{owner}/{repo}/rules/branches/{branch}` — H32
    (`docs/HARDENING_SPEC.md` §9's §18.5 row, FR-032): the *unified*
    rules endpoint, not the classic branch-protection-only one this
    function used to query. GitHub's own documentation states rulesets
    and classic branch protection "work alongside each other, and all
    applicable rules are enforced" for a branch — this endpoint reports
    that combined, already-translated set as one array of rule objects
    (`{"type": "deletion", ...}`, `{"type": "non_fast_forward", ...}`,
    `{"type": "pull_request", ...}`, and 20 other documented types),
    which is why this replaces the classic query entirely rather than
    supplementing it: rulesets are the *only* mechanism available on a
    GitHub Free private repository, where the classic endpoint 404s
    unconditionally regardless of whether protection exists.

    GitHub documents only `200` as a possible response for this
    endpoint — unlike the classic endpoint's own `404` (its documented
    "not protected" signal), a `404` here has no such documented
    meaning and is NOT treated as one: it is left to propagate as an
    `HTTPError`, same as any other unexpected status, so the caller
    degrades to `unverified` ("not visible to this token") rather than
    asserting `missing` ("confirmed absent") without evidence — exactly
    the distinction §18.5's amendment row requires. A genuinely
    rule-free branch is reported as `200` with an empty array, which
    *is* real evidence of absence and is handled by the caller.

    RR-008 (`docs/REMEDIATION_SPEC.md` §5): `?per_page=100` — GitHub's
    documented maximum page size — rather than its default of 30.
    Not a partial mitigation: the response is the combined, deduplicated
    set of *rule types* that apply to the branch (this docstring's own
    23-type ceiling above), never one entry per contributing ruleset, so
    a single page of 100 is unconditionally enough; this isn't paginating
    around a growing collection, it's sizing the one page to exceed a
    fixed, documented upper bound.

    `json.loads(body)` — and this function's own return type — assume
    the parsed body is a JSON array of objects, per GitHub's documented
    shape. Reproduced empirically that this assumption silently held
    even when wrong: a `200` response body that is a JSON *object*
    (e.g. a misconfigured `api_base_url` pointing at a different API
    entirely) iterates as its string *keys* rather than raising here, so
    the `AttributeError` this raises instead is deliberately immediate
    and specific, not deferred to whatever the caller happens to do with
    each "rule" next. A non-JSON body (`json.loads` itself raising
    `json.JSONDecodeError`, a `ValueError` subclass) is a related,
    equally real crash reproduced the same way and caught by the same
    `except ValueError` the caller already needs for this."""
    url = f"{api_base_url}/repos/{owner_repo}/rules/branches/{branch}?per_page=100"
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        body = response.read()
    parsed = json.loads(body)
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError(
            f"expected a JSON array of rule objects from {url!r}, got "
            f"{type(parsed).__name__} instead"
        )
    return parsed


def verify_remote_protection(
    *,
    github_config: GithubConfig,
    owner_repo: str | None,
    default_branch: str | None,
    checks: Sequence[str],
) -> RemoteProtectionResult:
    """§18.5/§20.4: produce a three-state remote-protection result.

    Verification itself remains diagnostic and does not raise for ordinary
    inability to verify. Missing credentials, unresolved repository/default
    branch identity, an unimplemented auth mode, authorization failures,
    network failures, and malformed responses all become `unverified` with
    the exact reason preserved. A successful query that proves the configured
    checks absent becomes `missing`; only proven satisfaction is `verified`.

    G3R-003 deliberately separates this reporting layer from enforcement:
    `workspace_open` interprets `git_policy.require_remote_protection`.
    `warn` allows `missing` and `unverified` while reporting them; `deny`
    blocks either state before SSH-key provisioning or Docker work. Keeping
    `unverified` distinct here is therefore load-bearing — enforcement may
    fail closed without falsely claiming that protection was confirmed absent.
    """
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
        rules = _query_branch_rules(
            api_base_url=github_config.api_base_url,
            token=token,
            owner_repo=owner_repo,
            branch=default_branch,
        )
    except urllib.error.HTTPError as exc:
        # H32: includes a `404` — the unified rules endpoint documents
        # no "not protected" meaning for it (see `_query_branch_rules`'s
        # own docstring), so it degrades honestly to `unverified`
        # ("not visible to this token") rather than asserting `missing`.
        return _unverified(default_branch, f"GitHub API returned HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _unverified(default_branch, f"GitHub API request failed: {exc}")
    except ValueError as exc:
        # RR-008 (`docs/REMEDIATION_SPEC.md` §5): `_query_branch_rules`
        # raises `ValueError` for a `200` response whose body isn't the
        # documented JSON-array-of-objects shape — including, since
        # `json.JSONDecodeError` is itself a `ValueError`, a body that
        # isn't JSON at all. Reproduced empirically that either used to
        # propagate uncaught (`AttributeError`/`JSONDecodeError`),
        # violating this function's own "never raises" contract exactly
        # the way an `HTTPError`/`URLError` already can't.
        return _unverified(default_branch, f"GitHub API returned an unexpected response: {exc}")

    checked_at = datetime.now(UTC).isoformat()
    rule_types = {rule.get("type") for rule in rules}
    if not rule_types:
        return RemoteProtectionResult(
            status="missing",
            default_branch=default_branch,
            force_push_blocked=False,
            deletion_blocked=False,
            requires_pull_request=False,
            checked_at=checked_at,
            # A real, confirmed answer (§18.5) — `200` with an empty
            # array from the unified rules endpoint, not an unreachable
            # or forbidden query being assumed empty.
            reason="no branch protection or ruleset rules apply to the default branch",
        )

    #: GitHub's own rule-type names for exactly these three checks
    #: (`_query_branch_rules`'s docstring lists the full 23-type set;
    #: these are the only three §20.4's reported shape asks about).
    force_push_blocked = "non_fast_forward" in rule_types
    deletion_blocked = "deletion" in rule_types
    requires_pull_request = "pull_request" in rule_types

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
