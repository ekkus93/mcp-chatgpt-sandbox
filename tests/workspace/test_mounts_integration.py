"""S8: mounts against a real Docker daemon and real Git repositories
(§18.2, AT-012, AT-023, AT-024)."""

import os
import subprocess
from pathlib import Path

import pytest

import docker
from dev_workspace_mcp.config.models import ContainerConfig
from dev_workspace_mcp.workspace.container_config import build_workspace_run_kwargs
from dev_workspace_mcp.workspace.mounts import GitDirMode, build_workspace_mounts


def _real_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _run(
    docker_client: docker.DockerClient,
    image: str,
    command: list[str],
    *,
    repo: Path,
    git_dir_mode: GitDirMode,
) -> bytes:
    config = ContainerConfig(user="host", sudo=True, git_dir_mode=git_dir_mode)
    run_kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=docker_client,
        checkout_uid=os.getuid(),
        checkout_gid=os.getgid(),
    )
    mounts = build_workspace_mounts(
        host_repository_path=repo,
        workspace_path="/workspace",
        git_dir_mode=git_dir_mode,
        authorized_roots=[repo.parent],
    )
    result = docker_client.containers.run(
        image,
        command,
        remove=True,
        stderr=True,
        mounts=mounts,
        labels={"dev-workspace-mcp.managed": "true"},
        **run_kwargs,
    )
    return bytes(result)


def test_at_012_no_docker_socket_in_the_container(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    repo = _real_git_repo(tmp_path / "example")
    with pytest.raises(docker.errors.ContainerError):
        _run(
            docker_client,
            default_image_tag,
            ["sh", "-c", "[ -e /var/run/docker.sock ]"],
            repo=repo,
            git_dir_mode="read-write",
        )


def test_git_environment_is_configured_by_the_entrypoint(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    """§18.2: safe.directory is set container-locally, and
    GIT_TERMINAL_PROMPT=0 so a misconfigured remote fails rather than
    hanging on a credential prompt."""
    repo = _real_git_repo(tmp_path / "example")
    check = (
        "git config --system --get safe.directory "
        "&& echo -n GIT_TERMINAL_PROMPT=$GIT_TERMINAL_PROMPT"
    )
    output = _run(
        docker_client,
        default_image_tag,
        ["sh", "-c", check],
        repo=repo,
        git_dir_mode="read-write",
    )
    text = output.decode()
    assert "/workspace" in text
    assert "GIT_TERMINAL_PROMPT=0" in text


def test_at_023_hook_write_fails_under_read_only(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    repo = _real_git_repo(tmp_path / "example")
    with pytest.raises(docker.errors.ContainerError):
        _run(
            docker_client,
            default_image_tag,
            ["sh", "-c", "echo '#!/bin/sh' > /workspace/.git/hooks/pre-commit"],
            repo=repo,
            git_dir_mode="read-only",
        )


def test_hook_write_succeeds_under_the_default_read_write_mode(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    """Documented behaviour (§18.3), not a security gap: the default mode
    is read-write."""
    repo = _real_git_repo(tmp_path / "example")
    _run(
        docker_client,
        default_image_tag,
        ["sh", "-c", "echo '#!/bin/sh' > /workspace/.git/hooks/pre-commit"],
        repo=repo,
        git_dir_mode="read-write",
    )
    assert (repo / ".git" / "hooks" / "pre-commit").exists()


def test_at_024_config_write_fails_under_read_only(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    repo = _real_git_repo(tmp_path / "example")
    with pytest.raises(docker.errors.ContainerError):
        _run(
            docker_client,
            default_image_tag,
            ["git", "-C", "/workspace", "config", "core.fsmonitor", "true"],
            repo=repo,
            git_dir_mode="read-only",
        )


def test_config_write_succeeds_under_the_default_read_write_mode(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    repo = _real_git_repo(tmp_path / "example")
    _run(
        docker_client,
        default_image_tag,
        ["git", "-C", "/workspace", "config", "core.fsmonitor", "true"],
        repo=repo,
        git_dir_mode="read-write",
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.fsmonitor"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "true"


def test_none_mode_leaves_no_working_git_repository(
    docker_client: docker.DockerClient, default_image_tag: str, tmp_path: Path
) -> None:
    """§18.2: `.git` is masked with an empty read-only tmpfs — Git inside
    the container sees no repository at all."""
    repo = _real_git_repo(tmp_path / "example")
    with pytest.raises(docker.errors.ContainerError):
        _run(
            docker_client,
            default_image_tag,
            ["git", "-C", "/workspace", "status"],
            repo=repo,
            git_dir_mode="none",
        )
    # The host repository is untouched — masking is a container-side view,
    # not a mutation of the real .git.
    assert (repo / ".git" / "HEAD").exists()
