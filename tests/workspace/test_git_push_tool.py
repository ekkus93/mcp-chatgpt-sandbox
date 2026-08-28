"""S27: `git_push` through the real MCP tool layer — the third structured
*mutating* Git tool, running in-container (§20.1) against a real Docker
daemon.

Three families of test here:

- Input/policy tests (conflicting flags, invalid branch names, every
  `git_policy` key `git_push` actually consults, the §20.6 preconditions)
  need no real remote at all — each is expected to fail before Git ever
  runs, which is exactly what's being verified.
- Remote-validation tests mirror `test_git_pull_tool.py`'s: an
  unauthorized or dangerous remote is rejected before any network touch.
- One test exercises a real connection attempt to the real `github.com`
  host, for the same reason `test_git_pull_tool.py`'s does: a validated
  `https://github.com/...` origin is rewritten to SSH by S24's
  `url."git@github.com:".insteadOf` (§19.3), and GitHub requires an
  authenticated key for any SSH operation. Without S23 (blocked by D3)
  this can only reach the authentication stage, cleanly.

AT-031 (`git add`/`commit`/`fetch`/`push` all working from `exec`, with
correct host ownership) is deliberately *not* about the structured tool at
all — `exec` is unmediated by remote validation (§20.2: "It does not bind
`exec`") — so it is tested against a real local bare-repository remote,
fully offline.
"""

import os
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server

#: GitHub's own tiny, effectively-permanent demo repository — used only
#: for the real-`github.com`-connection test (see module docstring).
_PUBLIC_REMOTE_URL = "https://github.com/octocat/Hello-World.git"
_PUBLIC_REMOTE_IDENTITY = "octocat/Hello-World"


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


def _set_remote_with_pushurl(repo_path: Path, *, fetch_url: str, push_url: str) -> None:
    _set_remote(repo_path, fetch_url)
    subprocess.run(
        ["git", "config", "--local", "remote.origin.pushurl", push_url],
        cwd=repo_path,
        check=True,
    )


# --- input validation, before anything else (no remote, no workspace needed) -------


async def test_force_and_force_with_lease_together_is_invalid_argument(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-029: conflicting flags refused."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_push",
            {
                "workspace_id": workspace_id,
                "force": True,
                "force_with_lease": True,
                "expected_local_head_sha": "0" * 40,
            },
        )


