"""S15: `exec_start` / `exec_status` / `exec_output` / `exec_cancel`,
against a real Docker daemon and the real S7 default image (§15.3, §63,
§64)."""

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import docker
from dev_workspace_mcp.config.models import ExecutionConfig, RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
    *,
    execution: dict[str, object] | None = None,
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    update: dict[str, object] = {
        "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
        "repositories": [RepositoryConfig(id="example", path=repo_path, github="owner/example")],
    }
    if execution is not None:
        update["execution"] = ExecutionConfig.model_validate(execution)
    return minimal_config.model_copy(update=update)


async def _open_workspace(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    workspace_id: str = result.structured_content["workspace_id"]
    return workspace_id


async def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if await predicate():
            return
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(interval)


async def test_exec_start_returns_a_running_job(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    result = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "hello"]}
    )
    assert result.structured_content["state"] == "running"
    assert result.structured_content["job_id"].startswith("job_")
    assert result.structured_content["started_at"] is not None


async def test_exec_status_reports_success_after_completion(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "hello job"]}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    status = await server.call_tool("exec_status", {"job_id": job_id})
    content = status.structured_content
    assert content["exit_code"] == 0
    assert "hello job" in content["stdout_tail"]
    assert content["output_truncated"] is False
    assert content["elapsed_seconds"] >= 0


async def test_exec_status_reports_failure_exit_code(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "exit 7"}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "failed")

    await _wait_until(finished)
    status = await server.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["exit_code"] == 7


async def test_exec_status_on_unknown_job_raises_not_found(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)

    with pytest.raises(ToolError, match="JOB_NOT_FOUND"):
        await server.call_tool("exec_status", {"job_id": "job_does_not_exist"})


async def test_exec_output_retrieves_full_stdout_after_completion(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "shell": "printf 'line1\\nline2\\nline3\\n'"},
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    output = await server.call_tool("exec_output", {"job_id": job_id})
    assert output.structured_content["content"] == "line1\nline2\nline3\n"
    assert output.structured_content["stream"] == "stdout"
    assert output.structured_content["stream_total_bytes"] == len("line1\nline2\nline3\n")
    assert output.structured_content["truncated"] is False


async def test_exec_output_line_window(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "shell": "printf 'a\\nb\\nc\\nd\\n'"},
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    output = await server.call_tool(
        "exec_output", {"job_id": job_id, "line_start": 1, "line_end": 3}
    )
    assert output.structured_content["content"] == "b\nc\n"


async def test_exec_output_byte_range(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "printf '0123456789'"}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    output = await server.call_tool(
        "exec_output", {"job_id": job_id, "byte_start": 2, "byte_end": 5}
    )
    assert output.structured_content["content"] == "234"


async def test_exec_output_stderr_stream(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "echo oops 1>&2"}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    output = await server.call_tool("exec_output", {"job_id": job_id, "stream": "stderr"})
    assert "oops" in output.structured_content["content"]


async def test_exec_output_rejects_mutually_exclusive_ranges(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "x"]}
    )
    job_id = started.structured_content["job_id"]

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec_output",
            {"job_id": job_id, "byte_start": 0, "line_start": 0},
        )


async def test_exec_output_rejects_unknown_stream(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "x"]}
    )
    job_id = started.structured_content["job_id"]

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool("exec_output", {"job_id": job_id, "stream": "stdin"})


async def test_exec_cancel_terminates_a_running_job(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 60"}
    )
    job_id = started.structured_content["job_id"]

    cancel = await server.call_tool("exec_cancel", {"job_id": job_id})
    assert cancel.structured_content["state"] in ("running", "canceled")

    async def canceled() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "canceled")

    await _wait_until(canceled, timeout=10.0)

    # The classic `[s]leep` bracket idiom: matches the real process
    # without matching its own grep invocation, and "60" specifically
    # excludes the container's own long-running PID 1 (`sleep infinity`).
    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "ps aux | grep '[s]leep 60' || echo NONE"}
    )
    assert "NONE" in check.structured_content["stdout_tail"]


async def test_exec_cancel_on_already_finished_job_is_a_noop(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "done"]}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    cancel = await server.call_tool("exec_cancel", {"job_id": job_id})
    assert cancel.structured_content["state"] == "succeeded"


