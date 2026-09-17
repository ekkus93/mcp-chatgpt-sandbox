"""GAP3 Y16: close the two behavioral holes found during acceptance inventory."""

from pathlib import Path

import docker
import pytest

from mcp_chatgpt_sandbox.config.models import FilesystemConfig, ServerConfig
from mcp_chatgpt_sandbox.errors import WorkspaceError
from mcp_chatgpt_sandbox.execution.service import ExecResult, ExecutionService
from mcp_chatgpt_sandbox.filesystem.security import FilesystemSecurity
from mcp_chatgpt_sandbox.filesystem.service import FilesystemService
from mcp_chatgpt_sandbox.server import create_server
from tests.workspace import test_audit_logging as audit_tests


async def test_rr003_successful_push_audit_names_destination_actually_used(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAT-004/RR-003: successful structured push audit names its real destination."""
    repo_path = tmp_path / "work" / "example"
    config = audit_tests._configured(
        minimal_config,
        default_image_tag,
        repo_path,
        github=audit_tests._PUBLIC_REMOTE_IDENTITY,
    )
    audit_tests._set_remote(repo_path, audit_tests._PUBLIC_REMOTE_URL)
    server = create_server(config)
    workspace_id = await audit_tests._open(server)

    real_start = ExecutionService.start
    real_consume = ExecutionService.consume
    push_sentinel = "y16-fake-push-exec-id"

    def start_intercepting_push(
        self: ExecutionService, container_id: str, argv: list[str], **kwargs: object
    ) -> str:
        if "push" in argv:
            return push_sentinel
        return real_start(self, container_id, argv, **kwargs)  # type: ignore[arg-type]

    def consume_intercepting_push(
        self: ExecutionService, exec_id: str, **kwargs: object
    ) -> ExecResult:
        if exec_id == push_sentinel:
            return ExecResult(
                exit_code=0,
                stdout_tail="",
                stderr_tail="",
                stdout_truncated=False,
                stderr_truncated=False,
                stdout_total_bytes=0,
                stderr_total_bytes=0,
            )
        return real_consume(self, exec_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ExecutionService, "start", start_intercepting_push)
    monkeypatch.setattr(ExecutionService, "consume", consume_intercepting_push)

    await server.call_tool("git_push", {"workspace_id": workspace_id, "branch": "main"})

    events = audit_tests._read_events(config.audit.path)
    successes = [
        event
        for event in events
        if event["operation"] == "git_push" and event["result"] == "success"
    ]
    assert len(successes) == 1
    assert successes[0]["metadata"]["remote_repository"] == audit_tests._PUBLIC_REMOTE_IDENTITY


def test_rr006_read_file_missing_path_is_a_typed_workspace_error(tmp_path: Path) -> None:
    """RAT-007/RR-006: the service boundary never leaks raw FileNotFoundError."""
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    with pytest.raises(WorkspaceError):
        service.read_file("missing.txt")
