"""JPO-006/JPO-007 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.3; JPOV-003
… JPOV-007, JPOV-009): §14.4's promotion of a synchronous `exec` to a
background job, through the real server, a real Docker daemon and the real
default image.

Every test shortens `execution.sync_timeout_seconds` by config and runs a
command whose duration is far from it on the side that matters (a 1s
threshold against a 4s or 300s command, or an instant command) — never a
margin that a slow host could turn into a different outcome.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import docker
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import (
    AuditConfig,
    ExecutionConfig,
    RepositoryConfig,
    ServerConfig,
)
from mcp_chatgpt_sandbox.db.connection import open_db
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.jobs.store import NON_TERMINAL_STATES, JobStore
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools import execution as execution_tools
from mcp_chatgpt_sandbox.tools import execution_promotion
from mcp_chatgpt_sandbox.workspace.manager import DockerWorkspaceManager


@pytest.fixture(autouse=True)
def _namespaced_docker_resources(docker_client: docker.DockerClient) -> None:
    """Every test here creates a real workspace container through the
    server. `docker_client` is what gives a test its own G3R-015 resource
    namespace and removes its containers afterwards; autouse, because the
    first draft of this file requested it only in the one test that used
    the client directly, and the others leaked an un-namespaced
    `ws-example` container that a concurrent CI run could have adopted."""


_SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
#: Deliberately NOT shaped like anything `redact_secrets` recognises: an `env`
#: value is kept out of the audit log by never being written, not by pattern —
#: and it travels under a key name the audit log's key-based redaction ignores
#: too (`TOKEN` would have been masked, making the assertion vacuous).
_ENV_SECRET = "plain-env-value-jpo-009"


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
    *,
    execution: dict[str, object],
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
            "execution": ExecutionConfig.model_validate(execution),
        }
    )


async def _server(
    minimal_config: ServerConfig,
    default_image_tag: str,
    tmp_path: Path,
    **execution: object,
) -> tuple[Any, ServerConfig, str]:
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"sync_timeout_seconds": 1, **execution},
    )
    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    return server, config, opened.structured_content["workspace_id"]


async def _wait_for_state(server: Any, job_id: str, state: str, *, timeout: float = 30.0) -> Any:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        status = (await server.call_tool("exec_status", {"job_id": job_id})).structured_content
        if status["state"] == state:
            return status
        assert asyncio.get_event_loop().time() < deadline, f"job stuck in {status['state']!r}"
        await asyncio.sleep(0.2)


async def _processes_matching(server: Any, workspace_id: str, bracketed: str) -> str:
    check = await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": f"ps aux | grep '{bracketed}' || echo NONE"},
    )
    return str(check.structured_content["stdout_tail"])


def _jobs(config: ServerConfig) -> list[Any]:
    db = open_db(config.state_db_path)
    try:
        return [
            job
            for (workspace_id,) in db.execute("SELECT id FROM workspace")
            for job in JobStore(db).list_by_workspace(workspace_id)
        ]
    finally:
        db.close()


def _job_dirs(config: ServerConfig) -> list[Path]:
    jobs = config.state_db_path.parent / "jobs"
    return sorted(jobs.iterdir()) if jobs.is_dir() else []


async def test_jpov003_without_the_flag_an_overrun_is_still_killed_and_leaves_no_job(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    """I1. Both spellings of "did not ask": absent, and explicitly false."""
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)

    for arguments in ({}, {"promote_on_timeout": False}):
        with pytest.raises(ToolError, match="COMMAND_TIMEOUT"):
            await server.call_tool(
                "exec", {"workspace_id": workspace_id, "shell": "sleep 311", **arguments}
            )

    assert "NONE" in await _processes_matching(server, workspace_id, "[s]leep 311")
    assert _jobs(config) == []
    assert _job_dirs(config) == []


async def test_jpov004_with_the_flag_a_command_that_finishes_in_time_is_not_a_job(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    """I4 — and the opted-in path answers exactly as the default path does."""
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)
    call = {"workspace_id": workspace_id, "shell": "echo out; echo err >&2; exit 3"}

    default = (await server.call_tool("exec", call)).structured_content
    opted_in = (
        await server.call_tool("exec", {**call, "promote_on_timeout": True})
    ).structured_content

    assert opted_in == default
    assert (opted_in["promoted_to_job"], opted_in["exit_code"]) == (False, 3)
    assert (opted_in["stdout_tail"], opted_in["stderr_tail"]) == ("out\n", "err\n")
    assert opted_in["job_id"] is None
    assert _jobs(config) == []
    assert _job_dirs(config) == []


async def test_jpov005_an_overrun_continues_as_a_job_with_all_of_its_output(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    """I2, I3, I6. `$$` is printed on both sides of the promotion: the same
    pid proves the command was adopted, not re-run."""
    server, _config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)

    response = (
        await server.call_tool(
            "exec",
            {
                "workspace_id": workspace_id,
                "shell": "echo before $$; echo warn >&2; sleep 4; echo after $$",
                "promote_on_timeout": True,
            },
        )
    ).structured_content

    assert response["promoted_to_job"] is True
    assert response["state"] == "running"
    assert response["started_at"] is not None
    assert response["exit_code"] is None and response["stdout_tail"] is None
    job_id = response["job_id"]

    status = await _wait_for_state(server, job_id, "succeeded")
    assert status["exit_code"] == 0
    stdout = (
        await server.call_tool("exec_output", {"job_id": job_id, "stream": "stdout"})
    ).structured_content["content"]
    stderr = (
        await server.call_tool("exec_output", {"job_id": job_id, "stream": "stderr"})
    ).structured_content["content"]
    before, after = stdout.splitlines()
    assert before.startswith("before ") and after.startswith("after ")
    assert before.split()[1] == after.split()[1]
    assert stderr == "warn\n"


async def test_jpov005b_a_promoted_command_that_fails_reports_its_exit_code(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    server, _config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)

    response = (
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "shell": "sleep 4; exit 9", "promote_on_timeout": True},
        )
    ).structured_content

    status = await _wait_for_state(server, response["job_id"], "failed")
    assert status["exit_code"] == 9


async def test_jpov006_a_promoted_job_is_cancellable_and_protects_its_workspace_from_idle_stop(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)
    response = (
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "shell": "sleep 322", "promote_on_timeout": True},
        )
    ).structured_content
    job_id = response["job_id"]

    # I6: idle-stop sees an ordinary non-terminal job and leaves the
    # workspace alone, however long ago it was last used.
    db = open_db(config.state_db_path)
    try:
        (job,) = JobStore(db).list_by_workspace(workspace_id)
        assert (job.id, job.command, job.cwd) == (job_id, "sleep 322", "/workspace")
        assert job.state in NON_TERMINAL_STATES
        db.execute(
            "UPDATE workspace SET last_used_at = datetime('now', '-1000 seconds') || '+00:00'"
        )
        db.commit()
        stopped = DockerWorkspaceManager(docker_client, db).sweep_idle_workspaces(
            idle_stop_after_seconds=60,
            job_store=JobStore(db),
            audit_config=AuditConfig(enabled=False, path=tmp_path / "unused-audit.jsonl"),
        )
    finally:
        db.close()
    assert stopped == []

    await server.call_tool("exec_cancel", {"job_id": job_id})
    await _wait_for_state(server, job_id, "canceled")
    assert "NONE" in await _processes_matching(server, workspace_id, "[s]leep 322")


async def test_jpov006_a_promoted_job_times_out_on_the_clock_its_process_started_on(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    """D4-A, end to end: a 3s job allowance, 1s of it spent synchronously."""
    server, _config, workspace_id = await _server(
        minimal_config, default_image_tag, tmp_path, default_job_timeout_seconds=3
    )
    response = (
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "shell": "sleep 333", "promote_on_timeout": True},
        )
    ).structured_content

    await _wait_for_state(server, response["job_id"], "timed_out")
    assert "NONE" in await _processes_matching(server, workspace_id, "[s]leep 333")


async def test_jpov007_a_job_store_failure_during_promotion_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)

    def broken_create(self: JobStore, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(JobStore, "create", broken_create)

    with pytest.raises(ToolError, match="COMMAND_TIMEOUT"):
        await server.call_tool(
            "exec",
            {"workspace_id": workspace_id, "shell": "sleep 344", "promote_on_timeout": True},
        )

    assert "NONE" in await _processes_matching(server, workspace_id, "[s]leep 344")
    assert _jobs(config) == []


async def test_jpov007_a_log_file_failure_during_promotion_leaves_no_live_job_record(
    minimal_config: ServerConfig,
    default_image_tag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half-created case: the job record already exists when the
    failure happens, so it has to be driven terminal (I5) or it would pin
    the workspace against idle-stop like HR-021's stuck `starting` job.

    PJPO-001 (PJPOV-003): the write failure is real. The job's stdout log
    is pre-created as a symlink to `/dev/full` — `open` succeeds, every
    `write` fails with ENOSPC — and only the job id is pinned so the test
    knows where to put it. This replaced a monkeypatch of
    `StreamCapture.attach` that raised before touching any state, which
    passed against code that left the record stuck: the defect was
    exactly the state `attach` had already changed when it failed. Why
    not an unwritable `jobs/` directory: `mkdir` would fail before any
    file is opened, the one failure the old code already survived, so it
    would not have told the two apart."""
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)
    job_dir = config.state_db_path.parent / "jobs" / "job_diskfull"
    job_dir.mkdir(parents=True)
    (job_dir / "stdout.log").symlink_to("/dev/full")
    monkeypatch.setattr(execution_promotion, "new_job_id", lambda: "job_diskfull")

    with pytest.raises(ToolError, match="COMMAND_TIMEOUT"):
        await server.call_tool(
            "exec",
            {
                "workspace_id": workspace_id,
                "shell": "echo x; sleep 355",
                "promote_on_timeout": True,
            },
        )

    assert "NONE" in await _processes_matching(server, workspace_id, "[s]leep 355")
    (job,) = _jobs(config)
    assert (job.id, job.state.value) == ("job_diskfull", "failed")


