"""S22: `git_commit` through the real MCP tool layer — the first
structured *mutating* Git tool, running in-container (§20.1) against a
real Docker daemon.

Ordinary-operation contract: creation, direct-callability, staging
exactly what was asked, the HEAD/branch guards (§45), strict mode, and
policy (default-branch/amend, §18.9). Preconditions against a dirty or
conflicting repository, pathspec-injection protection, and the
failed-commit index-rollback contract (RR-005/HR-025/AR-001) are in
`test_git_commit_tool_failure_modes.py` — split from this file (originally
1066 lines) so no single file holds both halves at once; see that file's
own module docstring.
"""

import subprocess
from pathlib import Path

import docker
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.tools import git as git_tools
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
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
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_git_commit_creates_a_commit_from_explicit_paths(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "new.txt", "content": "hi\n"}
    )

    result = await server.call_tool(
        "git_commit",
        {"workspace_id": workspace_id, "message": "add new.txt", "paths": ["new.txt"]},
    )
    content = result.structured_content
    assert content["commit_sha"] is not None
    assert content["branch"] == "main"
    assert content["files_committed"] == ["new.txt"]

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo_path, capture_output=True, text=True
    )
    assert log.stdout.strip() == "add new.txt"


async def test_rr027_run_git_commit_is_directly_callable_without_a_server(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """R27 (`docs/REMEDIATION_TODO.md`): `run_git_commit` — the
    module-level extraction of `git_commit_tool`'s ~264-line body —
    called directly, with no `server.call_tool("git_commit", ...)`
    anywhere in the chain, only its real dependencies constructed
    directly. `create_server`/`workspace_open`/`write_file` are still
    used to get a real running container with real uncommitted content
    to commit — this test is about the commit tool body's own
    testability, not about reproducing workspace creation too."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "new.txt", "content": "hi\n"}
    )

    db = open_db(config.state_db_path)
    registry = RepositoryRegistry(config)
    manager = DockerWorkspaceManager(docker_client, db)
    execution_service = ExecutionService(docker_client)

    result = await git_tools.run_git_commit(
        workspace_id,
        "add new.txt",
        ["new.txt"],
        None,
        None,
        False,
        False,
        False,
        manager=manager,
        execution_service=execution_service,
        registry=registry,
        db=db,
        container_config=config.container,
        execution_config=config.execution,
        git_policy_config=config.git_policy,
        audit_config=config.audit,
    )

    assert result.commit_sha is not None
    assert result.branch == "main"
    assert result.files_committed == ["new.txt"]


async def test_git_commit_stages_exactly_the_given_paths(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "b.txt", "content": "b\n"}
    )

    await server.call_tool(
        "git_commit", {"workspace_id": workspace_id, "message": "only a", "paths": ["a.txt"]}
    )

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["untracked"] == ["b.txt"]
    log = subprocess.run(
        ["git", "show", "--stat", "--pretty=", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    assert "a.txt" in log.stdout
    assert "b.txt" not in log.stdout


async def test_git_commit_requires_at_least_one_path(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "empty", "paths": []}
        )


async def test_git_commit_expected_branch_mismatch_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "x",
                "paths": ["a.txt"],
                "expected_branch": "not-main",
            },
        )
    # Repository state left unmodified.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True
    )
    assert "a.txt" in status.stdout  # still untracked, nothing committed


async def test_at_008_a_commit_landed_externally_after_head_was_read_fails_the_guard(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-008, literally: read HEAD via `git_status`, then a *real*
    external commit — landed the same way an operator or another process
    could, straight into the host bind mount, entirely outside this MCP
    session — advances it before the guarded `git_commit` call, which
    SHALL fail against the now-stale value it read earlier."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    stale_head_sha = status.structured_content["head_sha"]

    # a real commit, external to the MCP session entirely — straight into
    # the shared bind mount, the same way a human or another tool could.
    (repo_path / "external.txt").write_text("landed while we weren't looking\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "external commit"], cwd=repo_path, check=True)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "guarded commit against stale HEAD",
                "paths": ["a.txt"],
                "expected_head_sha": stale_head_sha,
            },
        )


async def test_git_commit_expected_head_sha_mismatch_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="GIT_HEAD_CHANGED"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "x",
                "paths": ["a.txt"],
                "expected_head_sha": "deadbeef" * 5,
            },
        )


async def test_at_h024_git_commit_accepts_an_abbreviated_expected_head_sha(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H24 (`docs/HARDENING_TODO.md`, HR-022): `expected_head_sha` is
    routinely copied from a `git log --oneline`-style abbreviated SHA —
    a real regression had this rejected outright by an exact `!=`
    comparison against the always-full `status.head_sha`, even when it
    correctly identified the current HEAD."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    abbreviated_head_sha = status.structured_content["head_sha"][:8]

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    result = await server.call_tool(
        "git_commit",
        {
            "workspace_id": workspace_id,
            "message": "commit guarded by an abbreviated expected_head_sha",
            "paths": ["a.txt"],
            "expected_head_sha": abbreviated_head_sha,
        },
    )
    assert result.structured_content["files_committed"] == ["a.txt"]


async def test_git_commit_strict_mode_rejects_extraneous_staged_paths(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    (repo_path / "b.txt").write_text("b\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo_path, check=True)  # stray staged file

    with pytest.raises(ToolError, match="GIT_DIRTY"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "only a",
                "paths": ["a.txt"],
                "strict": True,
            },
        )
    # Nothing committed; b.txt is still staged as it was before the call.
    log_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo_path, capture_output=True, text=True
    )
    assert log_count.stdout.strip() == "1"  # just the initial commit
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=repo_path, capture_output=True, text=True
    )
    assert staged.stdout.strip() == "b.txt"


async def test_git_commit_non_strict_mode_ignores_extraneous_staged_paths(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    (repo_path / "b.txt").write_text("b\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo_path, check=True)

    await server.call_tool(
        "git_commit", {"workspace_id": workspace_id, "message": "only a", "paths": ["a.txt"]}
    )

    log = subprocess.run(
        ["git", "show", "--stat", "--pretty=", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    assert "a.txt" in log.stdout
    assert "b.txt" not in log.stdout  # left staged, not committed
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=repo_path, capture_output=True, text=True
    )
    assert staged.stdout.strip() == "b.txt"


async def test_git_commit_denies_direct_commit_on_default_branch(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote_path)], check=True)
    # Seed the bare remote with a real commit *before* the clone under
    # test: `origin/HEAD` (what `detect_default_branch` reads) is only
    # set up by a normal clone when the remote already has a branch to
    # point at — cloning a genuinely empty repo leaves it unset.
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
                update={"default_branch_commit": "deny"}
            ),
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "x", "paths": ["a.txt"]}
        )


async def test_git_commit_amend_allowed_when_not_published(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "git_commit", {"workspace_id": workspace_id, "message": "first try", "paths": ["a.txt"]}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\nmore\n"}
    )

    result = await server.call_tool(
        "git_commit",
        {
            "workspace_id": workspace_id,
            "message": "amended message",
            "paths": ["a.txt"],
            "amend": True,
        },
    )
    log_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo_path, capture_output=True, text=True
    )
    assert log_count.stdout.strip() == "2"  # initial + the one amended commit
    subject = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo_path, capture_output=True, text=True
    )
    assert subject.stdout.strip() == "amended message"
    assert result.structured_content["commit_sha"] is not None


async def test_git_commit_amend_denied_when_published(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote_path)], check=True)
    repo_path = tmp_path / "work" / "example"
    repo_path.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(remote_path), str(repo_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    (repo_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "published commit"], cwd=repo_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo_path, check=True)

    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\nmore\n"}
    )

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "rewriting published history",
                "paths": ["a.txt"],
                "amend": True,
            },
        )


async def test_at_h024_git_commit_amend_denied_when_published_and_require_approval(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H24 (`docs/HARDENING_TODO.md`, HR-022): `amend_published:
    require_approval` denies exactly like `deny` does (§42: a hard
    denial in this release, no interactive-approval mechanism exists
    yet) — the same treatment `default_branch_push` gives
    `require_approval`."""
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote_path)], check=True)
    repo_path = tmp_path / "work" / "example"
    repo_path.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(remote_path), str(repo_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    (repo_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "published commit"], cwd=repo_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo_path, check=True)

    config = minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
            "git_policy": minimal_config.git_policy.model_copy(
                update={"amend_published": "require_approval"}
            ),
        }
    )
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\nmore\n"}
    )

    with pytest.raises(ToolError, match="POLICY_DENIED"):
        await server.call_tool(
            "git_commit",
            {
                "workspace_id": workspace_id,
                "message": "rewriting published history",
                "paths": ["a.txt"],
                "amend": True,
            },
        )


async def test_git_commit_no_verify_skips_hooks(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["a.txt"]}
        )

    result = await server.call_tool(
        "git_commit",
        {
            "workspace_id": workspace_id,
            "message": "with no_verify",
            "paths": ["a.txt"],
            "no_verify": True,
        },
    )
    assert result.structured_content["commit_sha"] is not None
