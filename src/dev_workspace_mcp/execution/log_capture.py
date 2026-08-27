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

import os
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


def stat_log_bytes(path: str | None) -> int:
    """The log file's current on-disk size — empty/missing is `0`. H29
    (`docs/HARDENING_TODO.md`, HD3): the actual *retrievable* byte
    count, which `exec_output`'s `byte_start`/`byte_end` index into —
    distinct from a job's `stdout_total_bytes`/`stream_total_bytes`
    (§15.6's uncapped production total), the two having been silently
    conflated before. `os.stat` costs nothing close to a read even for
    a multi-megabyte file."""
    if path is None:
        return 0
    try:
        return os.stat(path).st_size
    except FileNotFoundError:
        return 0


def read_log_bytes(path: str | None, *, offset: int = 0, max_bytes: int | None = None) -> bytes:
    """The log file's content in `[offset, offset + max_bytes)` —  the
    whole file when both are omitted (`exec_status`'s use before H29's
    `read_log_tail` below existed, kept for any other whole-file
    caller). H29: seeks rather than slurping — reads only the requested
    window from disk, not the whole (possibly multi-megabyte, capped at
    `max_output_bytes + tail_keep_bytes`) on-disk capture just to slice
    it in Python afterward. Empty if the job never produced output, its
    log was pruned (§15.6's retention), or no path is recorded at all."""
    if path is None:
        return b""
    try:
        with open(path, "rb") as f:
            if offset:
                f.seek(offset)
            return f.read() if max_bytes is None else f.read(max_bytes)
    except FileNotFoundError:
        return b""


#: `read_log_tail`'s initial backward-read window — large enough to hold
#: `response_tail_lines`' worth of realistic log lines in the common
#: case (avoiding a second, doubled read), small relative to a
#: multi-megabyte capture (the whole point of reading backward in
#: growing chunks instead of the whole file).
_TAIL_INITIAL_CHUNK_BYTES = 64 * 1024


def read_log_tail(path: str | None, *, max_lines: int) -> bytes:
    """The last `max_lines` lines' worth of raw bytes — H29: read
    backward from disk in growing chunks, not by reading the whole file
    just to keep its final N lines in Python (`exec_status`'s previous
    behavior, on every poll of a still-running job). Correctness does
    not depend on `data`'s first reconstructed "line" being a genuine
    line start: a chunk read from an arbitrary offset can begin
    mid-line (or mid multi-byte UTF-8 character), but the caller
    (`execution/service.py`'s `tail_text`) only ever keeps the *last*
    `max_lines` lines after splitting — a possibly-garbled leading
    fragment is exactly what gets discarded, as long as at least
    `max_lines` real newlines were found after it, which the count
    check below guarantees before returning."""
    if path is None or max_lines <= 0:
        return b""
    size = stat_log_bytes(path)
    if size == 0:
        return b""

    chunk_size = _TAIL_INITIAL_CHUNK_BYTES
    try:
        with open(path, "rb") as f:
            while True:
                read_from = max(0, size - chunk_size)
                f.seek(read_from)
                data = f.read()
                if data.count(b"\n") >= max_lines or read_from == 0:
                    return data
                chunk_size *= 2
    except FileNotFoundError:
        return b""
