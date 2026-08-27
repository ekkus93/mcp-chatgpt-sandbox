"""S16: on-disk job log capture (§15.6)."""

import threading
import time
from pathlib import Path

from dev_workspace_mcp.execution.log_capture import (
    StreamCapture,
    read_log_bytes,
    read_log_tail,
    stat_log_bytes,
)


def test_write_below_cap_is_not_truncated(tmp_path: Path) -> None:
    capture = StreamCapture(tmp_path / "out.log", max_output_bytes=100, tail_keep_bytes=20)
    capture.write(b"hello")
    assert capture.total_bytes == 5
    assert capture.truncated is False
    capture.finish()
    assert (tmp_path / "out.log").read_bytes() == b"hello"


def test_write_never_opens_a_file_when_nothing_is_written(tmp_path: Path) -> None:
    capture = StreamCapture(tmp_path / "out.log", max_output_bytes=100, tail_keep_bytes=20)
    capture.finish()
    assert not (tmp_path / "out.log").exists()


def test_write_is_flushed_immediately_for_a_same_thread_reread(tmp_path: Path) -> None:
    """A `write()` that returns without a `flush()` would leave this
    unread from Python's own buffer, not just from another thread — this
    is the narrower, same-thread half of the property; H28's
    `test_a_concurrent_reader_thread_never_observes_a_torn_or_stale_write`
    below is the genuinely multi-threaded half."""
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=100, tail_keep_bytes=20)
    capture.write(b"partial")
    # No finish() yet — a concurrent reader must already see this chunk.
    assert path.read_bytes() == b"partial"


def test_at_h028_a_concurrent_reader_thread_never_observes_a_torn_or_stale_write(
    tmp_path: Path,
) -> None:
    """H28 (`docs/HARDENING_TODO.md`, HR-030): the predecessor of this
    test (`test_write_is_flushed_immediately_for_a_same_thread_reread`)
    was named for concurrent readers but was itself single-threaded — a
    sequential write-then-read in one thread can't distinguish "flushed
    for any reader" from "flushed only as far as *this* thread's own
    view." `AsyncJobRunner`'s actual production shape is exactly this:
    a background thread writing while `exec_output`'s async tool layer
    reads the same file from a different thread. A genuine reader
    thread, polling *while* the writer thread is still writing, must
    never see content that isn't a real prefix of what the file
    eventually contains — a torn write or a stale, unflushed read would
    show up here as a snapshot the final content doesn't start with."""
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=10_000, tail_keep_bytes=100)
    observed: list[bytes] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            if path.exists():
                observed.append(path.read_bytes())
            time.sleep(0.0005)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        for i in range(200):
            capture.write(f"chunk-{i:03d}\n".encode())
            time.sleep(0.001)
    finally:
        stop.set()
        reader_thread.join(timeout=5.0)
    capture.finish()

    final_content = path.read_bytes()
    assert len(observed) >= 5, "the reader thread barely ran — test is not exercising concurrency"
    for snapshot in observed:
        assert final_content.startswith(snapshot), (
            "a concurrent reader observed content that is not a genuine prefix "
            "of the final file — a torn write or a stale, unflushed read"
        )
    # Real concurrency, not the reader thread just catching the same
    # already-finished state repeatedly.
    assert len({len(s) for s in observed}) > 1


def test_total_bytes_counts_past_the_cap(tmp_path: Path) -> None:
    capture = StreamCapture(tmp_path / "out.log", max_output_bytes=10, tail_keep_bytes=5)
    capture.write(b"0123456789ABCDEFGHIJ")  # 20 bytes, cap is 10
    assert capture.total_bytes == 20
    assert capture.truncated is True


def test_on_disk_content_is_capped_at_max_output_bytes(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=10, tail_keep_bytes=5)
    capture.write(b"0123456789ABCDEFGHIJ")  # 20 bytes
    # Before finish(): only the first 10 bytes are on disk (the ring
    # buffer tail is not appended until the job ends).
    assert path.read_bytes() == b"0123456789"


def test_finish_appends_marker_and_tail_when_truncated(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=10, tail_keep_bytes=5)
    capture.write(b"0123456789ABCDEFGHIJ")  # 20 bytes: 10 written, 10 overflow
    capture.finish()
    content = path.read_bytes()
    assert content.startswith(b"0123456789")
    assert "--- 5 bytes omitted ---" in content.decode()
    assert content.endswith(b"FGHIJ")  # last 5 bytes, the tail_keep_bytes ring buffer


