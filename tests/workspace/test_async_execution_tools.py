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
from dev_workspace_mcp.workspace.naming import container_name


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


# --- H29 (`docs/HARDENING_TODO.md`, HD3): bound output by default ----------------


async def test_at_h029_exec_output_default_is_bounded_to_roughly_64kib(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H29's own Defect: the old default was `max_output_bytes +
    tail_keep_bytes` (10.25 MiB with the stock config), defeating §15.5's
    stated purpose. A real job producing far more than 64 KiB, retrieved
    with no `max_bytes` at all, must come back small and marked
    truncated — the full capture stays reachable, just only through an
    explicit `max_bytes`."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start",
        # ~200 KiB of realistic, line-oriented stdout — comfortably past
        # the default response cap, comfortably under the on-disk
        # max_output_bytes cap (still the stock 10 MiB), so this
        # specifically exercises the *response* default, not §15.6's
        # on-disk one.
        {"workspace_id": workspace_id, "shell": "yes '0123456789' | head -n 20000"},
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished, timeout=20.0)

    default_output = await server.call_tool("exec_output", {"job_id": job_id})
    content = default_output.structured_content
    assert len(content["content"].encode()) <= 64 * 1024
    assert content["truncated"] is True
    assert content["stream_total_bytes"] >= 200_000

    # The full capture is still reachable — just only through an
    # explicit, larger max_bytes.
    full_output = await server.call_tool(
        "exec_output", {"job_id": job_id, "max_bytes": 1024 * 1024}
    )
    assert len(full_output.structured_content["content"].encode()) >= 200_000
    assert full_output.structured_content["truncated"] is False


@pytest.mark.parametrize(
    "field",
    ["byte_start", "byte_end", "line_start", "line_end", "max_bytes"],
)
async def test_at_h029_exec_output_rejects_a_negative_offset(
    field: str,
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H29: a negative value used to silently slice from the end
    (Python's own slicing semantics) instead of the documented "position
    N from the start" — reject outright."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "argv": ["echo", "hello"]}
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool("exec_output", {"job_id": job_id, field: -1})


async def test_at_h029_stored_bytes_and_stream_total_bytes_diverge_once_truncated(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H29: `stored_bytes` (what `byte_start`/`byte_end` actually index
    into) and `stream_total_bytes` (§15.6's uncapped production total)
    used to be silently conflated — a client paging by byte offset
    against `stream_total_bytes` on a stream that was ever truncated on
    disk would be indexing into the wrong scale. Forcing the on-disk cap
    down to something the real output blows past makes the divergence
    real, not hypothetical."""
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
        {"workspace_id": workspace_id, "shell": "yes '0123456789' | head -n 20000"},
    )
    job_id = started.structured_content["job_id"]

    async def finished() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "succeeded")

    await _wait_until(finished, timeout=20.0)

    output = await server.call_tool("exec_output", {"job_id": job_id, "max_bytes": 1024 * 1024})
    content = output.structured_content
    assert content["stream_total_bytes"] >= 200_000
    # On-disk cap: max_output_bytes + tail_keep_bytes + a small marker —
    # far less than stream_total_bytes here.
    assert content["stored_bytes"] < content["stream_total_bytes"]
    assert content["stored_bytes"] <= 4096 + 512 + 64

    # byte_start/byte_end index into stored_bytes, not stream_total_bytes
    # — a start right at the edge of what's actually stored still comes
    # back with real content (the kept tail, not an error or emptiness).
    near_end = await server.call_tool(
        "exec_output",
        {
            "job_id": job_id,
            "byte_start": content["stored_bytes"] - 5,
            "byte_end": content["stored_bytes"],
        },
    )
    assert len(near_end.structured_content["content"]) > 0


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


async def test_at_h030_exec_start_rejects_a_traversal_cwd_and_clamps_timeout(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H30 (`docs/HARDENING_TODO.md`, HR-010): `_validate_cwd`/
    `_resolve_timeout` are shared with `exec` — confirming both fixes
    apply to `exec_start` too, not just the synchronous tool."""
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        execution={"default_job_timeout_seconds": 1},
    )
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "exec_start",
            {"workspace_id": workspace_id, "argv": ["pwd"], "cwd": "/workspace/../etc"},
        )

    # timeout_seconds clamped to the 1s ceiling despite requesting 100s.
    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "shell": "sleep 60", "timeout_seconds": 100},
    )
    job_id = started.structured_content["job_id"]

    async def timed_out() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "timed_out")

    await _wait_until(timed_out, timeout=10.0)


# --- HR-020/HAT-017: a failed termination is not reported as success --------------


async def test_at_hat017_cancel_kills_the_whole_process_group_not_only_the_direct_child(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """A real regression: `os.kill(pid, sig)` only ever reached the
    direct exec'd process — `sh`'s own pid here — leaving any child it
    forked (`sleep 61`, backgrounded) running. `os.killpg` on the whole
    process group (confirmed empirically that each `docker exec` starts
    as the leader of its own fresh group) reaches both."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 61 & sleep 60"}
    )
    job_id = started.structured_content["job_id"]

    await server.call_tool("exec_cancel", {"job_id": job_id})

    async def canceled() -> bool:
        status = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(status.structured_content["state"] == "canceled")

    await _wait_until(canceled, timeout=10.0)

    check = await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": "ps aux | grep -E '[s]leep 6[01]' || echo NONE"},
    )
    assert "NONE" in check.structured_content["stdout_tail"]


async def test_at_hat017_failed_termination_is_reported_and_not_marked_canceled(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HAT-017: with a process the daemon cannot signal, `exec_cancel`
    SHALL return a structured error, the job SHALL NOT be marked
    `canceled`, and the daemon SHALL still shut down cleanly (here:
    reach a terminal state within a bounded wait, not hang). A genuine
    `PermissionError` needs a real privilege split (a process owned by a
    uid this daemon cannot signal) this sandbox has no way to set up
    without root — `os.killpg` is monkeypatched to raise it instead, the
    same failure it would genuinely raise under that condition."""
    import dev_workspace_mcp.execution.service as service_module

    def _raise_permission_error(pgid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(service_module.os, "killpg", _raise_permission_error)

    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 60"}
    )
    job_id = started.structured_content["job_id"]

    with pytest.raises(ToolError, match="CONTAINER_RUNTIME_FAILED"):
        await server.call_tool("exec_cancel", {"job_id": job_id})

    status = await server.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["state"] != "canceled"

    # Bounded, not hung: the force-closed stream lets `drain_stream`'s
    # blocked read return even though the process itself was never
    # actually signaled (the monkeypatch prevented that) — proving the
    # executor thread genuinely gets released rather than held forever.
    async def reached_a_terminal_state() -> bool:
        current = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(current.structured_content["state"] == "failed")

    await _wait_until(reached_a_terminal_state, timeout=10.0)


async def test_rr013_failed_termination_via_a_real_exec_inspect_failure(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """RR-013 (`docs/REMEDIATION_SPEC.md` §10): a different failure point
    in `kill` than the sibling test above — `exec_inspect` itself raising
    (here, forced the real way: the container is removed out from under
    a still-running exec, so a subsequent `exec_inspect` genuinely 404s
    — not `os.killpg` monkeypatched). Reproduced empirically before this
    fix that this used to be swallowed: `exec_cancel` returned
    `{"state": "canceled"}`, a clean success, despite the process never
    having been signaled at all (the container it lived in was already
    gone). Same assertions as the sibling test: a structured error, the
    job not marked `canceled`, and a bounded reach of a terminal state
    rather than a hang."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open_workspace(server)

    started = await server.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 60"}
    )
    job_id = started.structured_content["job_id"]

    docker_client.containers.get(container_name(workspace_id)).remove(force=True)

    with pytest.raises(ToolError, match="CONTAINER_RUNTIME_FAILED"):
        await server.call_tool("exec_cancel", {"job_id": job_id})

    status = await server.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["state"] != "canceled"

    async def reached_a_terminal_state() -> bool:
        current = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(current.structured_content["state"] == "failed")

    await _wait_until(reached_a_terminal_state, timeout=10.0)


async def test_at_h028_exec_start_disconnect_reconnect_job_still_queryable(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-006, made genuinely cross-session (H28, `docs/HARDENING_TODO.md`,
    HR-030): the predecessor of this test simulated "disconnect" by
    `del`-ing the `exec_start` response inside the *same* Python
    `server` object and the *same* `AsyncJobRunner` instance — proving
    nothing about whether job state survives independent of the
    in-process object graph that started it, since nothing here ever
    actually severed a connection. `create_server` opens its own real,
    file-backed `sqlite3.Connection` (`config.state_db_path`, not
    `:memory:`) and its own fresh `AsyncJobRunner` on every call — a
    *second*, independent `create_server(config)` instance is a much
    closer analogue to a genuinely different client session hitting the
    daemon fresh, with no Python reference to anything the first session
    created: its `AsyncJobRunner` never started this job, so
    `exec_status`/`exec_output` here can only be reading real, persisted
    `JobStore` state (§15.4's own durability contract), not an
    in-memory shortcut."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    session_one = create_server(config)
    workspace_id = await _open_workspace(session_one)

    started = await session_one.call_tool(
        "exec_start", {"workspace_id": workspace_id, "shell": "sleep 1 && echo built"}
    )
    job_id = started.structured_content["job_id"]
    del session_one  # the "disconnect" — no reference to this session survives

    # "Reconnect": a genuinely independent server instance, sharing only
    # the on-disk state `config` points at — the same session_id no
    # in-process client of session_one would ever hold.
    session_two = create_server(config)
    status = await session_two.call_tool("exec_status", {"job_id": job_id})
    assert status.structured_content["job_id"] == job_id
    assert status.structured_content["state"] in ("running", "succeeded")

    async def finished() -> bool:
        s = await session_two.call_tool("exec_status", {"job_id": job_id})
        return bool(s.structured_content["state"] == "succeeded")

    await _wait_until(finished)
    output = await session_two.call_tool("exec_output", {"job_id": job_id})
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
    from dev_workspace_mcp.repositories import RepositoryRegistry
    from dev_workspace_mcp.tools import execution as execution_tools

    server = MCPServer(name="t")
    db = open_db(minimal_config.state_db_path)
    execution_tools.register(
        server,
        db=db,
        docker_client=None,
        registry=RepositoryRegistry(minimal_config),
        container_config=minimal_config.container,
        execution_config=minimal_config.execution,
        state_dir=minimal_config.state_db_path.parent,
        audit_config=minimal_config.audit,
        ssh_config=minimal_config.ssh,
    )
    with pytest.raises(ToolError, match="Docker is not reachable"):
        await server.call_tool("exec_start", {"workspace_id": "ws_example", "argv": ["true"]})
