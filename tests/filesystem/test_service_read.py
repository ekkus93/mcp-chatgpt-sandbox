"""S10: `read_file` (§13.2, §12.2, §47) — including H29's stat-before-read
streaming, RR-018/RR-020's character-safe bounded ranges, RR-006's
`_hash_pread` chunking, and HR-011's race-free-access guarantee against
outside content.

Extracted from `test_service.py` — see `test_service_list.py`'s module
docstring for the split rationale. `_symlink_flipper` is duplicated here
(and in `test_service_write.py`/`test_service_delete.py`) rather than
shared, matching this file's own `_service`/`_repo` duplication.
"""

import contextlib
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.errors import InvalidArgumentError
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService, sha256_of


def _service(root: Path, *, config: FilesystemConfig | None = None) -> FilesystemService:
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    return FilesystemService(security, config or FilesystemConfig())


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text("fn main() {}\n")
    return root


# --- read_file --------------------------------------------------------------------


def test_read_file_returns_content_and_sha256(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.read_file("src/lib.rs")
    assert result.content == "fn main() {}\n"
    assert result.sha256 == sha256_of(b"fn main() {}\n")
    assert result.size == len("fn main() {}\n")
    assert result.truncated is False
    assert result.is_binary is False


def test_read_file_byte_range(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.read_file("src/lib.rs", byte_range=(0, 2))
    assert result.content == "fn"
    assert result.size == len("fn main() {}\n")  # full size, not the range


def test_at_vat002_byte_range_truncated_reflects_bytes_withheld_not_requested(
    tmp_path: Path,
) -> None:
    """VR-002 (`docs/REVIEW_SPEC.md` §4): the old code computed
    `truncated = span_len > limit` from the *requested* span
    (`end - start`), not from how much of the file actually remained. A
    client whose `end` runs past EOF, or who omits a client-side
    `max_bytes` cap smaller than the requested span, got `truncated:
    True` on content that was already complete — reproduced here with a
    10-byte file and a huge explicit `end` standing in for "the rest of
    the file" (the documented, correct way to get that is `end=None`)."""
    root = _repo(tmp_path)
    (root / "small.txt").write_text("0123456789")
    svc = _service(root)  # default max_read_bytes: 10 MiB

    result = svc.read_file("small.txt", byte_range=(0, 20_000_000))
    assert result.content == "0123456789"
    assert result.truncated is False

    # The documented idiom for "to EOF" already worked correctly — this
    # asserts the fix didn't regress it.
    result_end_none = svc.read_file("small.txt", byte_range=(0, None))
    assert result_end_none.truncated is False


def test_at_vat002_byte_range_truncated_still_true_when_bytes_are_genuinely_withheld(
    tmp_path: Path,
) -> None:
    """The other half of VR-002: `truncated` SHALL still be `True` when
    content actually was withheld — the fix narrows the flag to bytes
    withheld, it does not weaken it to never firing."""
    root = _repo(tmp_path)
    (root / "small.txt").write_text("0123456789")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=5))

    result = svc.read_file("small.txt", byte_range=(0, 20_000_000))
    assert result.content == "01234"
    assert result.truncated is True

    # A requested span narrower than the withheld remainder is also still
    # correctly flagged.
    result_narrow = svc.read_file("small.txt", byte_range=(0, 8), max_bytes=3)
    assert result_narrow.content == "012"
    assert result_narrow.truncated is True


def test_read_file_line_range(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "multi.txt").write_text("a\nb\nc\nd\n")
    svc = _service(root)
    result = svc.read_file("multi.txt", line_range=(1, 3))
    assert result.content == "b\nc\n"


def test_read_file_rejects_both_ranges_at_once(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.read_file("src/lib.rs", byte_range=(0, 1), line_range=(0, 1))


def test_read_file_reports_truncation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "big.txt").write_text("x" * 100)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))
    result = svc.read_file("big.txt")
    assert result.truncated is True
    assert len(result.content) <= 10
    assert result.size == 100  # full size still reported


