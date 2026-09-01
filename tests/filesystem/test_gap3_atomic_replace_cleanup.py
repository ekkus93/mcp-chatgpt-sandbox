"""G3AT-013: atomic-write replacement failures clean temporary files."""

import logging
import os
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import FilesystemConfig
from dev_workspace_mcp.filesystem.security import FilesystemSecurity
from dev_workspace_mcp.filesystem.service import FilesystemService


def _service(root: Path) -> FilesystemService:
    security = FilesystemSecurity(root, authorized_roots=[root.parent])
    return FilesystemService(security, FilesystemConfig())


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "example"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    return root


def test_g3at_013_replace_failure_preserves_target_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    target = root / "existing.txt"
    original = b"original content must survive\n"
    target.write_bytes(original)
    service = _service(root)

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError) as excinfo:
        service.write_file("existing.txt", "replacement content\n")

    assert excinfo.value.errno == 5
    assert target.read_bytes() == original
    assert [p.name for p in root.iterdir() if "mcp-write-tmp" in p.name] == []


def test_g3at_013_cleanup_failure_preserves_replace_error_and_logs_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = _repo(tmp_path)
    target = root / "existing.txt"
    original = b"original content must survive\n"
    target.write_bytes(original)
    service = _service(root)

    real_unlink = os.unlink

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError(5, "Input/output error")

    def fail_temp_unlink(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> None:
        if "mcp-write-tmp" in os.fspath(path):
            raise PermissionError(13, "Permission denied")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(os, "unlink", fail_temp_unlink)

    with (
        caplog.at_level(logging.WARNING, logger="dev_workspace_mcp.filesystem.service"),
        pytest.raises(OSError) as excinfo,
    ):
        service.write_file("existing.txt", "replacement content\n")

    assert excinfo.value.errno == 5
    assert target.read_bytes() == original

    leftovers = [p.name for p in root.iterdir() if "mcp-write-tmp" in p.name]
    assert len(leftovers) == 1
    assert leftovers[0] in caplog.text
    assert "failed to remove atomic-write temp file" in caplog.text
