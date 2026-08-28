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

`start_and_open_stream` / `drain_stream` (S15) split what `start` /
`consume` do into a version the async job runner can use: docker-py's
`exec_start(stream=True)` issues its HTTP POST — the call that actually
starts the process server-side — eagerly, before returning; only reading
the response it returns is lazy (verified against docker-py's own
source). That means the exec can be started and its real pid obtained
*before* handing the (already-open) stream off to a background task to
drain, rather than the two racing the way they would if starting and
draining were one blocking call.

That said, `exec_inspect` immediately after `exec_start` does not always
have the pid yet, even once the exec's own stream is already producing
output (RR-022, `docs/REMEDIATION_SPEC.md` §15 — reproduced empirically,
not assumed). `pid_of` retries briefly to close that window; see its
docstring.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass

import docker

_logger = logging.getLogger(__name__)

#: RR-022 (`docs/REMEDIATION_SPEC.md` §15): `pid_of`'s retry budget for a
#: freshly-started exec whose pid `exec_inspect` has not reported yet.
#: Empirically tuned (see `pid_of`'s docstring): 5 retries, 10ms apart —
#: at most ~50ms added latency — eliminated the race in 30/30 trials
#: where 6/30 (20%) still came back empty with a single, unretried call.
_PID_RETRY_ATTEMPTS = 5
_PID_RETRY_INTERVAL_SECONDS = 0.01


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
            stdout_tail=tail_text(stdout_bytes, response_tail_lines),
            stderr_tail=tail_text(stderr_bytes, response_tail_lines),
            stdout_truncated=stdout_total > max_output_bytes,
            stderr_truncated=stderr_total > max_output_bytes,
            stdout_total_bytes=stdout_total,
            stderr_total_bytes=stderr_total,
        )

    def start_and_open_stream(
        self,
        container_id: str,
        cmd: list[str],
        *,
        cwd: str,
        user: str,
        env: dict[str, str] | None,
    ) -> tuple[
        str, docker.types.daemon.CancellableStream[tuple[bytes | None, bytes | None]], int | None
    ]:
        """Creates the exec and starts it (§15.3's `exec_start` tool),
        returning `(exec_id, stream, pid)`. Blocking but fast: returns
        once the process has actually started, not once it produces
        output or exits — see the module docstring for why the exec
        itself is already running by this point. The pid, separately,
        may not be — see `pid_of`, which this calls and retries through.
        Pass `stream` to `drain_stream` to read it; do not call
        `exec_start` on this `exec_id` again."""
        exec_id = self.start(container_id, cmd, cwd=cwd, user=user, env=env)
        stream = self._docker.api.exec_start(exec_id, stream=True, demux=True)
        return exec_id, stream, self.pid_of(exec_id)

    def drain_stream(
        self,
        exec_id: str,
        stream: docker.types.daemon.CancellableStream[tuple[bytes | None, bytes | None]],
        *,
        on_stdout: Callable[[bytes], None],
        on_stderr: Callable[[bytes], None],
    ) -> int:
        """Blocks reading `stream` (from `start_and_open_stream`) until
        the command exits, invoking a callback per chunk as it arrives
        rather than buffering the whole stream itself — the caller
        decides what buffering/capping/persistence looks like (S15's
        in-memory buffer, S16's capped on-disk log). Call via
        `asyncio.to_thread`; has no internal deadline of its own, same as
        `consume`."""
        for out_chunk, err_chunk in stream:
            if out_chunk:
                on_stdout(out_chunk)
            if err_chunk:
                on_stderr(err_chunk)
        exit_code = self._docker.api.exec_inspect(exec_id).get("ExitCode")
        return exit_code if exit_code is not None else -1

    def pid_of(self, exec_id: str) -> int | None:
        """The exec'd process's pid in the *host* PID namespace (see the
        module docstring), or `None` if the exec is unknown, or genuinely
        never reports one even after retrying.

        RR-022: a single `exec_inspect` right after `exec_start` reports
        `Pid: 0` often enough to matter — reproduced empirically, 6 of 30
        (20%) trials, even once the exec's own stream was already
        producing output. Retrying up to `_PID_RETRY_ATTEMPTS` times,
        `_PID_RETRY_INTERVAL_SECONDS` apart, closed the window entirely
        in the same empirical setup: 0 of 30 trials still unreported. A
        `None` that survives every retry is logged here and should be
        treated by callers as a real absence (see `mark_running`'s
        docstring), not as "ask again later" — there is no later update
        that would."""
        for attempt in range(_PID_RETRY_ATTEMPTS + 1):
            try:
                info = self._docker.api.exec_inspect(exec_id)
            except docker.errors.DockerException:
                return None
            pid = info.get("Pid")
            if pid:
                return int(pid)
            if attempt < _PID_RETRY_ATTEMPTS:
                time.sleep(_PID_RETRY_INTERVAL_SECONDS)
        _logger.warning(
            "exec %s: no pid reported after %d retries; proceeding without one",
            exec_id,
            _PID_RETRY_ATTEMPTS,
        )
        return None

    def kill(self, exec_id: str, *, grace_period_reached: bool = False) -> None:
        """Signals the exec'd process's *process group* directly via
        `os.killpg`: TERM, or KILL once `grace_period_reached`. See the
        module docstring for why this is not a second `docker exec`.

        HR-020 (`docs/HARDENING_SPEC.md` §9): a plain `os.kill(pid, sig)`
        only ever reached the direct exec'd process, not any children it
        spawned (e.g. `sh -c 'cmd1 & cmd2'`, or any command that forks) —
        those survived. Confirmed empirically that each `docker exec`
        starts as the leader of its own, fresh process group (`PGID ==
        PID`), never sharing the container's own init process group, so
        `killpg` here cannot reach anything outside this one exec's own
        process tree.

        `PermissionError` (the daemon's own uid lacks permission to
        signal this pid) is deliberately allowed to propagate — HR-020
        requires a failed signal be reported as a structured error, not
        silently swallowed; only `ProcessLookupError` (the process — or
        its whole group — is already gone) is treated as a benign no-op.

        RR-013 (`docs/REMEDIATION_SPEC.md` §10): `exec_inspect` itself
        failing is deliberately allowed to propagate too, for the same
        reason — reproduced empirically that this used to swallow a real
        `docker.errors.DockerException` (e.g. the container was removed
        out from under a still-running exec) and return as if the signal
        had been sent successfully. An inspect *failure* means the
        daemon genuinely does not know whether the process is still
        there — that is not the same thing as `ProcessLookupError`'s "we
        checked, and it's gone," and reporting it identically is exactly
        the silent-success HR-020 exists to close.
        """
        info = self._docker.api.exec_inspect(exec_id)
        pid = info.get("Pid")
        if not pid or not info.get("Running", False):
            return

        sig = signal.SIGKILL if grace_period_reached else signal.SIGTERM
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, sig)


def tail_text(data: bytes, max_lines: int) -> str:
    if max_lines <= 0:
        return ""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    return "".join(lines[-max_lines:])
