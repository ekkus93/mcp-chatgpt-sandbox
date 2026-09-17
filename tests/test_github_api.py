"""S28: remote protection verification against the GitHub REST API
(§18.5, §19.5, §20.4).

Against a real local HTTP server standing in for `api.github.com` —
D2 was decided "no live GitHub credential for now", so there is no real
token to test against the genuine API with. The server is real, the HTTP
round-trip is real, the JSON parsing is real; only the hostname differs
from `github.com`, the same substitution `github_config.api_base_url`
exists to support (GitHub Enterprise Server operators need exactly this
knob for a different reason). This is the same pattern S24's AT-034 test
used: a real local `sshd` standing in for `github.com`'s.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.config.models import GithubConfig
from mcp_chatgpt_sandbox.github_api import verify_remote_protection


class _FakeGitHubHandler(BaseHTTPRequestHandler):
    #: set per-test via subclassing/attributes below
    response_status: int = 200
    response_body: object = None
    #: RR-008: overrides `response_body` when set, for a body that isn't
    #: JSON at all (a misconfigured `api_base_url` pointing at a
    #: different, non-API server) — `response_body` is always run through
    #: `json.dumps`, which can't produce malformed JSON.
    raw_response_body: bytes | None = None
    require_token: str | None = None
    requested_paths: list[str] = []  # noqa: RUF012 - test double, shared deliberately

    def do_GET(self) -> None:  # noqa: N802 - stdlib's own method name
        type(self).requested_paths.append(self.path)
        if self.require_token is not None:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {self.require_token}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"message": "Bad credentials"}')
                return
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.raw_response_body is not None:
            self.wfile.write(self.raw_response_body)
            return
        body = self.response_body if self.response_body is not None else {}
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args: object) -> None:  # silence stdlib's default access log
        pass


@pytest.fixture
def fake_github(request: pytest.FixtureRequest):
    """A real local HTTP server for one test, torn down after."""
    handler_cls = type(f"_Handler_{request.node.name}", (_FakeGitHubHandler,), {})
    handler_cls.requested_paths = []
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    yield base_url, handler_cls
    server.shutdown()
    thread.join(timeout=5)


def _github_config(base_url: str, token_path: Path, *, api_auth: str = "token") -> GithubConfig:
    return GithubConfig(api_auth=api_auth, api_token_path=token_path, api_base_url=base_url)


# --- degrade-to-unverified paths, no network call needed ---------------------------


def test_unverified_when_no_credential_configured() -> None:
    config = GithubConfig(api_auth="none")
    result = verify_remote_protection(
        github_config=config, owner_repo="owner/repo", default_branch="main", checks=[]
    )
    assert result.status == "unverified"
    assert "no API credential" in result.reason


def test_unverified_when_repository_has_no_github_identity() -> None:
    config = GithubConfig(api_auth="token", api_token_path=Path("/tmp/token"))
    result = verify_remote_protection(
        github_config=config, owner_repo=None, default_branch="main", checks=[]
    )
    assert result.status == "unverified"
    assert "no configured GitHub identity" in result.reason


def test_unverified_when_default_branch_unknown() -> None:
    config = GithubConfig(api_auth="token", api_token_path=Path("/tmp/token"))
    result = verify_remote_protection(
        github_config=config, owner_repo="owner/repo", default_branch=None, checks=[]
    )
    assert result.status == "unverified"
    assert "default branch" in result.reason


def test_unverified_when_api_auth_is_app_mode() -> None:
    config = GithubConfig(api_auth="app")
    result = verify_remote_protection(
        github_config=config, owner_repo="owner/repo", default_branch="main", checks=[]
    )
    assert result.status == "unverified"
    assert "not implemented" in result.reason


def test_unverified_when_token_file_is_missing(tmp_path: Path) -> None:
    config = _github_config("http://unused.invalid", tmp_path / "does-not-exist")
    result = verify_remote_protection(
        github_config=config, owner_repo="owner/repo", default_branch="main", checks=[]
    )
    assert result.status == "unverified"
    assert "could not read api_token_path" in result.reason


# --- real HTTP round-trips against the local fake server ---------------------------


def test_unverified_when_the_server_returns_404(tmp_path: Path, fake_github) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §18.5 row): the unified rules
    endpoint documents no "not protected" meaning for `404` — unlike
    the classic endpoint this used to query, a `404` here is "not
    visible to this token", not confirmed-absent evidence, and SHALL
    NOT be reported as `missing`."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 404
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "unverified"
    assert "404" in result.reason
    assert result.default_branch == "main"
    assert handler_cls.requested_paths == ["/repos/owner/repo/rules/branches/main?per_page=100"]


def test_rr008_requests_per_page_100_not_githubs_default_of_30(tmp_path: Path, fake_github) -> None:
    """RR-008 (`docs/REMEDIATION_SPEC.md` §5): the combined, deduplicated
    rule-type set this endpoint returns is bounded by GitHub's own
    documented ~23 rule types, comfortably inside a single 100-item
    page — 30 (GitHub's default) is not guaranteed to be."""
    base_url, handler_cls = fake_github
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=[],
    )

    assert handler_cls.requested_paths == ["/repos/owner/repo/rules/branches/main?per_page=100"]