async def test_exec_start_honors_timeout_seconds_and_marks_timed_out(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "shell": "sleep 60", "timeout_seconds": 1},
    )
    job_id = started.structured_content["job_id"]

    async def timed_out() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "timed_out")

    await _wait_until(timed_out, timeout=10.0)

    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "ps aux | grep '[s]leep 60' || echo NONE"}
    )
    assert "NONE" in check.structured_content["stdout_tail"]


async def test_exec_start_disconnect_reconnect_job_still_queryable(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-006: start a long build, disconnect/reconnect, job still
    queryable. Simulated by discarding the `exec_start` response and
    looking the job up again purely by id, as a fresh client would."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 1 && echo built"}
    )
    job_id = started.structured_content["job_id"]
    del started  # the "disconnect" — nothing further comes from this call

    # "Reconnect": a fresh status query using only the remembered job_id.
    status = await server.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["job_id"] == job_id
    assert status.structured_content["state"] in ("running", "succeeded")

    async def finished() -> bool:
        s = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(s.structured_content["state"] == "succeeded")

    await _wait_until(finished)
    output = await server.call_tool("exec_output", {"job_id": job_id})
    assert "built" in output.structured_content["content"]


async def test_at_007_multi_megabyte_output_stays_bounded_and_retrievable(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-007: multi-megabyte output stays bounded in responses,
    retrievable incrementally (§15.5, §15.6, S16)."""
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"max_output_bytes": 4096, "tail_keep_bytes": 512, "response_tail_lines": 5},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        # ~3.3 MiB of realistic, line-oriented stdout (a real build log
        # isn't one giant unbroken chunk) — far past the 4 KiB on-disk cap.
        {
            "workspace_id": workspace_id,
            "shell": "yes '0123456789' | head -n 300000",
        },
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished, timeout=30.0)

    status = await server.call_tool("exec_status", {"job_id": job_id})
    content = status.structured_content
    # Bounded in the response: the last few lines, not 3+ MiB of output.
    assert len(content["stdout_tail"]) < 4096
    assert content["output_truncated"] is True
    assert content["stdout_total_bytes"] >= 3_000_000

    # Retrievable incrementally: a byte-range read against the captured
    # (capped) log gets exactly the slice asked for, not everything.
    chunk = await server.call_tool(
        "exec_output", {"job_id": job_id, "byte_start": 0, "byte_end": 100}
    )
    assert len(chunk.structured_content["content"]) == 100
    assert chunk.structured_content["stream_total_bytes"] >= 3_000_000

    # The on-disk log itself stayed bounded — nowhere near the full 3+ MiB.
    log_path = Path(f"{config.state_db_path.parent}/jobs/{job_id}/stdout.log")
    assert log_path.exists()
    assert log_path.stat().st_size < 4096 + 512 + 200  # cap + tail + marker overhead


async def test_exec_status_and_output_omission_marker_appears_when_capped(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        # tail_keep_bytes generous relative to max_output_bytes so the
        # default response limit (max_output_bytes + tail_keep_bytes)
        # comfortably covers the marker itself, not just the cap point.
        execution={"max_output_bytes": 100, "tail_keep_bytes": 100},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "shell": "head -c 10000 /dev/zero | tr '\\0' 'x'"},
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished, timeout=15.0)

    output = await server.call_tool("exec_output", {"job_id": job_id})
    assert "bytes omitted" in output.structured_content["content"]
    assert output.structured_content["logs_pruned"] is False


async def test_exec_start_fails_clearly_when_docker_unreachable(
    minimal_config: ServerConfig,
) -> None:
    from mcp.server import MCPServer

    from dev_workspace_mcp.db import open_db
    from dev_workspace_mcp.tools import execution as execution_tools

    server = MCPServer(name="t")
    db = open_db(minimal_config.state_db_path)
    execution_tools.register(
        server,
        db=db,
        docker_client=None,
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        state_dir=minimal_config.state_db_path.parent,
    )
    with pytest.raises(ToolError, match="Docker is not reachable"):
        await server.call_tool("exec_start", {"workspace_id": "ws_example", "argv": ["true"]})
