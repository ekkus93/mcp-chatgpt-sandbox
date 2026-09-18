"""A `drain_stream` destination that can turn into a job's logs mid-stream
(JPO-003, `docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.1).

§14.4 lets a synchronous `exec` that outlives the synchronous threshold
become a background job. The command is *not* restarted (I2), so the
output it produced while still "synchronous" has to end up at the front
of the job's log (I3). `ExecutionService.consume` cannot give that up: it
opens the exec stream itself and keeps what it has read in locals of a
blocked worker thread. `drain_stream`, the job path's reader, delivers
chunks through callbacks instead — so the stream never has to change
hands at all. Only where the callbacks put the bytes has to change, and
this class is that one switch.

Why not the obvious alternative — write to the job's log files from the
first byte, for every opted-in call: almost every such call finishes in
time and is never a job. Each would create a job directory and two log
files that belong to no `JobRecord`, which retention (keyed on job
records) would never find to prune.

Both streams are `StreamCapture`s in their deferred mode from the start,
rather than a separate in-memory buffer that is later copied into one:
the byte totals, the cap and the kept tail are then a single piece of
accounting across the switch, so `stdout_total_bytes` stays true and an
over-cap stream gets the usual omission marker without any hand-carried
counters.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from mcp_chatgpt_sandbox.execution.log_capture import StreamCapture
from mcp_chatgpt_sandbox.execution.service import ExecResult, tail_text


class PromotableSink:
    def __init__(self, *, max_output_bytes: int, tail_keep_bytes: int) -> None:
        self.stdout_capture = StreamCapture(
            None, max_output_bytes=max_output_bytes, tail_keep_bytes=tail_keep_bytes
        )
        self.stderr_capture = StreamCapture(
            None, max_output_bytes=max_output_bytes, tail_keep_bytes=tail_keep_bytes
        )
        #: I8: `on_stdout`/`on_stderr` run on `drain_stream`'s worker
        #: thread while `promote` runs on the event loop's. Without this
        #: a chunk arriving mid-`attach` could be appended to the memory
        #: buffer after that buffer had already been flushed to the file
        #: — and then released, losing the chunk outright.
        self._lock = threading.Lock()

    def on_stdout(self, chunk: bytes) -> None:
        with self._lock:
            self.stdout_capture.write(chunk)

    def on_stderr(self, chunk: bytes) -> None:
        with self._lock:
            self.stderr_capture.write(chunk)

    def promote(self, *, stdout_log_path: Path, stderr_log_path: Path) -> None:
        """Flushes everything buffered so far to the job's log files and
        routes every later chunk straight to them. Both streams switch
        under one lock acquisition, and both or neither: there is no
        moment — on success or on failure — at which one stream is a job
        log and the other still a buffer. If this raises, both captures
        are still deferred with their buffers intact, and `promote` can
        be called again.

        PJPO2-001 (`docs/POST_PJPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1,
        H1): the first version called `attach` on each capture in turn.
        Each was all-or-nothing, but the pair was not: with stderr's log
        on a full disk, stdout was left attached and stderr still a
        buffer — the state this docstring already said could not exist —
        and a second `promote` raised "already attached". Hence prepare
        both (all the I/O), then commit both (none of it).

        A log file already written for the stream whose sibling then
        failed is closed and left on disk rather than unlinked. It is at
        the log path of a job whose record the caller fails closed
        (`_fail_half_created_job`), so log retention
        (`jobs/retention.py`) prunes it as that terminal job's log, like
        any other; deleting it here would be a second failure path inside
        the handling of the first, for no gain.
        """
        with self._lock:
            prepared_stdout = self.stdout_capture.prepare_attach(stdout_log_path)
            try:
                prepared_stderr = self.stderr_capture.prepare_attach(stderr_log_path)
            except BaseException:
                if prepared_stdout is not None:
                    with contextlib.suppress(OSError):
                        prepared_stdout.close()
                raise
            self.stdout_capture.commit_attach(stdout_log_path, prepared_stdout)
            self.stderr_capture.commit_attach(stderr_log_path, prepared_stderr)

    def result(self, exit_code: int, *, response_tail_lines: int) -> ExecResult:
        """The same `ExecResult` `consume` builds for the same bytes —
        call only once the drain has returned, for a command that
        finished in time."""
        with self._lock:
            return ExecResult(
                exit_code=exit_code,
                stdout_tail=tail_text(self.stdout_capture.buffered_head(), response_tail_lines),
                stderr_tail=tail_text(self.stderr_capture.buffered_head(), response_tail_lines),
                stdout_truncated=self.stdout_capture.truncated,
                stderr_truncated=self.stderr_capture.truncated,
                stdout_total_bytes=self.stdout_capture.total_bytes,
                stderr_total_bytes=self.stderr_capture.total_bytes,
            )