def test_rr008_unverified_when_the_response_body_is_a_json_object_not_an_array(
    tmp_path: Path, fake_github
) -> None:
    """Reproduced empirically before this fix: a `200` whose body is a
    JSON *object* (e.g. a misconfigured `api_base_url` pointing at a
    different API entirely, or a future GitHub response-shape change)
    iterated as its string keys rather than rule objects, crashing with
    `AttributeError: 'str' object has no attribute 'get'` — escaping
    `verify_remote_protection`, `workspace_open`, and
    `translate_workspace_errors` uncaught, violating this function's own
    "never raises" contract."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = {"message": "not an array at all"}
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "unverified"
    assert "unexpected response" in result.reason
    assert result.default_branch == "main"


def test_rr008_unverified_when_a_list_item_is_not_an_object(tmp_path: Path, fake_github) -> None:
    """The array-of-*objects* half of the same shape guard: a list whose
    entries aren't dicts would crash `rule.get("type")` in the caller
    identically to the whole-body-is-an-object case above."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = ["not", "objects"]
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "unverified"
    assert "unexpected response" in result.reason


def test_rr008_unverified_when_the_response_body_is_not_json_at_all(
    tmp_path: Path, fake_github
) -> None:
    """The related crash this fix closes for free: `json.loads` itself
    raises `json.JSONDecodeError` (a `ValueError` subclass) for a
    genuinely non-JSON body — reproduced empirically against a real
    HTML response before this fix, uncaught for the same reason as the
    JSON-object case above."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.raw_response_body = b"<html>not json</html>"
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "unverified"
    assert "unexpected response" in result.reason


def test_missing_when_the_rules_array_is_genuinely_empty(tmp_path: Path, fake_github) -> None:
    """H32: unlike a `404`, a real `200` with an empty array *is*
    confirmed-absent evidence from the unified rules endpoint."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "missing"
    assert result.default_branch == "main"


def test_verified_when_force_push_and_deletion_are_both_blocked(
    tmp_path: Path, fake_github
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = [
        {"type": "non_fast_forward"},
        {"type": "deletion"},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
    ]
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "verified"
    assert result.force_push_blocked is True
    assert result.deletion_blocked is True
    assert result.requires_pull_request is True
    assert result.reason is None


def test_missing_when_force_push_is_allowed_despite_other_protections(
    tmp_path: Path, fake_github
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_body = [
        {"type": "deletion"},
        # no "non_fast_forward" rule -> force pushes are not blocked
    ]
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["force_push", "deletion"],
    )

    assert result.status == "missing"
    assert result.force_push_blocked is False
    assert result.deletion_blocked is True


def test_only_configured_checks_count_toward_verified(tmp_path: Path, fake_github) -> None:
    """`git_policy.remote_protection_checks` narrows what "verified" means
    — force push is allowed here, but only `deletion` is in `checks`."""
    base_url, handler_cls = fake_github
    handler_cls.response_body = [{"type": "deletion"}]
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=["deletion"],
    )

    assert result.status == "verified"


def test_unverified_when_the_token_is_rejected(tmp_path: Path, fake_github) -> None:
    base_url, handler_cls = fake_github
    handler_cls.require_token = "the-real-token"
    token_path = tmp_path / "token"
    token_path.write_text("wrong-token\n")

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=[],
    )

    assert result.status == "unverified"
    assert "401" in result.reason


def test_unverified_when_the_server_is_unreachable(tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("ghp_faketoken\n")
    config = _github_config("http://127.0.0.1:1", token_path)  # port 1: nothing listens there

    result = verify_remote_protection(
        github_config=config, owner_repo="owner/repo", default_branch="main", checks=[]
    )

    assert result.status == "unverified"
    assert "request failed" in result.reason


def test_token_read_from_file_is_sent_as_a_bearer_header(tmp_path: Path, fake_github) -> None:
    base_url, handler_cls = fake_github
    handler_cls.require_token = "exact-token-value"
    handler_cls.response_status = 200
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("exact-token-value\n")  # trailing newline, must be stripped

    result = verify_remote_protection(
        github_config=_github_config(base_url, token_path),
        owner_repo="owner/repo",
        default_branch="main",
        checks=[],
    )

    assert result.status == "missing"  # reached the server, got a real (confirmed-empty) answer