def test_read_file_sha256_is_of_full_content_even_when_truncated(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    full = "x" * 100
    (root / "big.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))
    result = svc.read_file("big.txt")
    assert result.sha256 == sha256_of(full.encode())


# --- H29 (`docs/HARDENING_TODO.md`, HD3): stat before reading, not after --------


def test_at_h029_full_sha_and_size_are_correct_for_a_multi_megabyte_file(
    tmp_path: Path,
) -> None:
    """H29: `read_file` now bounds its *content* buffer to `max_read_bytes`
    before reading, streaming the remainder through the hasher in
    `_READ_FILE_HASH_CHUNK_BYTES`-sized chunks rather than holding the
    whole file in memory — a file genuinely larger than one such chunk
    (unlike the pre-existing 100-byte truncation tests above, too small
    to exercise the streaming loop at all) is what actually proves the
    multi-chunk path computes the same `sha256`/`size` a full read
    would."""
    from dev_workspace_mcp.filesystem.service import _READ_FILE_HASH_CHUNK_BYTES

    root = _repo(tmp_path)
    # A few chunks' worth, so the streaming loop actually iterates more
    # than once — not just one bounded head read.
    full = ("0123456789" * 100_000)[: _READ_FILE_HASH_CHUNK_BYTES * 3 + 12345]
    (root / "huge.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=64))

    result = svc.read_file("huge.txt")

    assert result.truncated is True
    assert len(result.content.encode()) <= 64
    assert result.size == len(full.encode())
    assert result.sha256 == sha256_of(full.encode())


def test_at_h029_a_byte_range_deep_in_a_large_file_still_works(tmp_path: Path) -> None:
    """RR-020 (`docs/REMEDIATION_SPEC.md` §13): `byte_range` now `seek`s
    directly to the requested span rather than reading the whole file
    (the pre-R13 behavior this test's own docstring used to describe) —
    confirmed still correct, including `sha256`/`size` for the whole
    file (computed via the same streamed hash pass the no-range path
    uses), for a file larger than the streaming chunk size."""
    from dev_workspace_mcp.filesystem.service import _READ_FILE_HASH_CHUNK_BYTES

    root = _repo(tmp_path)
    full = "0123456789" * (_READ_FILE_HASH_CHUNK_BYTES // 5)
    (root / "huge.txt").write_text(full)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=len(full.encode())))

    deep_offset = _READ_FILE_HASH_CHUNK_BYTES * 2
    result = svc.read_file("huge.txt", byte_range=(deep_offset, deep_offset + 10))

    assert result.content == full[deep_offset : deep_offset + 10]
    assert result.sha256 == sha256_of(full.encode())
    assert result.size == len(full.encode())


# --- RR-018/RR-020 (`docs/REMEDIATION_SPEC.md` §13): ranged reads are
# character-safe and bounded --------------------------------------------------


def test_rr018_a_multibyte_character_at_the_limit_boundary_is_truncated_text(
    tmp_path: Path,
) -> None:
    """The checklist's own reproduction: `max_read_bytes` landing exactly
    where a multi-byte character starts used to decode the *whole*
    (peek-byte-included) buffer in one shot, fail on that lone leading
    byte of the split character, and misreport an ordinary text file as
    binary — with `content=None` and, worse, `truncated=False`, denying
    the client even the signal to retry with a range."""
    root = _repo(tmp_path)
    (root / "f.txt").write_bytes(b"a" * 10 + "é".encode() + b"tail")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))

    result = svc.read_file("f.txt")

    assert result.is_binary is False
    assert result.content == "a" * 10
    assert result.truncated is True
    assert result.size == 16


def test_rr018_a_character_split_exactly_at_the_limit_is_dropped_not_corrupted(
    tmp_path: Path,
) -> None:
    """The limit itself, not just the peek byte, can land mid-character —
    here `max_read_bytes=11` leaves the window holding exactly the first
    of `é`'s 2 bytes. That lone byte must be trimmed off (deferred, not
    surfaced as U+FFFD or as a decode failure), the same way `git_diff`'s
    own pagination already handles this (RR-016)."""
    root = _repo(tmp_path)
    (root / "f.txt").write_bytes(b"a" * 10 + "é".encode() + b"tail")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=11))

    result = svc.read_file("f.txt")

    assert result.is_binary is False
    assert result.content == "a" * 10
    assert "�" not in (result.content or "")
    assert result.truncated is True


