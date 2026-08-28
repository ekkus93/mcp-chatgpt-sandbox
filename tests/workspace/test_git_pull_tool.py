"""S26: `git_pull` through the real MCP tool layer — the second structured
*mutating* Git tool, running in-container (§20.1) against a real Docker
daemon.

Two families of test here:

- Precondition/validation tests (interrupted operation, branch/HEAD
  guards, and — via S25's `validate_remote_identity` — remote rejection)
  need no real remote at all: origin is set to a value the guard is
  expected to catch before anything is fetched.
- One test exercises a real connection attempt to the real `github.com`
  host. It cannot go over plain HTTPS: a validated `https://github.com/...`
  origin is rewritten to SSH by S24's `url."git@github.com:".insteadOf`
  (§19.3), same as any other structured operation, and GitHub requires an
  authenticated key for *any* SSH operation — even reading a public repo.
  Without S23 (SSH key provisioning, blocked by D3) there is no key to
  offer, so this test verifies the honest boundary of what's wired up so
  far: with GitHub's real, freshly `ssh-keyscan`-obtained host key pinned
  into `known_hosts`, the connection gets past host verification (proving
  S24's mount and this tool's fetch step are wired together correctly) and
  fails specifically at the authentication stage — cleanly, without
  hanging. The actual fetch/integrate *mechanics* (fast-forward success,
  non-fast-forward failure, merge, rebase) are proven for real, without
  this constraint, by `tests/git/test_pull.py` against local repositories.
"""

import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server

#: GitHub's own tiny, effectively-permanent demo repository — three
#: commits on `master`, used only for real, unauthenticated HTTPS fetch
#: tests (see module docstring).
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


def _config_for_repo(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path, *, github: str
) -> ServerConfig:
    """Like `_configured`, but for a repository directory the test has
    already built itself (a real clone of the public remote) rather than
    one this helper should create from scratch."""
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [RepositoryConfig(id="example", path=repo_path, github=github)],
        }
    )


# --- remote-validation preconditions (no network needed) --------------------------


async def test_at_026_ext_transport_is_rejected_before_any_fetch(
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
        await server.call_tool("git_pull", {"workspace_id": workspace_id})


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
        await server.call_tool("git_pull", {"workspace_id": workspace_id})


async def test_expected_remote_repository_override_is_honoured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§20.2: a client-supplied `expected_remote_repository` mismatch
    fails even when the remote matches the server's own configuration."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(
        minimal_config, default_image_tag, repo_path, github=_PUBLIC_REMOTE_IDENTITY
    )
    _set_remote(repo_path, _PUBLIC_REMOTE_URL)
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool(
            "git_pull",
            {
                "workspace_id": workspace_id,
                "expected_remote_repository": "owner/a-different-repo",
            },
        )


# --- §20.6 preconditions (no network needed) ---------------------------------------


async def test_git_pull_fails_when_a_merge_is_already_in_progress(
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
        await server.call_tool("git_pull", {"workspace_id": workspace_id})


async def test_git_pull_expected_branch_mismatch_fails(
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

    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_pull", {"workspace_id": workspace_id, "expected_branch": "not-main"}
        )


async def test_git_pull_expected_head_sha_mismatch_fails(
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

    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_pull", {"workspace_id": workspace_id, "expected_head_sha": "0" * 40}
        )


async def test_at_019_a_dirty_tree_blocks_pull_under_merge_or_rebase_strategy(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-019: "Attempt an operation configured to require a clean tree
    while unrelated uncommitted changes exist. The operation SHALL fail"
    (§22.3). `ff_only` (the default) is deliberately exempt — see
    `git_pull_tool`'s own comment for why — so this configures `merge`
    and confirms a real, unrelated uncommitted change (a plain
    `write_file`, nothing to do with what would be pulled) blocks the
    pull before anything is fetched."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "https://github.com/owner/example.git")
    config = config.model_copy(
        update={"git_policy": config.git_policy.model_copy(update={"pull_strategy": "merge"})}
    )
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "unrelated.txt", "content": "uncommitted\n"},
    )

    with pytest.raises(ToolError, match="GIT_DIRTY"):
        await server.call_tool("git_pull", {"workspace_id": workspace_id})


async def test_ff_only_pull_is_exempt_from_the_dirty_tree_check(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The default `ff_only` strategy is not gated by AT-019's check — a
    dirty tree still reaches remote validation (and fails there instead,
    for an unrelated reason), proving the dirty check itself did not
    fire."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path, github="owner/example")
    _set_remote(repo_path, "ext::sh -c 'touch /tmp/pwned'")
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "unrelated.txt", "content": "uncommitted\n"},
    )

    with pytest.raises(ToolError, match="GIT_REMOTE_NOT_ALLOWED"):
        await server.call_tool("git_pull", {"workspace_id": workspace_id})


# --- a real fetch attempt against the real github.com host -------------------------
#
# `git_pull` runs against a *validated* remote — an `https://github.com/...`
# origin passes §20.2 validation and is then rewritten to SSH by S24's
# `url."git@github.com:".insteadOf` (§19.3), same as any other structured
# operation. That means even an anonymous, read-only public-repo fetch goes
# over SSH here, not plain HTTPS — and GitHub requires an authenticated key
# for *any* SSH operation, including reading a public repository. Without
# S23 (SSH key provisioning, blocked by D3) there is no key to offer, so a
# real end-to-end fetch cannot succeed yet. What *is* verifiable now, for
# real, is the honest boundary of what's built so far: with GitHub's real,
# `ssh-keyscan`-obtained host key pinned into `known_hosts` (S24), the
# connection gets *past* host verification — proving the S24 mount and this
# tool's fetch step are wired together correctly — and fails specifically at
# the authentication stage, cleanly and without hanging, which is exactly
# the expected shape of "the plumbing works; the credential doesn't exist
# yet." The actual fetch/integrate *mechanics* (fast-forward success,
# non-fast-forward failure, merge, rebase) are proven for real, without this
# constraint, by `tests/git/test_pull.py` against local repositories.


async def test_git_pull_reaches_the_real_github_auth_stage_cleanly(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    # GitHub's real ed25519 host key, obtained the same way
    # `test_ssh_client_config.py`'s AT-034 test obtains a real key: a real
    # `ssh-keyscan`, not a hardcoded literal — verified fresh, every run.
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
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    _set_remote(repo_path, _PUBLIC_REMOTE_URL)

    minimal_config.ssh.known_hosts.parent.mkdir(parents=True, exist_ok=True)
    minimal_config.ssh.known_hosts.write_text(real_known_hosts_line + "\n")
    config = _config_for_repo(
        minimal_config, default_image_tag, repo_path, github=_PUBLIC_REMOTE_IDENTITY
    )
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError) as excinfo:
        await server.call_tool("git_pull", {"workspace_id": workspace_id})

    message = str(excinfo.value)
    # H32: a real auth-stage rejection now gets its own error code
    # instead of the generic COMMAND_FAILED it used to fall through to.
    assert "GIT_AUTH_FAILED" in message
    assert "REMOTE HOST IDENTIFICATION HAS CHANGED" not in message  # host key matched
    assert "Host key verification failed" not in message  # host verification passed
    assert "Permission denied" in message  # failed at auth, for lack of a key (S23)
