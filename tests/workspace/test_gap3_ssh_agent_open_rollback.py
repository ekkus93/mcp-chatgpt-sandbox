"""GAP3 Y5 / G3AT-005: failed workspace opens must not retain repository keys."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.errors import (
    ContainerCreateFailedError,
    SSHCredentialCleanupFailedError,
)
from mcp_chatgpt_sandbox.jobs.store import JobStore
from mcp_chatgpt_sandbox.repositories import RepositoryRecord, RepositoryRegistry
from mcp_chatgpt_sandbox.tools import workspace as workspace_tools
from mcp_chatgpt_sandbox.workspace.records import WorkspaceRecord, WorkspaceState


class _RecordingAgentManager:
    """Implements both the pre-Y5 and intended post-Y5 orchestration APIs."""

    def __init__(self, socket_path: Path) -> None:
        self.record = SimpleNamespace(socket_path=socket_path)
        self.loaded = False
        self.unload_calls = 0

    # Pre-Y5 API: this is the bug — obtaining the mountable socket also loads the key.
    def ensure(self, workspace_id: str, key_path: Path):
        self.loaded = True
        return self.record

    # Post-Y5 API: socket/process availability is independent of key provisioning.
    def ensure_agent(self, workspace_id: str):
        return self.record

    def load_key(self, workspace_id: str, key_path: Path) -> None:
        self.loaded = True

    def unload_key(self, workspace_id: str) -> None:
        self.unload_calls += 1
        self.loaded = False


class _FailingCleanupAgentManager(_RecordingAgentManager):
    """Models a Y4 cleanup failure after Y5 has already loaded a key."""

    def unload_key(self, workspace_id: str) -> None:
        self.unload_calls += 1
        raise SSHCredentialCleanupFailedError(
            "synthetic credential cleanup failure",
            details={"workspace_id": workspace_id},
        )


class _FailingManager:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def ensure_workspace(self, **kwargs):
        raise self.exc


class _ReturningManager:
    def __init__(self, record: WorkspaceRecord) -> None:
        self.record = record

    def ensure_workspace(self, **kwargs):
        return self.record

    def sweep_idle_workspaces(self, **kwargs) -> None:
        return None


def _repository(tmp_path: Path) -> RepositoryRecord:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    return RepositoryRecord(
        id="example",
        canonical_path=repo_path,
        github="owner/example",
        ssh_key="example.ed25519",
    )


def _config(minimal_config: ServerConfig, repository: RepositoryRecord) -> ServerConfig:
    return minimal_config.model_copy(
        update={
            "repositories": [
                RepositoryConfig(
                    id=repository.id,
                    path=repository.canonical_path,
                    github=repository.github,
                    ssh_key=repository.ssh_key,
                )
            ]
        }
    )


def _seed_workspace(db: sqlite3.Connection) -> WorkspaceRecord:
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('example', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, container_id, container_name, image,
             workspace_path, state, created_at, last_used_at)
        VALUES
            ('ws_example', 'example', 'persistent', 'container-1', 'mcp-test',
             'example:image', '/workspace', 'running',
             '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
        """
    )
    db.commit()
    return WorkspaceRecord(
        id="ws_example",
        repository_id="example",
        mode="persistent",
        container_id="container-1",
        container_name="mcp-test",
        image="example:image",
        workspace_path="/workspace",
        state=WorkspaceState.RUNNING,
        created_at="2000-01-01T00:00:00+00:00",
        last_used_at="2000-01-01T00:00:00+00:00",
    )


def _patch_nonessential_open_work(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_config: object | None,
) -> None:
    monkeypatch.setattr(workspace_tools, "detect_default_branch", lambda _path: "main")
    monkeypatch.setattr(
        workspace_tools,
        "verify_remote_protection",
        lambda **_kwargs: SimpleNamespace(
            status="unverified",
            default_branch="main",
            reason="synthetic test: no API credential",
        ),
    )
    monkeypatch.setattr(workspace_tools, "load_project_config", lambda _path: project_config)
    monkeypatch.setattr(
        workspace_tools, "resolve_effective_image", lambda _project_config, _docker: "example:image"
    )
    monkeypatch.setattr(workspace_tools, "resolve_caches", lambda **_kwargs: [])
    monkeypatch.setattr(workspace_tools, "build_workspace_run_kwargs", lambda **_kwargs: {})
    monkeypatch.setattr(workspace_tools, "build_workspace_mounts", lambda **_kwargs: [])
    monkeypatch.setattr(workspace_tools, "build_known_hosts_mount", lambda _path: object())
    monkeypatch.setattr(workspace_tools, "build_cache_mounts", lambda _caches: [])
    monkeypatch.setattr(workspace_tools, "build_ssh_agent_mount", lambda _path: object())
    monkeypatch.setattr(workspace_tools, "store_remote_protection", lambda *_args: None)
    monkeypatch.setattr(
        workspace_tools, "_runtime_image_digest", lambda _docker_client, _record: "sha256:example"
    )