def test_finish_appends_nothing_extra_when_never_truncated(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(tmp_path / "out.log", max_output_bytes=100, tail_keep_bytes=20)
    capture.write(b"short")
    capture.finish()
    assert path.read_bytes() == b"short"


def test_write_across_multiple_chunks_splices_correctly_at_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=10, tail_keep_bytes=100)
    for chunk in [b"01234", b"56789", b"ABCDE"]:  # cap hit mid-third-chunk
        capture.write(chunk)
    capture.finish()
    assert capture.total_bytes == 15
    content = path.read_bytes()
    assert content.startswith(b"0123456789")
    assert content.endswith(b"ABCDE")


def test_ring_buffer_keeps_only_the_most_recent_tail_keep_bytes(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=0, tail_keep_bytes=3)
    capture.write(b"ABCDEFGHIJ")  # all overflow (cap is 0); ring keeps last 3
    capture.finish()
    content = path.read_bytes()
    assert content.endswith(b"HIJ")
    assert b"ABCDEFG" not in content  # discarded by the ring buffer


def test_read_log_bytes_returns_empty_for_missing_path(tmp_path: Path) -> None:
    assert read_log_bytes(str(tmp_path / "does-not-exist.log")) == b""


def test_read_log_bytes_returns_empty_for_none_path() -> None:
    assert read_log_bytes(None) == b""


def test_read_log_bytes_reads_real_content(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=100, tail_keep_bytes=20)
    capture.write(b"hello world")
    capture.finish()
    assert read_log_bytes(str(path)) == b"hello world"


# --- H29 (`docs/HARDENING_TODO.md`, HD3): seek rather than slurp -----------------


def test_read_log_bytes_offset_and_max_bytes_return_a_real_window(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"0123456789ABCDEFGHIJ")
    assert read_log_bytes(str(path), offset=5, max_bytes=3) == b"567"
    assert read_log_bytes(str(path), offset=18, max_bytes=100) == b"IJ"
    assert read_log_bytes(str(path), offset=0, max_bytes=0) == b""


def test_read_log_bytes_offset_past_end_of_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"short")
    assert read_log_bytes(str(path), offset=1000, max_bytes=10) == b""


def test_stat_log_bytes_matches_the_real_file_size(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"x" * 12345)
    assert stat_log_bytes(str(path)) == 12345


def test_stat_log_bytes_is_zero_for_a_missing_or_none_path(tmp_path: Path) -> None:
    assert stat_log_bytes(None) == 0
    assert stat_log_bytes(str(tmp_path / "does-not-exist.log")) == 0


def test_read_log_tail_returns_the_last_n_lines(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    lines = [f"line-{i:04d}\n".encode() for i in range(1000)]
    path.write_bytes(b"".join(lines))

    tail = read_log_tail(str(path), max_lines=5)

    assert tail.count(b"\n") >= 5
    reconstructed = tail.decode().splitlines(keepends=True)
    assert reconstructed[-5:] == [f"line-{i:04d}\n" for i in range(995, 1000)]


def test_read_log_tail_grows_its_read_window_when_lines_are_long(tmp_path: Path) -> None:
    """Real regression risk: a file whose lines are longer than
    `_TAIL_INITIAL_CHUNK_BYTES`'s single-chunk window needs a second,
    doubled read to find enough newlines — reproduced with lines wide
    enough that the initial 64 KiB chunk holds fewer than the requested
    line count."""
    path = tmp_path / "out.log"
    wide_line = ("x" * 20_000 + "\n").encode()  # ~20 KB per line
    path.write_bytes(wide_line * 10)  # ~200 KB total, > one 64 KiB chunk

    tail = read_log_tail(str(path), max_lines=5)

    assert tail.count(b"\n") >= 5


def test_read_log_tail_returns_the_whole_file_when_shorter_than_requested(
    tmp_path: Path,
) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"only\ntwo\nlines\n")
    tail = read_log_tail(str(path), max_lines=1000)
    assert tail == b"only\ntwo\nlines\n"


def test_read_log_tail_is_empty_for_a_missing_or_none_path(tmp_path: Path) -> None:
    assert read_log_tail(None, max_lines=5) == b""
    assert read_log_tail(str(tmp_path / "does-not-exist.log"), max_lines=5) == b""


def test_read_log_tail_is_empty_for_zero_or_negative_max_lines(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"some content\n")
    assert read_log_tail(str(path), max_lines=0) == b""
    assert read_log_tail(str(path), max_lines=-1) == b""