def test_rr018_a_genuinely_binary_file_larger_than_the_limit_is_still_truncated(
    tmp_path: Path,
) -> None:
    """The other half of the same fix: `truncated` must be reported
    correctly even down the `is_binary=True` path — a real regression
    hardcoded `truncated=False` there unconditionally."""
    root = _repo(tmp_path)
    (root / "f.bin").write_bytes(b"\x00\x01\x02\xff" * 50)
    svc = _service(root, config=FilesystemConfig(max_read_bytes=10))

    result = svc.read_file("f.bin")

    assert result.is_binary is True
    assert result.content is None
    assert result.truncated is True


def test_rr018_a_genuinely_incomplete_trailing_sequence_at_true_eof_is_binary(
    tmp_path: Path,
) -> None:
    """The converse of the trim fix: when the read reaches the *genuine*
    end of the file (not truncated by `limit`), a dangling partial UTF-8
    sequence there is not a windowing artifact — the file's own content
    truly isn't valid UTF-8, and trimming must not hide that."""
    root = _repo(tmp_path)
    (root / "f.bin").write_bytes(b"hello " + "é".encode()[:1])  # a real, permanent partial byte
    svc = _service(root, config=FilesystemConfig(max_read_bytes=1000))

    result = svc.read_file("f.bin")

    assert result.is_binary is True
    assert result.truncated is False  # this genuinely is the whole file


def test_rr020_byte_range_does_not_hold_the_whole_file_in_memory(tmp_path: Path) -> None:
    """RR-020: reproduced (via code inspection of the pre-fix
    `f.read()`) that `byte_range=(0, 1)` on a large file loaded the
    entire thing into memory just to return one byte. A real, sizable
    file here — peak RSS growth from serving a 5-byte range must stay
    far below the file's own size, which it would not if the whole file
    were still being read into a Python `bytes` object at any point."""
    import resource

    root = _repo(tmp_path)
    path = root / "big.bin"
    size = 200 * 1024 * 1024
    with path.open("wb") as f:
        f.seek(size - 1)
        f.write(b"\0")
        f.seek(0)
        f.write(b"hello")
    svc = _service(root, config=FilesystemConfig(max_read_bytes=1000))

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB, Linux

    result = svc.read_file("big.bin", byte_range=(0, 5))

    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert result.content == "hello"
    assert result.size == size
    # Generous headroom (50 MiB) above whatever this process already had
    # resident — nowhere near the file's own 200 MiB, which is what the
    # old `f.read()` behavior would have added to RSS.
    assert (after - before) < 50 * 1024, (
        f"peak RSS grew by {(after - before) / 1024:.1f} MiB serving a 5-byte "
        f"range from a {size / 1024 / 1024:.0f} MiB file — the whole file was "
        "likely still read into memory"
    )


def test_read_file_binary_reports_is_binary_without_content(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "binary.dat").write_bytes(b"\x00\x01\xff\xfe\x02")
    svc = _service(root)
    result = svc.read_file("binary.dat")
    assert result.is_binary is True
    assert result.content is None
    assert result.sha256 == sha256_of(b"\x00\x01\xff\xfe\x02")


def test_read_file_directory_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.read_file("src")


def test_read_file_git_metadata_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    from dev_workspace_mcp.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.read_file(".git/config")


# --- RR-006 (`docs/REMEDIATION_SPEC.md` §5): typed errors, not crashes -------------


def test_rr006_hash_pread_loops_across_multiple_chunks(tmp_path: Path) -> None:
    """`os.pread` can transfer fewer bytes than requested in a single
    call — confirmed the kernel caps one call at `0x7ffff000` bytes (just
    under 2 GiB) — so a real file that size can't be used as a test
    fixture. `chunk_size` lets this test force the same multi-call path
    with an ordinary small file instead: the looped, chunked result must
    equal a plain whole-file hash."""
    from dev_workspace_mcp.filesystem.service import _hash_pread

    content = os.urandom(10_000)
    path = tmp_path / "data.bin"
    path.write_bytes(content)

    fd = os.open(path, os.O_RDONLY)
    try:
        looped = _hash_pread(fd, len(content), chunk_size=777)  # forces ~13 iterations
    finally:
        os.close(fd)

    assert looped == sha256_of(content)


