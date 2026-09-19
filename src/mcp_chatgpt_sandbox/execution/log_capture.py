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

JPO-003 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.1): a capture may be
created *deferred* — `path=None` — and given its file later with
`attach`. Until then the head it would have written to disk is held in
memory, under the very same `max_output_bytes` cap, and everything else
(the running total, the kept tail, `truncated`) works identically. This
exists for §14.4's promotion of a synchronous `exec` to a job: the
output produced before promotion has to become the front of the job's
log. Why here rather than a separate in-memory buffer that is later
copied into a fresh `StreamCapture`: a fresh capture would start
counting at zero, so bytes the buffer had already dropped at its cap
would vanish from `total_bytes` and from the omission marker — the two
numbers §15.6 promises stay true. One object doing one continuous piece
of accounting across the switch has no such seam to get wrong.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO


class StreamCapture:
    def __init__(self, path: Path | None, *, max_output_bytes: int, tail_keep_bytes: int) -> None:
        self._path = path
        self._max_output_bytes = max_output_bytes
        self._tail_keep_bytes = tail_keep_bytes
        self._total_bytes = 0
        self._written_bytes = 0
        self._tail_ring = bytearray()
        self._file: IO[bytes] | None = None
        self._deferred_head = bytearray()

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
            if self._path is None:
                self._deferred_head.extend(head)
            else:
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

    def attach(self, path: Path) -> None:
        """Gives a deferred capture its log file: writes the head held so
        far, releases that memory, and sends every later write to disk.
        Not thread-safe by itself — the caller serialises it against
        `write` (`PromotableSink` holds one lock over both).

        PJPO-001 (`docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1,
        G2): all or nothing. On any failure the capture is left exactly
        as it was — still deferred, head still in memory, no file object
        — and the error is re-raised. The first version assigned `_path`
        before the head reached the disk, so a real ENOSPC left a capture
        with a path, an open file and unflushed bytes in that file's
        buffer: a state no other code expects, in which `finish()` raised
        the same ENOSPC again while closing, and later `write`s from the
        drain thread raised too.

        PJPO2-001 (`docs/POST_PJPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1):
        now `prepare_attach` followed by `commit_attach`, so that a caller
        switching *two* captures (`PromotableSink.promote`) can do all the
        I/O for both before changing either."""
        self.commit_attach(path, self.prepare_attach(path))

    def prepare_attach(self, path: Path) -> IO[bytes] | None:
        """Every fallible step of `attach`, and nothing else: creates the
        directory, opens `path` and writes and flushes the deferred head
        into it, returning that file object *without* adopting it — or
        `None` if nothing is buffered, in which case no file is created
        until the first later `write` (a silent stream never gets a log).
        On failure the partial file object is closed, with the repeated
        error from closing it suppressed (it is the error already being
        raised, and closing still releases the descriptor), and the
        capture is untouched.

        No `write` may reach this capture between this call and
        `commit_attach`: the head is released at commit, so a chunk added
        to it in between would be lost. `PromotableSink` guarantees that
        by holding its lock across both."""
        if self._path is not None:
            raise RuntimeError(f"capture is already attached to {self._path}")
        if not self._deferred_head:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("wb")
        try:
            file.write(bytes(self._deferred_head))
            file.flush()
        except BaseException:
            with contextlib.suppress(OSError):
                file.close()
            raise
        return file

    def commit_attach(self, path: Path, prepared: IO[bytes] | None) -> None:
        """Adopts what `prepare_attach(path)` returned. Performs no I/O
        and cannot fail on a deferred capture, which is the whole point:
        once every capture in a switch has been prepared, committing them
        all is certain.

        PJPO3-001 (`docs/POST_PJPO2_CODE_REVIEW_SPEC_2026-09-18.md` §2.1,
        K1): refuses a capture that is already attached, before changing
        anything — the same refusal `prepare_attach` makes. Unguarded, a
        second commit was accepted silently: the capture moved to the
        second path and the first file object was never closed, its
        descriptor leaked for the life of the process. Neither caller
        (`attach`, `PromotableSink.promote`) does that, but both methods
        are public (RD1-A) and the pairing was enforced by a docstring
        alone.

        What this deliberately does not catch: a file prepared for one
        path committed under another. Ruling that out would take a token
        object tying the two calls together — more machinery than two
        correct callers justify (spec §3, §8)."""
        if self._path is not None:
            raise RuntimeError(f"capture is already attached to {self._path}")
        self._file = prepared
        self._path = path
        self._deferred_head = bytearray()

    def buffered_head(self) -> bytes:
        """What a still-deferred capture holds in memory — at most
        `max_output_bytes`, the same leading bytes `consume` keeps."""
        return bytes(self._deferred_head)

    def finish(self) -> None:
        """Call exactly once, when the job ends: appends the omission
        marker and the kept tail if the stream was ever capped, then
        closes the file. A stream that never wrote anything (a silent
        command) never opens a file at all."""
        if self._path is None:
            return
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
        if self._path is None:
            raise RuntimeError("a deferred capture has no file until attach()")
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
    check below guarantees before returning.

    RR-019 (`docs/REMEDIATION_SPEC.md` §14): `>`, not `>=`. The leading
    fragment's own terminating newline is itself one of the newlines
    `count()` sees — it is not one of the `max_lines` *complete* lines
    that follow the fragment, and `>=` counted it as if it were.
    Reproduced empirically: a chunk containing exactly `max_lines`
    newlines has only `max_lines - 1` complete lines after the fragment,
    so `tail_text`'s `lines[-max_lines:]` pulls the fragment itself in
    to fill the last slot, presenting it as a genuine line rather than
    discarding it. `>` requires one more newline than that — the
    fragment's own, plus a genuine one for each of the `max_lines` lines
    that must follow it."""
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
                if data.count(b"\n") > max_lines or read_from == 0:
                    return data
                chunk_size *= 2
    except FileNotFoundError:
        return b""