async def test_jpov009_promotion_is_audited_with_the_job_id_and_without_secrets(
    minimal_config: ServerConfig, default_image_tag: str, tmp_path: Path
) -> None:
    """I10. The secret is in both places a client can put one: the command
    line (redacted by `write_audit_event`) and `env` (never recorded)."""
    server, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)
    response = (
        await server.call_tool(
            "exec",
            {
                "workspace_id": workspace_id,
                "shell": f"echo {_SECRET} >/dev/null; sleep 366",
                "env": {"BUILD_FLAVOUR": _ENV_SECRET},
                "promote_on_timeout": True,
            },
        )
    ).structured_content
    await server.call_tool("exec_cancel", {"job_id": response["job_id"]})
    await _wait_for_state(server, response["job_id"], "canceled")

    audit_text = config.audit.path.read_text()
    events = [json.loads(line) for line in audit_text.splitlines()]
    (promotion,) = [e for e in events if e["operation"] == "exec_promoted_to_job"]
    assert promotion["workspace_id"] == workspace_id
    assert promotion["metadata"]["job_id"] == response["job_id"]
    assert [e["operation"] for e in events].count("exec") == 1  # started: audited once, not twice
    assert _SECRET not in audit_text
    assert _ENV_SECRET not in audit_text


async def test_jpo008_run_exec_refuses_the_flag_when_it_has_nothing_to_promote_with(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """JPO-008 review finding: `run_exec` called directly with the flag but
    no `PromotionDeps` used to fall through to the default path and kill, at
    the timeout, a command its caller had asked to keep. `true` (not an
    overrunning command) on purpose: the refusal must come before anything
    is started, so nothing is left running whichever way this goes."""
    _server_, config, workspace_id = await _server(minimal_config, default_image_tag, tmp_path)
    db = open_db(config.state_db_path)
    try:
        with pytest.raises(ValueError, match="PromotionDeps"):
            await execution_tools.run_exec(
                workspace_id,
                None,
                "true",
                "/workspace",
                None,
                None,
                manager=DockerWorkspaceManager(docker_client, db),
                service=ExecutionService(docker_client),
                registry=RepositoryRegistry(config),
                db=db,
                container_config=config.container,
                execution_config=config.execution,
                audit_config=config.audit,
                ssh_config=config.ssh,
                promote_on_timeout=True,
            )
    finally:
        db.close()
