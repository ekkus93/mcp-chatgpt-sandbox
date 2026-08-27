"""S29: audit logging (§24, §20.7) through the real MCP tool layer —
`workspace_opened`, `repository_authorization_failure`, and structured
Git denials audited with the same fidelity as successes.

`tests/test_audit.py` (S17) already exhaustively covers `write_audit_event`
itself, including AT-021's secret-redaction mechanism — every audit call
added here routes through that same function, so redaction is exercised
by construction rather than re-tested per call site. There is no natural,
realistic path for a secret to appear in this module's own audit
metadata (branch names, shas, remote identities, policy decisions), so no
contrived redaction scenario is added here.

`ssh_auth` (AT-037) needs the per-workspace `ssh-agent` S23 builds (agent
mode, blocked by D3) to intercept and log signature requests — there is
no agent to hook into yet, so it is not implemented, consistent with how
S25/S26/S27 deferred their own S23-dependent halves."""

import json
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path, *, github: str
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [RepositoryConfig(id="example", path=repo_path, github=github)],
        }
    )


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


def _set_remote(repo_path: Path, url: str) -> None:
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo_path, check=True)


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_workspace_opened_is_audited_on_success(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    events = _read_events(config.audit.path)
    opened = [e for e in events if e["operation"] == "workspace_opened"]
    assert len(opened) == 1
    assert opened[0]["result"] == "success"
    assert opened[0]["workspace_id"] == workspace_id
    assert opened[0]["repository_id"] == "example"
    assert opened[0]["metadata"]["mode"] == "persistent"


async def test_repository_authorization_failure_is_audited(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)

    with pytest.raises(ToolError, match="REPOSITORY_NOT_ALLOWED"):
        await server.call_tool("workspace_open", {"repository_id": "does-not-exist"})

    events = _read_events(config.audit.path)
    failures = [e for e in events if e["operation"] == "repository_authorization_failure"]
    assert len(failures) == 1
    assert failures[0]["result"] == "denied"
    assert failures[0]["metadata"]["repository_id"] == "does-not-exist"


async def test_git_push_policy_denial_is_audited_with_policy_decision_deny(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_push",
            {"workspace_id": workspace_id, "force": True, "expected_local_head_sha": "0" * 40},
        )

    events = _read_events(config.audit.path)
    denials = [e for e in events if e["operation"] == "git_push" and e["result"] == "denied"]
    assert len(denials) == 1
    assert denials[0]["workspace_id"] == workspace_id
    assert denials[0]["metadata"]["policy_decision"] == "deny"
    assert denials[0]["metadata"]["error_code"] == "POLICY_DENIED"


async def test_git_pull_remote_rejection_is_audited_as_a_failure_not_a_policy_denial(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§20.2's remote-validation rejection (`GIT_REMOTE_NOT_ALLOWED`) is a
    real denial worth auditing, but it isn't a `git_policy` key decision —
    `policy_decision` distinguishes the two rather than overclaiming."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "ext::sh -c 'touch /tmp/pwned'")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool("git_pull", {"workspace_id": workspace_id})

    events = _read_events(config.audit.path)
    failures = [e for e in events if e["operation"] == "git_pull" and e["result"] == "failure"]
    assert len(failures) == 1
    assert failures[0]["metadata"]["policy_decision"] == "n/a"
    assert failures[0]["metadata"]["error_code"] == "GIT_REMOTE_NOT_ALLOWED"


async def test_git_commit_success_is_audited_with_policy_decision_allow(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "new.txt", "content": "hi\n"}
    )
    await server.call_tool(
        "git_commit",
        {"workspace_id": workspace_id, "message": "add new.txt", "paths": ["new.txt"]},
    )

    events = _read_events(config.audit.path)
    commits = [e for e in events if e["operation"] == "git_commit"]
    assert len(commits) == 1
    assert commits[0]["result"] == "success"
    assert commits[0]["metadata"]["policy_decision"] == "allow"


async def test_interrupted_operation_denial_is_audited_as_a_failure(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A precondition failure (§20.6) is a denial too, not only a
    policy-key refusal — `audit_denials` catches every `WorkspaceError`
    a structured Git tool raises, not just `POLICY_DENIED` ones."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    (repo_path / ".git" / "MERGE_HEAD").write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True
        ).stdout
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "x", "paths": ["new.txt"]}
        )

    events = _read_events(config.audit.path)
    failures = [e for e in events if e["operation"] == "git_commit" and e["result"] == "failure"]
    assert len(failures) == 1
    assert failures[0]["metadata"]["error_code"] == "GIT_CONFLICT"
