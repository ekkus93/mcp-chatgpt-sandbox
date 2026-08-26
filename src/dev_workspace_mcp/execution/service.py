"""Synchronous command execution inside a workspace container (§14, §15.5,
§16, S12).

`docker-py`'s `Container.exec_run` has no timeout parameter. The timeout
here is enforced by the *caller* (the async tool layer, via
`asyncio.wait_for` around `consume` run in a thread) rather than inside
this module's blocking read loop: a command producing no output — `sleep
100`, say — blocks the stream's iterator waiting for the *next* chunk, so
a deadline check placed inside that loop would simply never run while
blocked. `asyncio.wait_for` is the one thing that can actually interrupt
that wait from the outside. `kill` is called separately once the caller
gives up, to stop the command rather than merely stop waiting for it.

`kill` signals the process directly with `os.kill`, from the daemon's own
process — not via a second `docker exec` inside the container. The `Pid`
`exec_inspect` reports is the process's PID in the *host's* PID namespace
(confirmed empirically: that number does not exist when looked up from
inside the container's own PID namespace, where `ps` shows a different,
small PID for the same process). The daemon itself runs on the host
(§5.3, §40), so it can signal that PID directly; a second `exec` cannot,
because it runs inside the container's namespace and the number means
nothing there.

Job promotion on overrun (§14.4 — `promoted_to_job: true`) is not
implemented here: it needs the job store, which does not exist until S14.
Until then, exceeding the timeout fails the call with `CommandTimeoutError`
rather than silently hanging or claiming to promote a job that was never
created.
"""

from __future__ import annotations

import contextlib
import os
import signal
from dataclasses import dataclass

import docker


@dataclass(frozen=True, slots=True)
class ExecResult:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_total_bytes: int
    stderr_total_bytes: int


class ExecutionService:
    def __init__(self, docker_client: docker.DockerClient) -> None:
        self._docker = docker_client

    def start(
        self,
        container_id: str,
        cmd: list[str],
        *,
        cwd: str,
        user: str,
        env: dict[str, str] | None,
    ) -> str:
        """Creates and starts the exec, returning its id. Fast — does not
        wait for the command to produce output or exit."""
        api = self._docker.api
        exec_id: str = api.exec_create(
            container_id,
            cmd,
            stdout=True,
            stderr=True,
            workdir=cwd,
            user=user,
            environment=env,
        )["Id"]
        return exec_id

    def consume(
        self, exec_id: str, *, max_output_bytes: int, response_tail_lines: int
    ) -> ExecResult:
        """Blocks until the command exits, reading its output. Call via
        `asyncio.to_thread` under `asyncio.wait_for` for the timeout to be
        real — this method has no internal deadline of its own."""
        api = self._docker.api
        stream = api.exec_start(exec_id, stream=True, demux=True)

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_total = stderr_total = 0
        stdout_captured = stderr_captured = 0

        for out_chunk, err_chunk in stream:
            if out_chunk:
                stdout_total += len(out_chunk)
                if stdout_captured < max_output_bytes:
                    stdout_chunks.append(out_chunk)
                    stdout_captured += len(out_chunk)
            if err_chunk:
                stderr_total += len(err_chunk)
                if stderr_captured < max_output_bytes:
                    stderr_chunks.append(err_chunk)
                    stderr_captured += len(err_chunk)

        exit_code = api.exec_inspect(exec_id).get("ExitCode")
        stdout_bytes = b"".join(stdout_chunks)[:max_output_bytes]
        stderr_bytes = b"".join(stderr_chunks)[:max_output_bytes]

        return ExecResult(
            exit_code=exit_code if exit_code is not None else -1,
            stdout_tail=_tail(stdout_bytes, response_tail_lines),
            stderr_tail=_tail(stderr_bytes, response_tail_lines),
            stdout_truncated=stdout_total > max_output_bytes,
            stderr_truncated=stderr_total > max_output_bytes,
            stdout_total_bytes=stdout_total,
            stderr_total_bytes=stderr_total,
        )

    def kill(self, exec_id: str, *, grace_period_reached: bool = False) -> None:
        """Signals the exec'd process directly via `os.kill`: TERM, or
        KILL once `grace_period_reached`. See the module docstring for
        why this is not a second `docker exec`."""
        try:
            info = self._docker.api.exec_inspect(exec_id)
        except docker.errors.DockerException:
            return
        pid = info.get("Pid")
        if not pid or not info.get("Running", False):
            return

        sig = signal.SIGKILL if grace_period_reached else signal.SIGTERM
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def _tail(data: bytes, max_lines: int) -> str:
    if max_lines <= 0:
        return ""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    return "".join(lines[-max_lines:])
