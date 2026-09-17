"""Remote protection verification (§18.5, §20.4, G3R-003).

The local HTTP server stands in for GitHub's REST API so status semantics
are exercised through the real `workspace_open` tool without requiring a
live repository credential. GAP3 changes enforcement, not verification:
`unverified` remains distinct from confirmed `missing`, but `deny` now
blocks both while `warn` continues to report and allow either state.
"""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import docker
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.workspace.naming import LABEL_REPOSITORY_ID
from mcp_chatgpt_sandbox.workspace.ssh_agent import SSHAgentManager
from tests.workspace.conftest import containers_in_this_context


class _FakeGitHubHandler(BaseHTTPRequestHandler):
    response_status: int = 404
    response_body: object | None = None
    requested_paths: list[str] = []  # noqa: RUF012 - test double, shared deliberately

    def do_GET(self) -> None:  # noqa: N802
        type(self).requested_paths.append(self.path)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
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


def _deny_with_write_key(config: ServerConfig) -> ServerConfig:
    repository = config.repositories[0]
    return config.model_copy(
        update={
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "deny"}
            ),
            "repositories": [
                RepositoryConfig(
                    id=repository.id,
                    path=repository.path,
                    github=repository.github,
                    ssh_key="example.ed25519",
                )
            ],
        }
    )


def _forbid_key_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("remote-protection deny reached SSH key provisioning")

    monkeypatch.setattr(SSHAgentManager, "load_key", _unexpected)


def _assert_no_workspace_container(docker_client: docker.DockerClient) -> None:
    containers = containers_in_this_context(
        docker_client, label=LABEL_REPOSITORY_ID, value="example"
    )
    assert containers == []


async def _open(server, repository_id: str = "example"):
    return await server.call_tool("workspace_open", {"repository_id": repository_id})


# --- no credential configured -------------------------------------------------------


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


async def test_at_g3at003_deny_blocks_no_credential_before_key_or_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _deny_with_write_key(
        _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    )
    _forbid_key_provision(monkeypatch)
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    _assert_no_workspace_container(docker_client)


# --- credential configured, real HTTP round-trip against a local server ------------


async def test_at_035_warn_succeeds_with_missing_status(
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


async def test_at_h032_warn_reports_a_404_as_unverified_and_still_allows(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
) -> None:
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
                update={"require_remote_protection": "warn"}
            ),
        }
    )
    server = create_server(config)

    result = await _open(server)

    protection = result.structured_content["remote_protection"]
    assert protection["status"] == "unverified"
    assert "404" in protection["reason"]


async def test_at_g3at003_deny_blocks_404_before_key_or_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_status = 404
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")
    config = _deny_with_write_key(
        _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    )
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            )
        }
    )
    _forbid_key_provision(monkeypatch)
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    _assert_no_workspace_container(docker_client)


async def test_at_g3at003_deny_blocks_network_failure_before_key_or_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")
    config = _deny_with_write_key(
        _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    )
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={
                    "api_auth": "token",
                    "api_token_path": token_path,
                    "api_base_url": "http://127.0.0.1:1",
                }
            )
        }
    )
    _forbid_key_provision(monkeypatch)
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    _assert_no_workspace_container(docker_client)


async def test_at_g3at003_deny_blocks_malformed_success_before_key_or_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    fake_github,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, handler_cls = fake_github
    handler_cls.response_status = 200
    handler_cls.response_body = {"unexpected": "object-not-array"}
    token_path = tmp_path / "token"
    token_path.write_text("fake-token\n")
    config = _deny_with_write_key(
        _configured_with_resolvable_default_branch(minimal_config, default_image_tag, tmp_path)
    )
    config = config.model_copy(
        update={
            "github": config.github.model_copy(
                update={"api_auth": "token", "api_token_path": token_path, "api_base_url": base_url}
            )
        }
    )
    _forbid_key_provision(monkeypatch)
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    _assert_no_workspace_container(docker_client)


async def test_at_035_deny_fails_on_confirmed_missing_and_creates_no_container(
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
            ),
            "git_policy": config.git_policy.model_copy(
                update={"require_remote_protection": "deny"}
            ),
        }
    )
    server = create_server(config)

    with pytest.raises(ToolError, match="REMOTE_PROTECTION_MISSING"):
        await _open(server)

    _assert_no_workspace_container(docker_client)


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

    assert len(handler_cls.requested_paths) == 1
    assert info_1.structured_content["remote_protection"]["status"] == "missing"
    assert info_2.structured_content["remote_protection"]["status"] == "missing"
