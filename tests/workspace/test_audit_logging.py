"""S29: audit logging (§24, §20.7) through the real MCP tool layer —
`workspace_opened`, `repository_authorization_failure`, and structured
Git denials audited with the same fidelity as successes. H26
(`docs/HARDENING_TODO.md`, HR-028) added `exec`'s own "command started"
event (§24) and AT-037's `ssh_auth` event.

`tests/test_audit.py` (S17) already exhaustively covers `write_audit_event`
itself, including AT-021's secret-redaction mechanism — every audit call
added here routes through that same function, so redaction is exercised
by construction rather than re-tested per call site. There is no natural,
realistic path for a secret to appear in this module's own audit
metadata (branch names, shas, remote identities, policy decisions), so no
contrived redaction scenario is added here — except `exec`'s own event
below, whose `argv`/`shell` metadata is exactly a client-supplied command
line, the one place in this file redaction is a real, reachable concern
(AT-021), not a theoretical one.

`ssh_auth` (AT-037) needed the per-workspace `ssh-agent` S23 builds
(agent mode) to have something to fingerprint — S23 has since landed
(`workspace/ssh_agent.py`), and H26 wires `tools/execution.py`'s `exec`/
`exec_start` into it via `ssh_key_fingerprint`."""

import contextlib
import json
import os
import signal
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


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


# --- H26 (`docs/HARDENING_TODO.md`, HR-028): exec's "command started" and
# AT-037's ssh_auth events -------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_spawned_agents(minimal_config: ServerConfig):
    """Same lifecycle concern `test_ssh_agent_provisioning.py` already
    documents: `ssh.mode: agent` (the default) spawns a real, host-side
    `ssh-agent` process per workspace opened below — not a Docker
    resource, so nothing else tears it down."""
    yield
    db = open_db(minimal_config.state_db_path)
    try:
        manager = SSHAgentManager(
            db, runtime_dir=minimal_config.state_db_path.parent / "ssh-agents"
        )
        for record in manager.list_all():
            with contextlib.suppress(ProcessLookupError):
                os.kill(record.pid, signal.SIGTERM)
    finally:
        db.close()


def _real_keypair(tmp_path: Path, name: str = "example.ed25519") -> Path:
    key_path = tmp_path / name
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)
    return key_path


def _configured_with_key(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path, key_path: Path
) -> ServerConfig:
    """Like `_configured`, but the repository also resolves a real ssh
    key under the default `ssh.mode: agent` — the precondition
    `_audit_ssh_auth_if_applicable` (`tools/execution.py`) checks."""
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    return config.model_copy(
        update={
            "ssh": config.ssh.model_copy(update={"keys_dir": key_path.parent}),
            "repositories": [
                RepositoryConfig(
                    id="example",
                    path=repo_path,
                    github="owner/example",
                    ssh_key=key_path.name,
                )
            ],
        }
    )


async def test_exec_produces_a_command_started_audit_event(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["true"]})

    events = _read_events(config.audit.path)
    execs = [e for e in events if e["operation"] == "exec"]
    assert len(execs) == 1
    assert execs[0]["result"] == "success"
    assert execs[0]["workspace_id"] == workspace_id
    assert execs[0]["repository_id"] == "example"
    assert execs[0]["metadata"]["argv"] == ["true"]


async def test_at_hat021_exec_command_started_event_redacts_a_secret_in_argv(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-021: a command containing a configured secret SHALL NOT expose
    the secret value in audit output — reachable at all now that `exec`
    actually produces an audit event. A GitHub-PAT-shaped value in
    `argv` is exactly the same pattern `redact_secrets`
    (`errors.py::_SECRET_VALUE_PATTERNS`) already catches in a
    client-facing error message; this confirms the same protection
    applies to the audit record `write_audit_event` writes."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    secret = "ghp_" + "a" * 36
    await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["echo", secret]})

    events = _read_events(config.audit.path)
    execs = [e for e in events if e["operation"] == "exec"]
    assert len(execs) == 1
    raw = json.dumps(execs[0])
    assert secret not in raw
    assert "[REDACTED]" in raw