def _setup_project_config() -> SimpleNamespace:
    return SimpleNamespace(
        version=1,
        image=None,
        caches=[],
        environment={},
        setup=[SimpleNamespace(command="false")],
    )


def _docker_client_with_image() -> MagicMock:
    return MagicMock()


async def test_at_g3at005_docker_create_failure_never_loads_the_repository_key(
    minimal_config: ServerConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix reproduction: `ensure(..., key_path)` loads before Docker create."""
    repository = _repository(tmp_path)
    config = _config(minimal_config, repository)
    db = open_db(":memory:")
    registry = RepositoryRegistry(config)
    agent_manager = _RecordingAgentManager(tmp_path / "agent.sock")
    _patch_nonessential_open_work(monkeypatch, project_config=None)

    with pytest.raises(ContainerCreateFailedError, match="synthetic create failure"):
        await workspace_tools.open_workspace(
            "example",
            "persistent",
            manager=_FailingManager(ContainerCreateFailedError("synthetic create failure")),
            docker_client=MagicMock(),
            execution_service=MagicMock(),
            registry=registry,
            db=db,
            job_store=JobStore(db),
            docker_config=config.docker,
            container_config=config.container,
            execution_config=config.execution,
            resources_config=config.resources,
            ssh_config=config.ssh,
            workspace_config=config.workspace,
            github_config=config.github,
            git_policy_config=config.git_policy,
            audit_config=config.audit,
            repository_roots=config.repository_roots,
            ssh_agent_manager=agent_manager,  # type: ignore[arg-type]
        )

    assert agent_manager.loaded is False


async def test_at_g3at005_setup_failure_rolls_back_an_already_loaded_key(
    minimal_config: ServerConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once setup needs the key, any later open failure must unload it again."""
    repository = _repository(tmp_path)
    config = _config(minimal_config, repository)
    db = open_db(":memory:")
    record = _seed_workspace(db)
    registry = RepositoryRegistry(config)
    agent_manager = _RecordingAgentManager(tmp_path / "agent.sock")
    _patch_nonessential_open_work(monkeypatch, project_config=_setup_project_config())
    monkeypatch.setattr(workspace_tools, "load_setup_completed_container_id", lambda *_args: None)

    async def _fail_setup(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic setup failure")

    monkeypatch.setattr(workspace_tools, "run_setup_steps", _fail_setup)

    with pytest.raises(RuntimeError, match="synthetic setup failure"):
        await workspace_tools.open_workspace(
            "example",
            "persistent",
            manager=_ReturningManager(record),
            docker_client=_docker_client_with_image(),
            execution_service=MagicMock(),
            registry=registry,
            db=db,
            job_store=JobStore(db),
            docker_config=config.docker,
            container_config=config.container,
            execution_config=config.execution,
            resources_config=config.resources,
            ssh_config=config.ssh,
            workspace_config=config.workspace,
            github_config=config.github,
            git_policy_config=config.git_policy,
            audit_config=config.audit,
            repository_roots=config.repository_roots,
            ssh_agent_manager=agent_manager,  # type: ignore[arg-type]
        )

    assert agent_manager.loaded is False
    assert agent_manager.unload_calls == 1


async def test_at_g3at005_cleanup_failure_is_surfaced_instead_of_forgotten(
    minimal_config: ServerConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rollback replaces the setup error with the typed cleanup failure."""
    repository = _repository(tmp_path)
    config = _config(minimal_config, repository)
    db = open_db(":memory:")
    record = _seed_workspace(db)
    registry = RepositoryRegistry(config)
    agent_manager = _FailingCleanupAgentManager(tmp_path / "agent.sock")
    _patch_nonessential_open_work(monkeypatch, project_config=_setup_project_config())
    monkeypatch.setattr(workspace_tools, "load_setup_completed_container_id", lambda *_args: None)

    async def _fail_setup(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic setup failure")

    monkeypatch.setattr(workspace_tools, "run_setup_steps", _fail_setup)

    with pytest.raises(
        SSHCredentialCleanupFailedError, match="synthetic credential cleanup failure"
    ):
        await workspace_tools.open_workspace(
            "example",
            "persistent",
            manager=_ReturningManager(record),
            docker_client=_docker_client_with_image(),
            execution_service=MagicMock(),
            registry=registry,
            db=db,
            job_store=JobStore(db),
            docker_config=config.docker,
            container_config=config.container,
            execution_config=config.execution,
            resources_config=config.resources,
            ssh_config=config.ssh,
            workspace_config=config.workspace,
            github_config=config.github,
            git_policy_config=config.git_policy,
            audit_config=config.audit,
            repository_roots=config.repository_roots,
            ssh_agent_manager=agent_manager,  # type: ignore[arg-type]
        )

    assert agent_manager.loaded is True
    assert agent_manager.unload_calls == 1
