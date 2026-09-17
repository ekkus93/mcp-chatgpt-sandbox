"""S15: `exec_start` / `exec_status` / `exec_output` — the basic contract
and H29's bounded-output-by-default fix, against a real Docker daemon and
the real S7 default image (§15.3, §63, §64).

`exec_cancel`, timeout/traversal handling, HR-020/HAT-017's failed-
termination coverage, the H28 disconnect/reconnect test, and the large-
output/docker-unreachable tests moved to
`test_async_execution_tools_cancel.py` — this file (838 lines) grew past
800 and needed splitting, the same way `tools/git.py` and
`tests/workspace/test_git_commit_tool.py` were split before it.
`_configured`/`_open_workspace`/`_wait_until` are duplicated in both
files, matching this test suite's own established convention for small
per-file test helpers.
"""

import asyncio
from pathlib import Path

import docker
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import ExecutionConfig, RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server


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


async def test_at_g2r017_exec_output_rejects_an_inverted_byte_range(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """G2R-017 (`docs/GAP2_SPEC.md`): a real regression — with
    `byte_start=100, byte_end=50`, the old code let `requested =
    byte_end - byte_start` go negative, `capped = max(min(requested,
    limit), 0)` floored it back to `0`, and the client got back empty
    `content` with `truncated: true` — indistinguishable from "there is
    more to read, page again," inviting a paging loop that can never
    terminate rather than a clear error naming the actual mistake."""
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

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool("exec_output", {"job_id": job_id, "byte_start": 100, "byte_end": 50})


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
