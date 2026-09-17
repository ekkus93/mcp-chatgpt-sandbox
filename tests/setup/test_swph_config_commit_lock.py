from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from mcp_chatgpt_sandbox.setup import config_writer
from mcp_chatgpt_sandbox.setup.config_writer import (
    ConfigUpdatePlan,
    SetupConfigError,
    commit_config_update,
    prepare_config_update,
)


def _minimal(tmp_path: Path) -> dict[str, object]:
    return {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
        "docker": {"default_image": "mcp-chatgpt-sandbox-test:24.04"},
        "ssh": {
            "keys_dir": str(tmp_path / "keys"),
            "known_hosts": str(tmp_path / "known_hosts"),
        },
        "audit": {"path": str(tmp_path / "audit.jsonl")},
    }


def _existing_plan(tmp_path: Path) -> tuple[Path, bytes, ConfigUpdatePlan]:
    target = tmp_path / "config.yml"
    target.write_text(yaml.safe_dump(_minimal(tmp_path), sort_keys=False))
    before = target.read_bytes()
    plan = prepare_config_update(
        target,
        wizard_values={"logging": {"level": "DEBUG"}},
        managed_fields={"logging"},
    )
    return target, before, plan


def test_swphv_006_lock_contention_is_bounded_and_never_modifies_config(tmp_path: Path) -> None:
    target, before, plan = _existing_plan(tmp_path)
    lock_path = target.with_name(f".{target.name}.setup.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SetupConfigError, match="locked by another setup writer"):
            commit_config_update(target, plan)
        assert target.read_bytes() == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    lock_stat = lock_path.stat()
    assert stat.S_IMODE(lock_stat.st_mode) == 0o600
    assert lock_stat.st_size == 0


def test_swphv_006_source_is_revalidated_after_commit_lock_is_acquired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, before, plan = _existing_plan(tmp_path)
    competing = before + b"# competing edit after review\n"

    @contextmanager
    def competing_writer_after_lock(_path: Path) -> Iterator[Path]:
        target.write_bytes(competing)
        yield target.with_name(f".{target.name}.setup.lock")

    monkeypatch.setattr(config_writer, "_config_commit_lock", competing_writer_after_lock)

    with pytest.raises(SetupConfigError, match="changed after review"):
        commit_config_update(target, plan)
    assert target.read_bytes() == competing


def test_swphv_006_unchanged_source_commits_with_exact_backup_and_reload(tmp_path: Path) -> None:
    target, before, plan = _existing_plan(tmp_path)

    result = commit_config_update(target, plan)

    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == before
    assert target.read_text() == plan.candidate_text
    assert result.config.logging.level == "DEBUG"
    lock_path = target.with_name(f".{target.name}.setup.lock")
    assert lock_path.read_bytes() == b""
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_swphv_006_symlink_lock_target_is_rejected_without_config_mutation(tmp_path: Path) -> None:
    target, before, plan = _existing_plan(tmp_path)
    decoy = tmp_path / "decoy.lock"
    decoy.write_text("do not touch")
    lock_path = target.with_name(f".{target.name}.setup.lock")
    lock_path.symlink_to(decoy)

    with pytest.raises(SetupConfigError, match="Could not open the configuration commit lock"):
        commit_config_update(target, plan)

    assert target.read_bytes() == before
    assert decoy.read_text() == "do not touch"


def test_swphv_006_concurrent_writers_cannot_both_commit_same_reviewed_source(
    tmp_path: Path,
) -> None:
    target, _before, plan = _existing_plan(tmp_path)

    def commit() -> str:
        try:
            commit_config_update(target, plan)
        except SetupConfigError as exc:
            return str(exc)
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: commit(), range(2)))

    assert outcomes.count("success") == 1
    failures = [outcome for outcome in outcomes if outcome != "success"]
    assert len(failures) == 1
    assert "changed after review" in failures[0] or "locked by another setup writer" in failures[0]


def test_swphv_006_persistent_lock_file_has_no_secret_payload(tmp_path: Path) -> None:
    target, _before, plan = _existing_plan(tmp_path)

    commit_config_update(target, plan)

    lock_path = target.with_name(f".{target.name}.setup.lock")
    assert lock_path.read_bytes() == b""
