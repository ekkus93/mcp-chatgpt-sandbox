"""S22: `git_commit` through the real MCP tool layer — the first
structured *mutating* Git tool, running in-container (§20.1) against a
real Docker daemon.
"""

import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


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


async def test_git_commit_interrupted_merge_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("feature change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feature change"], cwd=repo_path, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("main change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main change"], cwd=repo_path, check=True)
    subprocess.run(["git", "merge", "-q", "feature"], cwd=repo_path, check=False)

    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["README.md"]},
        )