# --- HR-011 (docs/HARDENING_SPEC.md §7): race-free file access -----------------------


@pytest.fixture
def _symlink_flipper(tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
    """A background thread that continuously flips a directory `d`
    between a real directory and a symlink pointing outside the
    repository — standing in for the container racing the daemon via
    `exec`, the same threat `open_validated`'s own docstring names.
    Yields `(root, d, outside)`; stops the thread on teardown."""
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    d = root / "d"
    d.mkdir()

    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                if d.is_dir() and not d.is_symlink():
                    shutil.rmtree(str(d))
            except OSError:
                pass
            with contextlib.suppress(FileExistsError):
                os.symlink(str(outside), str(d))
            with contextlib.suppress(OSError):
                os.unlink(str(d))
            with contextlib.suppress(FileExistsError):
                d.mkdir()

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        yield root, d, outside
    finally:
        stop.set()
        thread.join(timeout=2)


def test_at_hat009_read_file_never_returns_content_from_outside_the_repository(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """A real regression: with `d` flipping between a real directory and
    a symlink to `outside`, `read_file("d/secret.txt")` returned the
    *host* file's content — a real private key, in the original
    reproduction — on the very first race iteration. Sustained here for
    2000 iterations with no leak, confirmed (by reverting the fix) to
    fail within ~150 iterations without it."""
    root, _d, outside = _symlink_flipper
    (outside / "secret.txt").write_text("OUTSIDE-THE-REPOSITORY\n")
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        try:
            result = service.read_file("d/secret.txt")
        except Exception:
            continue
        assert result.content is None or "OUTSIDE-THE-REPOSITORY" not in result.content


@pytest.fixture
def _git_metadata_flipper(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Same race shape as `_symlink_flipper` above, but the escape
    target is the repository's own **in-tree** `.git` directory rather
    than somewhere outside the repo entirely — G2R-005
    (`docs/GAP2_SPEC.md`)'s specific gap: under the shipped default
    `git_dir_mode: read-write`, `.git` lives *inside* `root`, so a race
    redirected there still passes plain containment, which is exactly
    what a containment-only post-open check cannot catch. Yields
    `(root, d)`; `root / ".git"` is a real (if minimal) directory for
    the duration."""
    root = tmp_path / "repo"
    root.mkdir()
    git_dir = root / ".git"
    git_dir.mkdir()
    d = root / "d"
    d.mkdir()

    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                if d.is_dir() and not d.is_symlink():
                    shutil.rmtree(str(d))
            except OSError:
                pass
            with contextlib.suppress(FileExistsError):
                os.symlink(str(git_dir), str(d))
            with contextlib.suppress(OSError):
                os.unlink(str(d))
            with contextlib.suppress(FileExistsError):
                d.mkdir()

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        yield root, d
    finally:
        stop.set()
        thread.join(timeout=2)


def test_at_g2r005_read_file_never_returns_content_from_inside_git_metadata(
    _git_metadata_flipper: tuple[Path, Path],
) -> None:
    """G2R-005 (`docs/GAP2_SPEC.md`): the read-side reproduction — the
    concrete example the spec names is `.git/config`, which can hold a
    repository-scoped credential helper or URL rewrite; this test uses
    a distinctly-named file so a leak is unambiguous. A race redirecting
    through `.git` used to pass the old containment-only post-open
    check cleanly and return the file's real content, since `.git` is
    *inside* the repository root under the shipped default
    `git_dir_mode: read-write`."""
    root, _d = _git_metadata_flipper
    (root / ".git" / "secret-config-value").write_text("GIT-METADATA-SECRET\n")
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        try:
            result = service.read_file("d/secret-config-value")
        except Exception:
            continue
        assert result.content is None or "GIT-METADATA-SECRET" not in result.content
