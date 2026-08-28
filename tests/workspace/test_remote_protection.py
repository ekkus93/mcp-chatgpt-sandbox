"""S28: remote protection verification (§18.5, §20.4) through the real
`workspace_open`/`workspace_info` tools, against a real Docker daemon.

D2 was decided "no live GitHub credential for now" (docs/TODO.md), so
the "credential configured, query answers" half of this suite runs
against a real local HTTP server standing in for `api.github.com` — see
`tests/test_github_api.py`'s own module docstring for why that's a
faithful substitute, not a mock. The "no credential configured" half
needs no server at all — it is the actual default configuration.

`git_push`'s new `remote_protection` field is wired (confirmed by mypy
and the schema export) but not exercised here via an actual successful
structured push: no test in this suite achieves one, for the same reason
S26/S27's own real-network tests couldn't — GitHub requires a real,
installed credential for any authenticated write, and none exists
without S23's key actually being registered on a real repository.
"""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


class _FakeGitHubHandler(BaseHTTPRequestHandler):
    response_status: int = 404
    response_body: dict | None = None
    requested_paths: list[str] = []  # noqa: RUF012 - test double, shared deliberately

    def do_GET(self) -> None:  # noqa: N802
        type(self).requested_paths.append(self.path)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        # RR-008 (`docs/REMEDIATION_SPEC.md` §5): `or {}`, not `if ... is
        # not None else {}`, silently turned a deliberately empty *list*
        # (`response_body = []`, this file's own "no rules apply"
        # fixture, `or` short-circuiting since `[]` is falsy) into an
        # empty JSON *object* on the wire — always wrong, but with no
        # observable effect pre-RR-008: `_query_branch_rules` performed
        # no shape validation at all, and an empty dict and an empty
        # list both iterate to zero rule types either way. RR-008's new
        # validation correctly rejects the object shape once it can tell
        # the difference, which is what turned this latent fixture bug
        # into a real test failure — confirmed by reproducing against
        # `HEAD` (pre-RR-008) first: these tests passed there despite
        # actually exercising `{}`, not the `[]` they intended.
        body = self.response_body if self.response_body is not None else {}
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def fake_github(request: pytest.FixtureRequest):
    handler_cls = type(f"_Handler_{request.node.name}", (_FakeGitHubHandler,), {})
    handler_cls.requested_paths = []
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    yield base_url, handler_cls
    server.shutdown()
    thread.join(timeout=5)


def _configured_with_resolvable_default_branch(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> ServerConfig:
    """`detect_default_branch` (git/commit.py) reads `origin/HEAD`, which
    only a real clone of an already-pushed-to bare remote sets up — the
    same seeding S22's own default-branch test uses."""
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote_path)], check=True)
    seed_path = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote_path), str(seed_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=seed_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=seed_path, check=True)
    (seed_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=seed_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=seed_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed_path, check=True)

    repo_path = tmp_path / "work" / "example"
    repo_path.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(remote_path), str(repo_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)

    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example"):
    return await server.call_tool("workspace_open", {"repository_id": repository_id})


# --- no credential configured (the actual default) ---------------------------------


async def test_unverified_without_a_credential(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    server = create_server(config)

    result = await _open(server)

    protection = result.structured_content["remote_protection"]
    assert protection["status"] == "unverified"
    assert "no API credential" in protection["reason"]


async def test_deny_degrades_to_warn_without_a_credential(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§18.5: "Without an API credential... deny SHALL be treated as warn
    with the reason reported" — `workspace_open` SHALL still succeed."""
    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "git_policy": config.git_policy.model_copy(update={"require_remote_protection": "deny"})
        }
    )
    server = create_server(config)

    result = await _open(server)

    assert result.structured_content["remote_protection"]["status"] == "unverified"


# --- credential configured, real HTTP round-trip against a local server ------------


async def test_at_035_warn_succeeds_with_missing_status(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
    base_url, handler_cls = fake_github
    # H32: a real, confirmed "no rules apply" answer from the unified
    # rules endpoint — `200` with an empty array, not a `404` (which no
    # longer means "not protected"; see `test_at_h032_*` below).
    handler_cls.response_status = 200
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")

    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            ),
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "warn"}
            ),
        }
    )
    server = create_server(config)

    result = await _open(server)

    protection = result.structured_content["remote_protection"]
    assert protection["status"] == "missing"
    assert protection["default_branch"] == "main"
    assert handler_cls.requested_paths == ["/repos/owner/example/rules/branches/main?per_page=100"]


async def test_at_h032_a_404_from_the_rules_endpoint_is_unverified_not_missing(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §18.5 row): the actual defect
    this task fixes, through the full `workspace_open` path — a `404`
    from the unified rules endpoint (e.g. a token that cannot see this
    repository's rules) is `unverified`, and `require_remote_protection:
    deny` SHALL treat `unverified` as `warn` (§18.5) and still succeed —
    it must not fail closed on evidence that was never actually
    confirmed."""
    base_url, handler_cls = fake_github
    handler_cls.response_status = 404
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")

    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            ),
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "deny"}
            ),
        }
    )
    server = create_server(config)

    result = await _open(server)  # SHALL NOT raise REMOTE_PROTECTION_MISSING

    protection = result.structured_content["remote_protection"]
    assert protection["status"] == "unverified"
    assert "404" in protection["reason"]


async def test_at_035_deny_fails_and_provisions_no_key(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
    base_url, handler_cls = fake_github
    # H32: deny only fails closed on a *confirmed* missing (§18.5) — a
    # real `200` with an empty rules array, not a `404`.
    handler_cls.response_status = 200
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")
    key_path = tmp_path / "example.ed25519"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)

    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "ssh": config.ssh.model_copy(update={"keys_dir": key_path.parent}),
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            ),
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "deny"}
            ),
            "repositories": [
                RepositoryConfig(
                    id="example",
                    path=config.repositories[0].path,
                    github="owner/example",
                    ssh_key=key_path.name,
                )
            ],
        }
    )
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    # AT-035: "SHALL NOT have provisioned a key into the container" — no
    # container was even created, so there is nothing to check the
    # mounts of; confirmed instead by absence entirely.
    containers = docker_client.containers.list(
        all=True, filters={"label": "dev-workspace-mcp.repository-id=example"}
    )
    assert containers == []


async def test_verified_when_the_configured_checks_are_satisfied(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = [
        {"type": "non_fast_forward"},
        {"type": "deletion"},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
    ]
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")

    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            ),
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "deny"}
            ),
        }
    )
    server = create_server(config)

    result = await _open(server)

    protection = result.structured_content["remote_protection"]
    assert protection["status"] == "verified"
    assert protection["force_push_blocked"] is True
    assert protection["deletion_blocked"] is True
    assert protection["requires_pull_request"] is True


# --- workspace_info reports the cached result, not a fresh query -------------------


async def test_workspace_info_reports_the_cached_result_without_requerying(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = []
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")

    config = _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            )
        }
    )
    server = create_server(config)
    opened = await _open(server)
    workspace_id = opened.structured_content["workspace_id"]
    assert len(handler_cls.requested_paths) == 1

    info_1 = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
    info_2 = await server.call_tool("workspace_info", {"workspace_id": workspace_id})

    assert len(handler_cls.requested_paths) == 1  # no new HTTP call from either read
    assert info_1.structured_content["remote_protection"]["status"] == "missing"
    assert info_2.structured_content["remote_protection"]["status"] == "missing"
