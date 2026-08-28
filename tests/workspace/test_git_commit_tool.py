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


async def test_at_hr023_a_stash_pop_conflict_fails_closed_though_it_leaves_no_marker_file(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-023 (`docs/HARDENING_SPEC.md` §11): `detect_interrupted_operation`
    alone doesn't catch this — a `git stash pop` conflict leaves no
    `MERGE_HEAD` (confirmed empirically: only an `AUTO_MERGE` file
    appears, which nothing in `detect_interrupted_operation` reads) even
    though `git status` reports the same `UU` unresolved-conflict state
    an ordinary `git merge` conflict does. The precondition SHALL inspect
    `status.conflicted` directly, not only marker files."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "README.md").write_text("line1-stash-change\n")
    subprocess.run(["git", "stash", "-q"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("line1-main-change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main change"], cwd=repo_path, check=True)
    subprocess.run(["git", "stash", "pop"], cwd=repo_path, check=False)  # expected to conflict
    assert not (repo_path / ".git" / "MERGE_HEAD").exists()  # confirms no marker file exists

    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["README.md"]},
        )


async def test_at_hr024_a_magic_pathspec_does_not_stage_the_whole_repository(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-024 (`docs/HARDENING_SPEC.md` §11): a real regression —
    `paths=[":/"]` used to reach `git add` unprefixed, and `:/` is Git's
    own pathspec syntax for "the entire repository root", staging (and
    then committing) every changed file rather than failing the way a
    genuinely nonexistent literal path named ":/" should."""
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

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": [":/"]},
        )

    # Nothing was staged or committed — both files are still untracked.
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert set(status.structured_content["untracked"]) == {"a.txt", "b.txt"}
    assert status.structured_content["staged"] == []


@pytest.mark.parametrize("root_path", ["", ".", "./"])
async def test_rr004_an_empty_or_root_normalizing_path_is_rejected_before_staging(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    root_path: str,
) -> None:
    """RR-004 (`docs/REMEDIATION_SPEC.md` §5): `paths=[""]` is a non-empty
    *list*, so it passed the `if not paths:` check untouched, and reached
    `git add -- ':(literal)'` — reproduced empirically against real git
    2.43 that this stages the *entire* working tree, not "no such file"
    the way a genuinely nonexistent literal path should fail. `"."` and
    `"./"` normalize to the same repository-root pathspec."""
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

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": [root_path]},
        )

    # Nothing was staged or committed — both files are still untracked.
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert set(status.structured_content["untracked"]) == {"a.txt", "b.txt"}
    assert status.structured_content["staged"] == []


async def test_rr004_a_root_normalizing_path_mixed_with_a_real_path_is_still_rejected(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The check must inspect every entry, not just reject a lone one —
    a caller who names one real path and one root-normalizing path should
    not have the real path's presence mask the dangerous one."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["a.txt", ""]},
        )

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["staged"] == []


async def test_at_hr024_files_committed_reports_what_git_actually_committed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`files_committed` SHALL report what Git actually committed, not
    the request echo — a directory path is the clearest case where the
    two differ: the request names one path, but the commit touches every
    file beneath it."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "dir/a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "dir/b.txt", "content": "b\n"}
    )

    result = await server.call_tool(
        "git_commit",
        {"workspace_id": workspace_id, "message": "add dir", "paths": ["dir"]},
    )

    assert sorted(result.structured_content["files_committed"]) == ["dir/a.txt", "dir/b.txt"]


async def test_at_hr025_a_failed_commit_leaves_the_index_unchanged(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-025 (`docs/HARDENING_SPEC.md` §12): §20.6 requires a failed
    operation to leave repository state unmodified — `git add` SHALL be
    rolled back when the subsequent `git commit` fails. Reproduced via a
    rejecting pre-commit hook (the same mechanism
    `test_git_commit_no_verify_skips_hooks` uses to fail a commit for a
    real reason, not a "nothing changed" no-op)."""
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

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["staged"] == []
    assert status.structured_content["untracked"] == ["a.txt"]  # git add was rolled back


async def test_rr005_a_failed_commit_restores_prior_staged_content_not_head(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """RR-005 (`docs/REMEDIATION_SPEC.md` §5): the old HR-025 rollback
    (`git reset -- <paths>`) resets to HEAD — reproduced empirically that
    this silently destroys any content staged *before* this call ran,
    whenever that differs from HEAD. This test distinguishes all three
    states a naive rollback could land on: HEAD's original content, the
    content staged before this call (what the restore SHALL recover),
    and the content this call's own `git add` staged over top of it
    (what the restore SHALL discard)."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    # HEAD: "v1" — committed *before* the rejecting hook exists, or this
    # setup commit would be blocked by it too.
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "v1\n"}
    )
    add_v1 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "add", "a.txt"]}
    )
    assert add_v1.structured_content["exit_code"] == 0
    commit_v1 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "commit", "-m", "v1"]}
    )
    assert commit_v1.structured_content["exit_code"] == 0

    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)

    # Staged before git_commit_tool ever runs — distinct from HEAD. This
    # is the state the restore SHALL recover.
    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "a.txt", "content": "v2-prior-staged\n"},
    )
    add_v2 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "add", "a.txt"]}
    )
    assert add_v2.structured_content["exit_code"] == 0

    # Not yet staged — git_commit_tool's own `git add` is what stages
    # this. The restore SHALL discard it, not leave it staged.
    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "a.txt", "content": "v3-this-calls-own-add\n"},
    )

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["a.txt"]}
        )

    staged = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "git show :a.txt"}
    )
    assert staged.structured_content["stdout_tail"] == "v2-prior-staged\n"


async def test_at_hr025_nothing_to_commit_maps_to_a_specific_error_code(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The second HR-025 requirement: "nothing to commit" (the paths
    named are real and already committed, with no actual change) gets
    its own stable, matchable code rather than the generic
    `COMMAND_FAILED` every other commit failure reports."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_NOTHING_TO_COMMIT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "no-op", "paths": ["README.md"]},
        )
