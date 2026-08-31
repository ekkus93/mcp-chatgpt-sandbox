"""GAP3 Y6 / G3AT-006: runtime image provenance must follow the container.

A mutable tag is configuration, not process/container identity.  These tests
exercise the real Docker daemon so retagging can diverge the configured image
reference from the immutable image already backing a persistent container.
"""

import contextlib
import io
import subprocess
import uuid
from pathlib import Path

import docker

from dev_workspace_mcp.cli import _recreated_run_kwargs
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


def _init_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)


def _configured(minimal_config: ServerConfig, image: str, repo_path: Path) -> ServerConfig:
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": image}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server):
    return await server.call_tool("workspace_open", {"repository_id": "example"})


async def test_g3at_006_retagging_does_not_change_existing_container_digest(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    image_a = docker_client.images.get(default_image_tag)
    suffix = uuid.uuid4().hex

    # Build B FROM A with only a unique image label. It has the same
    # entrypoint/tools/runtime contract as A but a distinct immutable ID,
    # so the rebuild half tests provenance rather than distro differences.
    derived_repository = f"mcp-gap3-y6-derived-{suffix}"
    derived_tag = f"{derived_repository}:latest"
    dockerfile = (
        f"FROM {default_image_tag}\n"
        f'LABEL mcp.gap3.y6="{suffix}"\n'
    ).encode()
    image_b, _ = docker_client.images.build(
        fileobj=io.BytesIO(dockerfile),
        tag=derived_tag,
        rm=True,
        forcerm=True,
        pull=False,
    )
    assert image_a.id != image_b.id

    tag_repository = f"mcp-gap3-y6-{suffix}"
    mutable_tag = f"{tag_repository}:latest"
    assert image_a.tag(tag_repository, tag="latest", force=True)

    repo_path = tmp_path / "work" / "example"
    _init_repo(repo_path)
    config = _configured(minimal_config, mutable_tag, repo_path)
    server = create_server(config)

    try:
        first = await _open(server)
        workspace_id = first.structured_content["workspace_id"]
        assert first.structured_content["image"] == mutable_tag
        assert first.structured_content["image_digest"] == image_a.id

        # Retarget the same configured reference to B. Reopening a
        # persistent workspace reconciles the existing container; it is
        # deliberately not a rebuild and therefore must still report A.
        assert image_b.tag(tag_repository, tag="latest", force=True)
        reopened = await _open(server)
        assert reopened.structured_content["workspace_id"] == workspace_id

        info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
        assert info.structured_content["image"] == mutable_tag
        assert info.structured_content["configured_image"] == mutable_tag
        assert info.structured_content["image_digest"] == image_a.id

        # An explicit rebuild is the boundary where the mutable reference
        # is resolved for a new container. After that, runtime provenance
        # must change to B.
        db = open_db(config.state_db_path)
        try:
            repository = RepositoryRegistry(config).get("example")
            image, run_kwargs, _ = _recreated_run_kwargs(config, docker_client, repository)
            DockerWorkspaceManager(docker_client, db).rebuild(
                workspace_id, image=image, run_kwargs=run_kwargs
            )
        finally:
            db.close()

        rebuilt_info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
        assert rebuilt_info.structured_content["image_digest"] == image_b.id

        # Removing the mutable tag must not erase provenance for the
        # already-running container. No fallback tag lookup is allowed.
        docker_client.images.remove(mutable_tag, noprune=True)
        missing_tag_info = await server.call_tool("workspace_info", {"workspace_id": workspace_id})
        assert missing_tag_info.structured_content["image_digest"] == image_b.id
    finally:
        with contextlib.suppress(docker.errors.DockerException):
            docker_client.images.remove(mutable_tag, noprune=True)
        with contextlib.suppress(docker.errors.DockerException):
            docker_client.images.remove(derived_tag, noprune=True)
