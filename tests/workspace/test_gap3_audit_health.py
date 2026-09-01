"""G3AT-012: audit write failures become visible server health degradation."""

from pathlib import Path

import docker

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
    audit_path: Path,
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "audit": minimal_config.audit.model_copy(update={"enabled": True, "path": audit_path}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def test_g3at_012_audit_failure_is_visible_and_recovers_after_success(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A completed operation keeps its result when best-effort audit fails,
    while server_info exposes the failure and a later successful write
    restores healthy status. The diagnostic reason must not echo the audit
    path, which can contain operator-sensitive filesystem information.
    """
    blocker = tmp_path / "audit-blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    audit_path = blocker / "audit.jsonl"
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        audit_path,
    )
    server = create_server(config)

    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert opened.structured_content["container_state"] == "running"

    degraded = (await server.call_tool("server_info", {})).structured_content["audit"]
    assert degraded["status"] == "degraded"
    assert degraded["mode"] == "best_effort"
    assert degraded["last_failure_at"] is not None
    assert degraded["last_failure_reason"]
    assert str(blocker) not in degraded["last_failure_reason"]
    assert str(audit_path) not in degraded["last_failure_reason"]

    failure_at = degraded["last_failure_at"]
    failure_reason = degraded["last_failure_reason"]

    blocker.unlink()
    blocker.mkdir()

    reopened = await server.call_tool("workspace_open", {"repository_id": "example"})
    assert reopened.structured_content["workspace_id"] == opened.structured_content["workspace_id"]
    assert audit_path.exists()

    recovered = (await server.call_tool("server_info", {})).structured_content["audit"]
    assert recovered["status"] == "healthy"
    assert recovered["mode"] == "best_effort"
    assert recovered["last_failure_at"] == failure_at
    assert recovered["last_failure_reason"] == failure_reason