async def test_at_037_exec_produces_an_ssh_auth_audit_event_with_a_fingerprint(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-037: under `ssh.mode: agent` (the default), `exec` on a
    workspace whose repository resolves a real key produces an
    `ssh_auth` record carrying the key's fingerprint."""
    key_path = _real_keypair(tmp_path)
    repo_path = tmp_path / "work" / "example"
    config = _configured_with_key(minimal_config, default_image_tag, repo_path, key_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["true"]})

    events = _read_events(config.audit.path)
    auths = [e for e in events if e["operation"] == "ssh_auth"]
    assert len(auths) == 1
    assert auths[0]["result"] == "success"
    assert auths[0]["workspace_id"] == workspace_id
    assert auths[0]["repository_id"] == "example"
    assert auths[0]["metadata"]["fingerprint"].startswith("SHA256:")


async def test_exec_start_also_produces_command_started_and_ssh_auth_events(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The background job path (`exec_start`) is a real command
    invocation too — the same §24/AT-037 events, not only the
    synchronous `exec` tool."""
    key_path = _real_keypair(tmp_path)
    repo_path = tmp_path / "work" / "example"
    config = _configured_with_key(minimal_config, default_image_tag, repo_path, key_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool("exec_start", {"workspace_id": workspace_id, "argv": ["sleep", "0.1"]})

    events = _read_events(config.audit.path)
    execs = [e for e in events if e["operation"] == "exec"]
    auths = [e for e in events if e["operation"] == "ssh_auth"]
    assert len(execs) == 1
    assert execs[0]["metadata"]["argv"] == ["sleep", "0.1"]
    assert len(auths) == 1
    assert auths[0]["metadata"]["fingerprint"].startswith("SHA256:")


async def test_no_ssh_auth_event_when_the_repository_has_no_configured_key(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`ssh.mode: agent` alone is not enough — a repository with no
    `ssh_key` configured (`_configured`'s default) resolves no key
    (`resolve_ssh_key_path` returns `None`), so `exec` still produces
    its "command started" event but SHALL NOT fabricate an `ssh_auth`
    one with nothing to fingerprint."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool("exec", {"workspace_id": workspace_id, "argv": ["true"]})

    events = _read_events(config.audit.path)
    assert [e for e in events if e["operation"] == "exec"]
    assert not [e for e in events if e["operation"] == "ssh_auth"]


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


async def test_at_hr015_policy_denial_carries_the_policy_key(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-015: the denial record SHALL carry the policy key that produced
    it (`PolicyDeniedError.details["policy"]`), not just `policy_decision`
    and `error_code` — before this fix, `audit_denials` discarded
    `details` entirely, so a denial gave no way to tell *which* policy
    fired without cross-referencing the (also-audited, but separate)
    error message."""
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
    assert denials[0]["metadata"]["policy"] == "force_push"


async def test_at_hr015_policy_denial_carries_repository_id_and_branch(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-015: `repository_id` (top-level, matching a success record's
    shape) and `branch` (where applicable — here, known before the
    policy check that denies the push) SHALL both be present."""
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
    assert denials[0]["repository_id"] == "example"
    assert denials[0]["metadata"]["branch"] == "main"


async def test_at_hr015_remote_rejection_failure_carries_remote_and_repository_id(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-015's "where applicable" for a non-policy denial: `remote`
    (a tool kwarg, present regardless of where the raise happens) and
    `repository_id` (resolved via the workspace lookup) SHALL both
    appear — `remote_repository` correctly does NOT appear here, since
    `ext::` is rejected before remote identity is ever resolved."""
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
    assert failures[0]["repository_id"] == "example"
    assert failures[0]["metadata"]["remote"] == "origin"
    assert "remote_repository" not in failures[0]["metadata"]
