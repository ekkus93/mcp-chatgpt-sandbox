"""JPO-003 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §5.1; JPOV-002):
`PromotableSink` and `StreamCapture`'s deferred mode — invariants I3 (no
lost output, truthful totals), I7 (bounded memory) and I8 (one lock over
write and switch)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from mcp_chatgpt_sandbox.execution.log_capture import StreamCapture
from mcp_chatgpt_sandbox.execution.promotable_sink import PromotableSink
from mcp_chatgpt_sandbox.execution.service import ExecutionService


def _sink(*, cap: int = 1024, tail: int = 64) -> PromotableSink:
    return PromotableSink(max_output_bytes=cap, tail_keep_bytes=tail)


def _promote(sink: PromotableSink, tmp_path: Path) -> tuple[Path, Path]:
    out, err = tmp_path / "job" / "stdout.log", tmp_path / "job" / "stderr.log"
    sink.promote(stdout_log_path=out, stderr_log_path=err)
    return out, err


def test_jpov002_output_before_and_after_promotion_is_in_the_log_once_and_in_order(
    tmp_path: Path,
) -> None:
    sink = _sink()
    sink.on_stdout(b"before-1\n")
    sink.on_stderr(b"err-before\n")
    sink.on_stdout(b"before-2\n")
    out, err = _promote(sink, tmp_path)
    sink.on_stdout(b"after\n")
    sink.on_stderr(b"err-after\n")
    sink.stdout_capture.finish()
    sink.stderr_capture.finish()

    assert out.read_bytes() == b"before-1\nbefore-2\nafter\n"
    assert err.read_bytes() == b"err-before\nerr-after\n"
    assert sink.stdout_capture.total_bytes == len(b"before-1\nbefore-2\nafter\n")


def test_jpov002_the_log_is_readable_the_moment_promotion_returns(tmp_path: Path) -> None:
    """§15.5: a job's log is retrievable incrementally — `exec_output`
    may be called on the promoted job before it writes anything more."""
    sink = _sink()
    sink.on_stdout(b"already produced\n")
    out, _err = _promote(sink, tmp_path)

    assert out.read_bytes() == b"already produced\n"


def test_jpov002_bytes_dropped_at_the_cap_before_promotion_still_count(tmp_path: Path) -> None:
    sink = _sink(cap=10, tail=4)
    sink.on_stdout(b"0123456789")  # fills the cap exactly
    sink.on_stdout(b"abcdefghij")  # all over the cap: 6 dropped, 4 kept as tail
    out, _err = _promote(sink, tmp_path)
    sink.on_stdout(b"WXYZ")  # pushes `ghij` out of the 4-byte tail ring
    sink.stdout_capture.finish()

    assert sink.stdout_capture.total_bytes == 24
    assert sink.stdout_capture.truncated
    assert out.read_bytes() == b"0123456789\n--- 10 bytes omitted ---\nWXYZ"


def test_jpov002_the_memory_held_is_bounded_and_released_on_promotion(tmp_path: Path) -> None:
    sink = _sink(cap=100, tail=16)
    for _ in range(50):
        sink.on_stdout(b"x" * 37)

    assert len(sink.stdout_capture.buffered_head()) == 100
    _promote(sink, tmp_path)
    assert sink.stdout_capture.buffered_head() == b""


def test_jpov002_a_sink_that_is_never_promoted_creates_no_file(tmp_path: Path) -> None:
    sink = _sink()
    sink.on_stdout(b"quick command\n")
    sink.stdout_capture.finish()
    sink.stderr_capture.finish()

    assert list(tmp_path.iterdir()) == []


def test_jpov002_a_capture_cannot_be_attached_twice(tmp_path: Path) -> None:
    capture = StreamCapture(None, max_output_bytes=10, tail_keep_bytes=4)
    capture.attach(tmp_path / "a.log")
    with pytest.raises(RuntimeError, match="already attached"):
        capture.attach(tmp_path / "b.log")


def test_jpov002_a_chunk_arriving_during_promotion_waits_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I8, made deterministic rather than left to a stress loop: a writer
    is started from *inside* the switch. With the lock it cannot proceed
    until `promote` returns; without it (proved by deleting the `with
    self._lock` in `on_stdout`) it completes mid-switch, which is the
    window in which a chunk lands in a buffer that has already been
    flushed and is about to be thrown away."""
    sink = _sink()
    sink.on_stdout(b"first\n")
    wrote = threading.Event()
    observed: dict[str, bool] = {}
    real_attach = sink.stdout_capture.attach

    def writer() -> None:
        sink.on_stdout(b"second\n")
        wrote.set()

    thread = threading.Thread(target=writer)

    def attach_with_a_concurrent_writer(path: Path) -> None:
        thread.start()
        observed["wrote_during_switch"] = wrote.wait(timeout=0.5)
        real_attach(path)

    monkeypatch.setattr(sink.stdout_capture, "attach", attach_with_a_concurrent_writer)
    out, _err = _promote(sink, tmp_path)
    thread.join(timeout=5)

    assert observed == {"wrote_during_switch": False}
    assert wrote.is_set()
    assert out.read_bytes() == b"first\nsecond\n"


class _FakeDockerApi:
    def __init__(self, chunks: list[tuple[bytes | None, bytes | None]], exit_code: int) -> None:
        self._chunks = chunks
        self._exit_code = exit_code

    def exec_start(self, exec_id: str, **_kwargs: Any) -> list[tuple[bytes | None, bytes | None]]:
        return self._chunks

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return {"ExitCode": self._exit_code}


class _FakeDockerClient:
    def __init__(self, api: _FakeDockerApi) -> None:
        self.api = api


@pytest.mark.parametrize("cap", [1_000_000, 25])
def test_jpov002_result_matches_what_consume_returns_for_the_same_stream(cap: int) -> None:
    """I1's other half: a caller who opts in and whose command finishes in
    time gets the same answer the default path would have given —
    including when the output overran the cap."""
    chunks: list[tuple[bytes | None, bytes | None]] = [
        (b"line 1\nline 2\n", None),
        (None, b"warn\n"),
        (b"line 3\nline 4 is long enough to cross a small cap\n", b"warn 2\n"),
    ]
    service = ExecutionService(_FakeDockerClient(_FakeDockerApi(chunks, 3)))  # type: ignore[arg-type]
    expected = service.consume("exec", max_output_bytes=cap, response_tail_lines=2)

    sink = PromotableSink(max_output_bytes=cap, tail_keep_bytes=8)
    exit_code = service.drain_stream(
        "exec",
        chunks,  # type: ignore[arg-type]
        on_stdout=sink.on_stdout,
        on_stderr=sink.on_stderr,
    )

    assert sink.result(exit_code, response_tail_lines=2) == expected
