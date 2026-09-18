"""PJPO-001 (`docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md` §2.1;
PJPOV-001, PJPOV-002): a *real* I/O failure while promotion switches output
to the job's log files.

`/dev/full` accepts `open` and fails every `write` with ENOSPC — the
disk-full case, without needing a full disk. The earlier tool-level test
monkeypatched `StreamCapture.attach` to raise before it touched anything,
which is precisely why it could not see the defect: the defect *was* what
`attach` had already done by the time it failed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_chatgpt_sandbox.execution.log_capture import StreamCapture
from mcp_chatgpt_sandbox.execution.promotable_sink import PromotableSink
from mcp_chatgpt_sandbox.jobs.store import JobState
from mcp_chatgpt_sandbox.tools.execution_promotion import _fail_half_created_job

_DISK_FULL = Path("/dev/full")


def test_pjpov001_a_failed_attach_leaves_the_capture_deferred_and_intact() -> None:
    capture = StreamCapture(None, max_output_bytes=1024, tail_keep_bytes=64)
    capture.write(b"produced before promotion\n")

    with pytest.raises(OSError, match="No space left on device"):
        capture.attach(_DISK_FULL)

    # G2: still deferred — not half-attached.
    assert capture._path is None
    assert capture._file is None
    assert capture.buffered_head() == b"produced before promotion\n"
    capture.write(b"and after\n")  # a later drain-thread write does not raise
    capture.finish()  # nor does finishing it
    assert capture.total_bytes == len(b"produced before promotion\nand after\n")


def test_pjpov001_a_capture_can_still_be_attached_after_a_failed_attempt(
    tmp_path: Path,
) -> None:
    capture = StreamCapture(None, max_output_bytes=1024, tail_keep_bytes=64)
    capture.write(b"kept\n")
    with pytest.raises(OSError):
        capture.attach(_DISK_FULL)

    capture.attach(tmp_path / "stdout.log")
    capture.finish()

    assert (tmp_path / "stdout.log").read_bytes() == b"kept\n"


class _RecordingJobStore:
    def __init__(self) -> None:
        self.finished: dict[str, Any] | None = None

    def get(self, job_id: str) -> Any:
        return SimpleNamespace(state=JobState.QUEUED)  # created, not yet adopted

    def mark_finished(self, job_id: str, **fields: Any) -> None:
        self.finished = fields


def test_pjpov002_a_disk_full_promotion_still_makes_the_job_record_terminal() -> None:
    """The spec §2.1 reproduction, as a test."""
    sink = PromotableSink(max_output_bytes=1024, tail_keep_bytes=64)
    sink.on_stdout(b"produced before promotion\n")
    with pytest.raises(OSError):
        sink.promote(stdout_log_path=_DISK_FULL, stderr_log_path=Path("/dev/null"))
    store = _RecordingJobStore()

    _fail_half_created_job(store, "job_x", sink)  # type: ignore[arg-type]

    assert store.finished is not None
    assert store.finished["state"] is JobState.FAILED
    assert store.finished["stdout_total_bytes"] == len(b"produced before promotion\n")


def test_pjpov002_a_capture_that_fails_to_close_does_not_stop_the_record_being_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1 independently of G2: even if a capture *is* in a state whose
    `finish()` raises, the record is made terminal and the other capture
    is still finished."""
    sink = PromotableSink(max_output_bytes=1024, tail_keep_bytes=64)
    finished: list[str] = []

    def broken_finish() -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sink.stdout_capture, "finish", broken_finish)
    monkeypatch.setattr(sink.stderr_capture, "finish", lambda: finished.append("stderr"))
    store = _RecordingJobStore()

    _fail_half_created_job(store, "job_x", sink)  # type: ignore[arg-type]

    assert store.finished is not None
    assert finished == ["stderr"]


def _deferred_capture_holding(data: bytes) -> StreamCapture:
    capture = StreamCapture(None, max_output_bytes=1024, tail_keep_bytes=64)
    capture.write(data)
    return capture


def _assert_still_deferred_and_usable(capture: StreamCapture, good_path: Path) -> None:
    """What "still deferred, head intact" means, observed from outside:
    the buffered output is all there, a later write neither raises nor
    creates anything, `finish()` is harmless, and the capture can still
    be attached — with every byte, in order."""
    assert capture.buffered_head() == b"before\n"
    capture.write(b"after\n")
    assert capture.buffered_head() == b"before\nafter\n"
    capture.attach(good_path)
    capture.finish()
    assert good_path.read_bytes() == b"before\nafter\n"


def test_pjpo2v002_attach_is_all_or_nothing_when_the_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    """PJPO2-002: PJPO-007's evidence credited PJPOV-001 with this case;
    PJPOV-001 only exercises a failing `write`."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    capture = _deferred_capture_holding(b"before\n")

    with pytest.raises(NotADirectoryError):
        capture.attach(blocker / "job" / "stdout.log")

    _assert_still_deferred_and_usable(capture, tmp_path / "good" / "stdout.log")


def test_pjpo2v002_attach_is_all_or_nothing_when_the_file_cannot_be_opened(
    tmp_path: Path,
) -> None:
    target = tmp_path / "stdout.log"
    target.mkdir()  # a directory where the log file should be
    capture = _deferred_capture_holding(b"before\n")

    with pytest.raises(IsADirectoryError):
        capture.attach(target)

    _assert_still_deferred_and_usable(capture, tmp_path / "good" / "stdout.log")


def test_pjpo2v002_attaching_with_nothing_buffered_creates_no_file_until_output_arrives(
    tmp_path: Path,
) -> None:
    capture = StreamCapture(None, max_output_bytes=1024, tail_keep_bytes=64)
    log = tmp_path / "job" / "stderr.log"

    capture.attach(log)
    assert not log.exists()  # a silent stream never gets a log

    capture.write(b"first output\n")
    capture.finish()
    assert log.read_bytes() == b"first output\n"
