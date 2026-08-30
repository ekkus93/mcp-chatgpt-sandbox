"""S10: `write_file` (§13.3, §12.2, §47, AT-003, AT-018) — including
RR-006's typed-error coverage, H28's intermediate-symlink mkdir guard, and
HR-011's race-free-access guarantee against writing outside the repository.

Extracted from `test_service.py` — see `test_service_list.py`'s module
docstring for the split rationale. `_symlink_flipper` is duplicated here
(and in `test_service_read.py`/`test_service_delete.py`) rather than
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
from dev_workspace_mcp.errors import FileConflictError, InvalidArgumentError
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


# --- write_file (AT-003, AT-018) ------------------------------------------------


def test_at_003_write_appears_immediately_on_disk(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("new.txt", "hello")
    assert (root / "new.txt").read_text() == "hello"


def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("a/b/c.txt", "nested")
    assert (root / "a" / "b" / "c.txt").read_text() == "nested"


def test_write_file_returns_sha256_and_size(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    result = svc.write_file("new.txt", "hello")
    assert result.sha256 == sha256_of(b"hello")
    assert result.size == 5


def test_write_file_without_expected_sha_always_overwrites(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("f.txt", "one")
    svc.write_file("f.txt", "two")
    assert (root / "f.txt").read_text() == "two"


def test_at_g2r010_a_successful_writes_sha256_matches_a_subsequent_read(
    tmp_path: Path,
) -> None:
    """G2R-010 (`docs/GAP2_SPEC.md`): the old truncate-then-single-
    `os.pwrite` sequence never checked `pwrite`'s return value, so a
    short write (POSIX permits transferring fewer bytes than requested)
    could report a `sha256`/`size` for the buffer it *intended* to
    write, not what actually reached disk — `expected_sha256` on a
    later call would then never match a real `read_file`. `_write_all`
    now loops until every byte is confirmed written, so the value this
    computes from the buffer is provably what is on disk by the time it
    returns — checked here the direct way, against a real
    `read_file`-equivalent, not by asserting the loop ran."""
    root = _repo(tmp_path)
    svc = _service(root)
    content = "line one\nline two\nline three\n" * 100  # large enough to be non-trivial
    result = svc.write_file("big.txt", content)
    on_disk = (root / "big.txt").read_bytes()
    assert result.sha256 == sha256_of(on_disk)
    assert result.size == len(on_disk)


def test_at_g2r010_a_write_that_fails_partway_leaves_previous_content_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2R-010: the old sequence `ftruncate(fd, 0)` then a single
    unchecked `pwrite` — if the write then failed outright (disk full,
    `ENOSPC`), the *previous* content was already gone, destroyed by the
    truncate, before any new content ever landed. The new sequence
    writes to a fresh temp file first and `os.replace`s it over the
    target only once the write fully succeeds: a failure during the
    write touches only the temp file, never the real target.

    Patches both `os.write` (this fix's own mechanism) and `os.pwrite`
    (the pre-fix mechanism's) to fail on the very first call — an
    immediate, unconditional `ENOSPC`, matching the actual scenario
    both this task's own write-up and `docs/GAP2_SPEC.md` describe —
    rather than just the one the current source happens to use: a
    revert-check against this test SHALL exercise the *old* code's
    actual data-loss bug, not merely find that the old code doesn't
    call the same syscall this fix introduced and so never reaches the
    mock at all. (A short-write-then-fail-on-the-second-call design was
    tried first and rejected: the old code's single unchecked `pwrite`
    call never notices a short write at all — no exception, silently
    wrong `sha256`/`size` instead — so it never reaches the mock's
    second branch either; that is a *different* defect (an unverified
    short write, also fixed by this same task, covered by the
    sha256-matches-a-read test above) from the one this test targets.)"""
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("existing.txt", "original content, must survive\n")

    def _always_fail(*args: object, **kwargs: object) -> int:
        raise OSError(28, "No space left on device")  # ENOSPC

    monkeypatch.setattr(os, "write", _always_fail)
    monkeypatch.setattr(os, "pwrite", _always_fail)

    with pytest.raises(OSError):
        svc.write_file("existing.txt", "x" * 1000)

    assert (root / "existing.txt").read_text() == "original content, must survive\n"
    # the failed attempt's own temp file is not left behind either.
    leftover_temp_files = [p for p in root.iterdir() if "mcp-write-tmp" in p.name]
    assert leftover_temp_files == []