async def test_invalid_branch_name_is_rejected(
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

    # H32: `validate_branch_name`'s own error code, not the generic
    # `INVALID_ARGUMENT` — this *is* §20.3's "not a valid ref name" rule.
    with pytest.raises(ToolError, match="GIT_REFSPEC_NOT_ALLOWED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id, "branch": "../evil"})


async def test_delete_with_expected_local_head_sha_is_invalid(
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

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_push",
            {
                "workspace_id": workspace_id,
                "delete": True,
                "branch": "stale",
                "expected_local_head_sha": "0" * 40,
            },
        )


# --- §18.9 policy, refused before Git is invoked or the remote validated -----------


async def test_at_011_force_true_is_denied_before_git_runs_under_default_policy(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    # a remote that would fail validation too, if the policy check did not
    # actually happen first — proving POLICY_DENIED fires before that.
    _set_remote(repo_path, "ext::sh -c 'touch /tmp/pwned'")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_push",
            {"workspace_id": workspace_id, "force": True, "expected_local_head_sha": "0" * 40},
        )


async def test_at_h031_policy_denial_is_logged_not_only_audited(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H31 (`docs/HARDENING_TODO.md`, HR-031): policy denials SHALL be
    logged — `audit_denials` (`tools/git.py`) already writes the
    client-operation-facing audit record (§24); this is its operational-
    visibility half, through the standard `logging` module."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "ext::sh -c 'touch /tmp/pwned'")
    server = create_server(config)
    workspace_id = await _open(server)

    with (
        caplog.at_level("WARNING", logger="dev_workspace_mcp.tools.git"),
        pytest.raises(ToolError, match="POLICY_DENIED"),
    ):
        await server.call_tool(
            "git_push",
            {"workspace_id": workspace_id, "force": True, "expected_local_head_sha": "0" * 40},
        )

    messages = [r.message for r in caplog.records]
    assert any("git_push" in m and "denied" in m for m in messages)


async def test_push_policy_deny_blocks_the_whole_tool(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    config = config.model_copy(
        update={"git_policy": config.git_policy.model_copy(update={"push": "deny"})}
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id})


async def test_delete_remote_branch_default_policy_denies(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`delete_remote_branch` defaults to `require_approval`, a hard
    denial in this release (§42, §18.9) since there is no interactive
    approval mechanism yet."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_push", {"workspace_id": workspace_id, "delete": True, "branch": "stale"}
        )


async def test_default_branch_push_deny_blocks_an_ordinary_push_to_the_default_branch(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
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

    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
            "git_policy": minimal_config.git_policy.model_copy(
                update={"default_branch_push": "deny"}
            ),
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id, "branch": "main"})


async def test_at_h024_default_branch_push_require_approval_blocks_an_ordinary_push(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H24 (`docs/HARDENING_TODO.md`, HR-022): `require_approval` is a
    hard denial in this release (§42, no interactive-approval mechanism
    exists yet) — the same as `deny`, and the same shape `amend_published`
    already used. `default_branch_push`'s own check is now fail-closed
    (`not in ("allow", "allow_fast_forward")`) rather than an enumeration
    of the two known deny-values, so this also guards against a future
    unrecognized value being silently treated as permitted."""
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

    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
            "git_policy": minimal_config.git_policy.model_copy(
                update={"default_branch_push": "require_approval"}
            ),
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id, "branch": "main"})


async def test_default_branch_push_allow_fast_forward_blocks_force_on_the_default_branch(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`allow_fast_forward` (the actual default) permits an ordinary push
    to the default branch but SHALL still refuse a *forced* one — even
    with `force_push` itself set to `allow`, proving this is
    `default_branch_push`'s own restriction, not merely `force_push`'s."""
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
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.strip()

    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
            "git_policy": minimal_config.git_policy.model_copy(update={"force_push": "allow"}),
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_push",
            {
                "workspace_id": workspace_id,
                "branch": "main",
                "force": True,
                "expected_local_head_sha": head_sha,
            },
        )


async def test_expected_local_head_sha_is_required_when_force_is_permitted_and_used(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    config = config.model_copy(
        update={"git_policy": config.git_policy.model_copy(update={"force_push": "allow"})}
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_push", {"workspace_id": workspace_id, "branch": "main", "force": True}
        )


async def test_expected_local_head_sha_mismatch_fails(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    config = config.model_copy(
        update={"git_policy": config.git_policy.model_copy(update={"force_push": "allow"})}
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_push",
            {
                "workspace_id": workspace_id,
                "branch": "main",
                "force": True,
                "expected_local_head_sha": "0" * 40,
            },
        )


async def test_git_push_fails_when_a_merge_is_already_in_progress(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    (repo_path / ".git" / "MERGE_HEAD").write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True
        ).stdout
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool("git_push", {"workspace_id": workspace_id})


# --- remote-validation preconditions (no network needed) ---------------------------


async def test_at_026_ext_transport_is_rejected_before_any_push(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "ext::sh -c 'touch /tmp/pwned'")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id})


async def test_at_009_unauthorized_remote_is_rejected(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/attacker/unrelated.git")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id})


async def test_h12_pushurl_redirect_to_an_unauthorized_repo_is_rejected(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-014: a real regression, reproduced end-to-end. The *fetch* URL
    (`git remote get-url origin`) resolves to the repository's own
    configured identity and validates cleanly — but `remote.origin.pushurl`
    silently redirects where `git push` actually goes. Before this fix,
    `git_push` validated only the fetch URL and would have pushed straight
    through to the unauthorized destination while auditing the wrong
    (approved) one; it SHALL now reject before Git ever runs."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote_with_pushurl(
        repo_path,
        fetch_url="https://github.com/owner/example.git",
        push_url="https://github.com/attacker/unrelated.git",
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool("git_push", {"workspace_id": workspace_id})


async def test_expected_remote_repository_override_is_honoured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(
        minimal_config, default_image_tag, repo_path, github=_PUBLIC_REMOTE_IDENTITY
    )
    _set_remote(repo_path, _PUBLIC_REMOTE_URL)
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool(
            "git_push",
            {
                "workspace_id": workspace_id,
                "expected_remote_repository": "owner/a-different-repo",
            },
        )


# --- a real connection attempt to the real github.com host -------------------------


async def test_git_push_reaches_the_real_github_auth_stage_cleanly(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    keyscan = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519", "github.com"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    real_known_hosts_line = next(
        line for line in keyscan.stdout.splitlines() if line and not line.startswith("#")
    )

    repo_path = tmp_path / "work" / "example"
    config = _configured(
        minimal_config, default_image_tag, repo_path, github=_PUBLIC_REMOTE_IDENTITY
    )
    _set_remote(repo_path, _PUBLIC_REMOTE_URL)

    minimal_config.ssh.known_hosts.parent.mkdir(parents=True, exist_ok=True)
    minimal_config.ssh.known_hosts.write_text(real_known_hosts_line + "\n")
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError) as excinfo:
        await server.call_tool("git_push", {"workspace_id": workspace_id, "branch": "main"})

    message = str(excinfo.value)
    # H32: a real auth-stage rejection now gets its own error code
    # instead of the generic COMMAND_FAILED it used to fall through to.
    assert "GIT_AUTH_FAILED" in message
    assert "REMOTE HOST IDENTIFICATION HAS CHANGED" not in message  # host key matched
    assert "Host key verification failed" not in message  # host verification passed
    assert "Permission denied" in message  # failed at auth, for lack of a key (S23)


# --- AT-031: exec-based git workflow, fully offline, unmediated by remote validation


async def test_at_031_add_commit_fetch_push_all_succeed_from_exec_with_correct_ownership(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§20.2: remote validation "does not bind `exec`" — a local bare
    repository is a perfectly legitimate `exec`-only remote (unlike the
    structured tool, which is anchored to github.com). The bare "remote"
    lives inside the *container's* own filesystem (only `/workspace` and
    the known_hosts mount are bind-mounted from the host) rather than at
    a host path the container can't see — still fully offline, no
    network, no SSH key needed at all for a plain local-path remote."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    server = create_server(config)
    workspace_id = await _open(server)

    init_remote = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": ["git", "init", "-q", "--bare", "-b", "main", "/tmp/remote.git"],
        },
    )
    assert init_remote.structured_content["exit_code"] == 0
    add_remote = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": ["git", "remote", "add", "origin", "/tmp/remote.git"],
        },
    )
    assert add_remote.structured_content["exit_code"] == 0

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "new.txt", "content": "hi\n"}
    )
    add_result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "add", "new.txt"]}
    )
    assert add_result.structured_content["exit_code"] == 0

    commit_result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": ["git", "commit", "-q", "-m", "add new.txt"],
        },
    )
    assert commit_result.structured_content["exit_code"] == 0

    fetch_result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "fetch", "origin"]}
    )
    assert fetch_result.structured_content["exit_code"] == 0, fetch_result.structured_content

    push_result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "push", "origin", "main"]}
    )
    assert push_result.structured_content["exit_code"] == 0, push_result.structured_content

    # visible in the host checkout...
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    assert "add new.txt" in log.stdout
    # ...owned by the real host user, not root or a container-only uid —
    # `stat` from the host process itself is the ground truth here.
    assert (repo_path / "new.txt").stat().st_uid == os.getuid()

    # ...and the resulting commit really reached the bare remote, verified
    # from inside the container (the bare repo isn't host-visible at all).
    remote_log = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            # `-C` rather than exec's own `cwd`: `cwd` is restricted to
            # `/workspace` or beneath it (§14.2), and the bare remote is
            # deliberately outside that tree.
            "argv": ["git", "-C", "/tmp/remote.git", "log", "--oneline", "-1", "main"],
        },
    )
    assert "add new.txt" in remote_log.structured_content["stdout_tail"]
