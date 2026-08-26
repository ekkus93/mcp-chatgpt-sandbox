"""S16: on-disk job log capture (§15.6)."""

from pathlib import Path

from dev_workspace_mcp.execution.log_capture import StreamCapture, read_log_bytes


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


def test_write_is_flushed_immediately_for_concurrent_readers(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    capture = StreamCapture(path, max_output_bytes=100, tail_keep_bytes=20)
    capture.write(b"partial")
    # No finish() yet — a concurrent reader must already see this chunk.
    assert path.read_bytes() == b"partial"


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