def test_write_file_with_matching_expected_sha_succeeds(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    first = svc.write_file("f.txt", "one")
    svc.write_file("f.txt", "two", expected_sha256=first.sha256)
    assert (root / "f.txt").read_text() == "two"


def test_at_018_stale_expected_sha_raises_file_conflict(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    svc.write_file("f.txt", "one")
    stale_sha = sha256_of(b"never-written")
    with pytest.raises(FileConflictError) as excinfo:
        svc.write_file("f.txt", "two", expected_sha256=stale_sha)
    assert excinfo.value.recoverable is True
    assert (root / "f.txt").read_text() == "one"  # unchanged


def test_write_file_with_expected_sha_on_nonexistent_file_conflicts(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(FileConflictError):
        svc.write_file("new.txt", "content", expected_sha256=sha256_of(b"anything"))


def test_write_file_exceeding_limit_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root, config=FilesystemConfig(max_write_bytes=5))
    with pytest.raises(InvalidArgumentError):
        svc.write_file("f.txt", "this is too long")


def test_write_file_git_metadata_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    from dev_workspace_mcp.errors import PathGitMetadataError

    with pytest.raises(PathGitMetadataError):
        svc.write_file(".git/hooks/pre-commit", "#!/bin/sh\n")


# --- RR-006 (`docs/REMEDIATION_SPEC.md` §5): typed errors, not crashes -------------


def test_rr006_write_file_onto_a_directory_raises_invalid_argument(tmp_path: Path) -> None:
    """Reproduced empirically that this used to propagate a raw,
    untranslated `IsADirectoryError` all the way out of `write_file` —
    `translate_workspace_errors` only catches `WorkspaceError`, so the
    tool call crashed instead of failing cleanly. Also confirms the
    `stat.S_ISDIR` check this replaces really was dead code: opening an
    existing directory with `O_RDWR` already raises `IsADirectoryError`
    before that check could ever run against it."""
    root = _repo(tmp_path)
    svc = _service(root)
    with pytest.raises(InvalidArgumentError):
        svc.write_file("src", "hello")


def test_rr006_write_file_permission_denied_raises_invalid_argument(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = _service(root)
    target = root / "locked.txt"
    target.write_text("original")
    target.chmod(0o000)
    try:
        with pytest.raises(InvalidArgumentError):
            svc.write_file("locked.txt", "new content")
    finally:
        target.chmod(0o644)  # restore so tmp_path cleanup can remove it


def test_rr006_ensure_parent_directories_plain_file_blocking_via_write_file(
    tmp_path: Path,
) -> None:
    """The same distinction as `test_security.py`'s direct test, exercised
    through the actual `write_file` client-facing entry point: a plain
    file blocking an intermediate component is a benign mistake, not
    reported as an escape."""
    root = _repo(tmp_path)
    svc = _service(root)
    (root / "notadir.txt").write_text("hi")
    with pytest.raises(InvalidArgumentError):
        svc.write_file("notadir.txt/nested/thing.txt", "content")


def test_rr006_write_file_expected_sha_matches_across_a_multi_chunk_hash(
    tmp_path: Path,
) -> None:
    """End-to-end version of the same fix, through `write_file`'s own
    `expected_sha256` conflict check — using the real (large, default)
    chunk size, so this specifically guards against a future regression
    that reintroduces a single unbounded `os.pread` call."""
    root = _repo(tmp_path)
    svc = _service(root)
    original = "x" * 50_000
    svc.write_file("big.txt", original)
    correct_sha = sha256_of(original.encode("utf-8"))

    # Matching expected_sha256 succeeds...
    svc.write_file("big.txt", "replaced", expected_sha256=correct_sha)
    # ...and a stale one is correctly rejected as a real conflict, not
    # masked by a mis-hashed "current" value.
    with pytest.raises(FileConflictError):
        svc.write_file("big.txt", "again", expected_sha256=correct_sha)


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


def test_at_hat009_write_file_never_lands_outside_the_repository(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """The write-side counterpart: `write_file("d/pwned.txt", ...)` must
    never create a file under `outside`, and — the bug this test itself
    caught while this task was being written — a *rejected* write must
    not leave a stray file behind outside the repository either."""
    root, _d, outside = _symlink_flipper
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.write_file("d/pwned.txt", "written by the container\n")
        assert not (outside / "pwned.txt").exists()


def test_at_h028_write_file_never_mkdirs_through_an_intermediate_symlink(
    _symlink_flipper: tuple[Path, Path, Path],
) -> None:
    """H28 (`docs/HARDENING_TODO.md`, HR-030): the mkdir-side gap H9
    (HR-011) documented and deliberately left open — `write_file`'s old
    `path.parent.mkdir(parents=True, exist_ok=True)` walked the
    *resolved path string*, which happily creates directories through
    whatever `d` currently is. A single-level target (`d/pwned.txt`,
    the existing HAT-009 write test above) can't reach this gap at
    all: `resolve()` itself already rejects the call outright if `d` is
    a symlink to `outside` at resolution time, since the immediate
    parent needs no `mkdir` — there's no intermediate component left to
    create. A *nested* target (`d/nested/pwned.txt`) is what actually
    exercises it: `resolve()` can legitimately succeed while `d` is
    still a real, in-bounds directory (the deeper `nested` component
    doesn't exist yet, so nothing to follow), and only *after* that
    does `d` get swapped to a symlink before the parent-directory walk
    runs — reproduced, by reverting `ensure_parent_directories` back to
    the old `mkdir(parents=True)`, as a real `outside/nested/` directory
    left behind on the host."""
    root, _d, outside = _symlink_flipper
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.write_file("d/nested/pwned.txt", "written by the container\n")
        assert not (outside / "nested").exists()
        assert not (outside / "nested" / "pwned.txt").exists()


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


def test_at_g2r005_write_file_never_lands_inside_git_metadata(
    _git_metadata_flipper: tuple[Path, Path],
) -> None:
    """G2R-005 (`docs/GAP2_SPEC.md`): the write-side reproduction of the
    actual named consequence — a race redirecting through `.git` used
    to pass the old containment-only post-open check cleanly and write
    straight through it (the concrete example in the spec is
    `.git/hooks/pre-commit`, a code-execution primitive; this test
    writes to the top level of the flipped target, which is enough to
    prove any write into `.git` is refused, not just that one file)."""
    root, _d = _git_metadata_flipper
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    service = FilesystemService(security, FilesystemConfig())

    for _ in range(2000):
        with contextlib.suppress(Exception):
            service.write_file("d/pwned", "written by the container\n")
        assert not (root / ".git" / "pwned").exists()
