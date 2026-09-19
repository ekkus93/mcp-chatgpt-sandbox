"""PJPO2-001 (`docs/POST_PJPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1, H1;
PJPO2V-001): `PromotableSink.promote` switches both streams or neither,
under a *real* I/O failure on either one.

`/dev/full` is Linux-only: `open` succeeds, every `write` fails with
ENOSPC. That matches the suite's existing requirements (Docker, a
self-hosted Linux CI runner), and these tests are deliberately not guarded
with a skip — `tests/_skip_policy.py` turns any skip into a session
failure, so a skip would only move the failure somewhere less clear.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO

import pytest

from mcp_chatgpt_sandbox.execution.log_capture import StreamCapture
from mcp_chatgpt_sandbox.execution.promotable_sink import PromotableSink

_DISK_FULL = Path("/dev/full")


def _sink_with_output() -> PromotableSink:
    sink = PromotableSink(max_output_bytes=1024, tail_keep_bytes=64)
    sink.on_stdout(b"out-1\n")
    sink.on_stderr(b"err-1\n")
    return sink


def _open_descriptors_to(path: Path) -> list[str]:
    """This process's file descriptors currently open on `path` — how a
    test observes "no file object was left open" without reaching into
    the capture's private attributes."""
    target = str(path.resolve())
    fd_dir = Path("/proc/self/fd")
    found = []
    for fd in os.listdir(fd_dir):
        try:
            if os.readlink(fd_dir / fd) == target:
                found.append(fd)
        except OSError:
            continue  # closed between listdir and readlink
    return found


def test_pjpo2v001_a_stderr_failure_leaves_both_streams_buffered_and_promote_retryable(
    tmp_path: Path,
) -> None:
    sink = _sink_with_output()
    first_stdout = tmp_path / "attempt-1" / "stdout.log"

    with pytest.raises(OSError, match="No space left on device"):
        sink.promote(stdout_log_path=first_stdout, stderr_log_path=_DISK_FULL)

    # Neither stream switched: both still hold their buffered output ...
    assert sink.stdout_capture.buffered_head() == b"out-1\n"
    assert sink.stderr_capture.buffered_head() == b"err-1\n"
    # ... the stdout file prepared for the failed attempt is not held open ...
    # (PJPO3-002: this shows no descriptor survives, but not *why* — with
    # the explicit close in `promote` removed it still passes, because
    # refcounting closes the orphaned file object as the call unwinds.
    # `test_pjpo3v002_…` below is what pins the close itself.)
    assert _open_descriptors_to(first_stdout) == []
    # ... and later output still goes to the buffers, not to that file.
    sink.on_stdout(b"out-2\n")
    sink.on_stderr(b"err-2\n")
    assert first_stdout.read_bytes() == b"out-1\n"

    # The switch can be made again, and nothing produced so far is lost.
    out, err = tmp_path / "attempt-2" / "stdout.log", tmp_path / "attempt-2" / "stderr.log"
    sink.promote(stdout_log_path=out, stderr_log_path=err)
    sink.on_stdout(b"out-3\n")
    sink.on_stderr(b"err-3\n")
    sink.stdout_capture.finish()
    sink.stderr_capture.finish()

    assert out.read_bytes() == b"out-1\nout-2\nout-3\n"
    assert err.read_bytes() == b"err-1\nerr-2\nerr-3\n"


def test_pjpo2v001_a_stdout_failure_never_touches_stderr(tmp_path: Path) -> None:
    """The mirror case. It held before PJPO2-001 too (stdout is prepared
    first and its failure raises before stderr is reached); pinned so a
    reordering cannot quietly break it."""
    sink = _sink_with_output()
    err = tmp_path / "job" / "stderr.log"

    with pytest.raises(OSError, match="No space left on device"):
        sink.promote(stdout_log_path=_DISK_FULL, stderr_log_path=err)

    assert not err.exists()
    assert sink.stdout_capture.buffered_head() == b"out-1\n"
    assert sink.stderr_capture.buffered_head() == b"err-1\n"


def test_pjpo3v001_a_second_commit_attach_is_refused(tmp_path: Path) -> None:
    """PJPO3-001 (`docs/POST_PJPO2_CODE_REVIEW_SPEC_2026-09-18.md` §2.1,
    K1): unguarded, the second commit silently moved the capture to
    `second` and left the file on `first` open."""
    capture = StreamCapture(None, max_output_bytes=1024, tail_keep_bytes=64)
    capture.write(b"x")
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    capture.commit_attach(first, capture.prepare_attach(first))

    with pytest.raises(RuntimeError, match="already attached"):
        capture.commit_attach(second, None)

    capture.write(b"y")
    capture.finish()
    assert first.read_bytes() == b"xy"
    assert not second.exists()


def test_pjpo3v002_promote_closes_the_prepared_stdout_file_when_stderr_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PJPO3-002 (`docs/POST_PJPO2_CODE_REVIEW_SPEC_2026-09-18.md` §2.2).
    The test keeps its own reference to the file object `prepare_attach`
    returned for stdout, so reference counting cannot close it on the way
    out: only the explicit close in `promote` can. `prepare_attach` is
    wrapped, not replaced — the file is the real one, and stderr's failure
    is a real ENOSPC."""
    sink = _sink_with_output()
    prepared: list[IO[bytes] | None] = []
    real_prepare = sink.stdout_capture.prepare_attach

    def keep_a_reference(path: Path) -> IO[bytes] | None:
        result = real_prepare(path)
        prepared.append(result)
        return result

    monkeypatch.setattr(sink.stdout_capture, "prepare_attach", keep_a_reference)

    with pytest.raises(OSError, match="No space left on device"):
        sink.promote(stdout_log_path=tmp_path / "job" / "stdout.log", stderr_log_path=_DISK_FULL)

    (stdout_file,) = prepared
    assert stdout_file is not None
    assert stdout_file.closed
