"""H28 (`docs/HARDENING_TODO.md`, HR-030): restart-survival coverage for
jobs, through the real `serve` CLI startup sequence and a genuinely
independent post-restart session — not the pieces exercised in
isolation elsewhere.

`tests/jobs/test_reconcile.py` already covers `reconcile_jobs` as a pure
function; `tests/test_cli.py::test_serve_reconciles_non_terminal_jobs_and_warns`
already covers the CLI wiring into it, but against a hand-inserted
`JobStore` row (`pid=1`, no real process, no real output ever written) —
proving reconciliation flips the DB column, not that a job's *already-
captured output* is still correctly served through the real `exec_status`/
`exec_output` MCP tools by a *different* server instance afterward. This
file closes that gap: a real, Docker-backed job that has already
produced real output, "crashed" (abandoned mid-run, no clean shutdown),
reconciled through the real `serve` CLI command, then queried by a
third, independent `create_server` instance standing in for the
restarted daemon.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import docker
import pytest
import yaml
from click.testing import CliRunner
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.cli import main
from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


def _write_config_file(config: ServerConfig, tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    return config_path


async def _open_workspace(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    workspace_id: str = result.structured_content["workspace_id"]
    return workspace_id


async def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if await predicate():
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError("condition not met within timeout")
        await asyncio.sleep(interval)


async def test_at_h028_a_crashed_jobs_output_survives_a_real_restart(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A real job, already mid-run with real captured output, "crashes"
    (its session is simply abandoned — no cancel, no clean shutdown),
    then goes through the *real* `serve` CLI reconciliation, then is
    queried by a *third*, independent server instance — the closest
    approximation of an actual daemon restart this test harness can
    produce (§73's own steps 3-5, not a mocked stand-in for any of
    them)."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    config_path = _write_config_file(config, tmp_path)

    session_before_crash = create_server(config)
    workspace_id = await _open_workspace(session_before_crash)

    started = await session_before_crash.call_tool(
        "exec_start",
        {
            "workspace_id": workspace_id,
            "shell": "echo before-the-crash && sleep 30",
        },
    )
    job_id = started.structured_content["job_id"]

    async def has_produced_output() -> bool:
        out = await session_before_crash.call_tool(
            "exec_output", {"job_id": job_id, "stream": "stdout"}
        )
        return "before-the-crash" in out.structured_content["content"]

    await _wait_until(has_produced_output)

    # "Crash": no cancel, no finish — `session_before_crash` (its
    # AsyncJobRunner and background drain task included) is never
    # touched again below, exactly as an unclean daemon shutdown leaves
    # it. The job itself, and its already-captured output, live on only
    # in Docker and the on-disk state this session leaves behind.

    # §73 steps 3-5, for real: the actual `serve` CLI command, run
    # through its real startup sequence — reconciliation included — with
    # only the blocking transport step mocked out.
    with (
        patch("mcp_chatgpt_sandbox.cli.docker_available", return_value=True),
        patch("mcp_chatgpt_sandbox.cli.run_server") as mock_run,
    ):
        result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert job_id in result.output
    assert "lost" in result.output
    mock_run.assert_called_once()

    # "The restarted daemon": a genuinely independent server instance —
    # its own AsyncJobRunner never started this job, so anything it
    # reports can only come from real, persisted state.
    session_after_restart = create_server(config)
    status = await session_after_restart.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["state"] == "lost"

    output = await session_after_restart.call_tool(
        "exec_output", {"job_id": job_id, "stream": "stdout"}
    )
    assert "before-the-crash" in output.structured_content["content"]


async def test_at_h028_a_finished_jobs_output_survives_a_real_restart(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The non-crash counterpart: a job that completed normally *before*
    the restart keeps its real terminal state (not `lost`) and its
    output, through the same real `serve` reconciliation and a fresh
    session — reconciliation only touches non-terminal jobs (§15.4), so
    this proves it correctly leaves an already-finished job alone."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    config_path = _write_config_file(config, tmp_path)

    session_before_restart = create_server(config)
    workspace_id = await _open_workspace(session_before_restart)

    started = await session_before_restart.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "echo done-before-restart"}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        s = await session_before_restart.call_tool("exec_status", {"job_id": job_id})
        return bool(s.structured_content["state"] == "succeeded")

    await _wait_until(finished)
    # `session_before_restart` is never touched again below.

    with (
        patch("mcp_chatgpt_sandbox.cli.docker_available", return_value=True),
        patch("mcp_chatgpt_sandbox.cli.run_server") as mock_run,
    ):
        result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert job_id not in result.output  # nothing to report lost
    mock_run.assert_called_once()

    session_after_restart = create_server(config)
    status = await session_after_restart.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["state"] == "succeeded"

    output = await session_after_restart.call_tool(
        "exec_output", {"job_id": job_id, "stream": "stdout"}
    )
    assert "done-before-restart" in output.structured_content["content"]


async def test_at_h028_exec_status_on_an_unknown_job_after_restart_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A hostile-input companion to the two tests above (HR-030): a
    plausible-looking but never-real `job_id`, queried through a fresh
    post-restart session with no `AsyncJobRunner` state whatsoever to
    fall back on, still fails closed with `JOB_NOT_FOUND` rather than a
    crash or a silently-empty result."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")

    session_after_restart = create_server(config)
    with pytest.raises(ToolError, match="JOB_NOT_FOUND"):
        await session_after_restart.call_tool("exec_status", {"job_id": "job_00000000deadbeef"})
