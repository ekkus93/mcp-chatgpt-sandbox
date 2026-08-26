"""Per-stream, on-disk job log capture (§15.6) — S16.

§15.5 bounds what a *response* carries; this bounds what is *kept*.
`StreamCapture` writes one job's one stream to its host-side log file as
chunks arrive, capping what actually lands on disk at
`max_output_bytes` while still counting every byte produced (so
`stdout_total_bytes` stays true even once the file itself has stopped
growing): once the cap is reached, writes go into a `tail_keep_bytes`
ring buffer instead, and that ring buffer — the *end* of the stream,
where a build failure usually is — is appended to the file, behind a
`--- <N> bytes omitted ---` marker, when the job finishes.

Every write is flushed immediately: a job's log is meant to be
"retrievable incrementally" (§15.5) while still running, from a
different thread/coroutine than the one writing it (`exec_output`, in
the async tool layer, reads the file `AsyncJobRunner`'s background
thread is appending to) — an unflushed write would make a concurrent
reader see a shorter file than what has actually been produced.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO


class StreamCapture:
    def __init__(self, path: Path, *, max_output_bytes: int, tail_keep_bytes: int) -> None:
        self._path = path
        self._max_output_bytes = max_output_bytes
        self._tail_keep_bytes = tail_keep_bytes
        self._total_bytes = 0
        self._written_bytes = 0
        self._tail_ring = bytearray()
        self._file: IO[bytes] | None = None

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def truncated(self) -> bool:
        """True once more bytes have been produced than the cap allows —
        mirrors §15.6's `output_truncated`, not §15.5's response-level
        truncation (a separate, always-stricter bound applied later, at
        the tool layer, to whatever this file holds)."""
        return self._total_bytes > self._max_output_bytes

    def write(self, chunk: bytes) -> None:
        self._total_bytes += len(chunk)
        remaining_capacity = self._max_output_bytes - self._written_bytes
        if remaining_capacity > 0:
            head, overflow = chunk[:remaining_capacity], chunk[remaining_capacity:]
            file = self._ensure_open()
            file.write(head)
            file.flush()
            self._written_bytes += len(head)
        else:
            overflow = chunk
        if overflow:
            self._tail_ring.extend(overflow)
            excess = len(self._tail_ring) - self._tail_keep_bytes
            if excess > 0:
                del self._tail_ring[:excess]

    def finish(self) -> None:
        """Call exactly once, when the job ends: appends the omission
        marker and the kept tail if the stream was ever capped, then
        closes the file. A stream that never wrote anything (a silent
        command) never opens a file at all."""
        if self.truncated:
            omitted = self._total_bytes - self._written_bytes - len(self._tail_ring)
            file = self._ensure_open()
            if omitted > 0:
                file.write(f"\n--- {omitted} bytes omitted ---\n".encode())
            file.write(bytes(self._tail_ring))
            file.flush()
        if self._file is not None:
            self._file.close()

    def _ensure_open(self) -> IO[bytes]:
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("wb")
        return self._file


def read_log_bytes(path: str | None) -> bytes:
    """The log file's current full content — empty if the job never
    produced output, its log was pruned (§15.6's retention), or no path
    is recorded at all. Used both for `exec_status`'s tail and
    `exec_output`'s ranged reads; capped at `max_output_bytes` +
    `tail_keep_bytes` on disk, so reading the whole file is bounded."""
    if path is None:
        return b""
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return b""
