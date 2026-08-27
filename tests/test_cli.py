"""S1/S2: `serve --config <path>` CLI shape, now backed by real config
validation (§73 step 1) and a Docker reachability check (§73 step 2)."""

import contextlib
import os
import signal
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from dev_workspace_mcp.cli import main
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager


def _write_config(path: Path, data: dict[str, Any]) -> Path:
    config_path = path / "config.yml"
    config_path.write_text(yaml.safe_dump(data))
    return config_path


def test_serve_requires_config_option() -> None:
    result = CliRunner().invoke(main, ["serve"])
    assert result.exit_code != 0
    assert "--config" in result.output


def test_serve_rejects_missing_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yml"
    result = CliRunner().invoke(main, ["serve", "--config", str(missing)])
    assert result.exit_code == 1
    assert "cannot read config file" in result.output


def test_serve_rejects_invalid_config(tmp_path: Path) -> None:
    """A config missing a required field (§52) refuses to serve rather than
    starting with a partially-formed configuration."""
    config_path = _write_config(tmp_path, {"version": 1})  # missing docker/ssh/audit
    result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_serve_warns_but_continues_when_docker_unavailable(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)
    with (
        patch("dev_workspace_mcp.cli.docker_available", return_value=False),
        patch("dev_workspace_mcp.cli.run_server") as mock_run,
    ):
        result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "degraded" in result.output
    mock_run.assert_called_once()


def test_serve_starts_transport_on_valid_config(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)
    with (
        patch("dev_workspace_mcp.cli.docker_available", return_value=True),
        patch("dev_workspace_mcp.cli.run_server") as mock_run,
    ):
        result = CliRunner().invoke(
            main, ["serve", "--config", str(config_path), "--transport", "stdio"]
        )
    assert result.exit_code == 0
    assert "degraded" not in result.output
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["transport"] == "stdio"


def test_serve_reconciles_non_terminal_jobs_and_warns(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    """§15.4/§73 step 4: a job left `running` by an unclean shutdown is
    marked `lost` before the transport starts, and the operator is
    warned — this is real, not mocked, so it covers the CLI's actual
    wiring into `reconcile_jobs`, not just the function in isolation."""
    config_path = _write_config(tmp_path, minimal_config_dict)

    db = open_db(minimal_config_dict["state_db_path"])
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_test', 'repo_test', 'persistent', '/workspace', 'running',
                '2026-01-01', '2026-01-01')
        """
    )
    db.commit()
    JobStore(db).create(
        job_id="job_orphan",
        workspace_id="ws_test",
        command="make",
        cwd="/workspace",
        stdout_log_path="/state/jobs/job_orphan/stdout.log",
        stderr_log_path="/state/jobs/job_orphan/stderr.log",
    )
    JobStore(db).mark_running("job_orphan", pid=1)
    db.close()

    with (
        patch("dev_workspace_mcp.cli.docker_available", return_value=True),
        patch("dev_workspace_mcp.cli.run_server") as mock_run,
    ):
        result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "job_orphan" in result.output
    assert "lost" in result.output
    mock_run.assert_called_once()

    db = open_db(minimal_config_dict["state_db_path"])
    row = db.execute("SELECT state FROM job WHERE id = 'job_orphan'").fetchone()
    assert row["state"] == "lost"


def test_serve_terminates_an_orphaned_ssh_agent_for_a_no_longer_running_workspace(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    """§73 step 6, §44.1 step 5: a real `ssh-agent` tracked for a
    workspace that is not `running` gets torn down at startup — through
    the real `serve` command's actual wiring (`running_workspace_key_paths`
    + `SSHAgentManager.reconcile`), not the pieces tested in isolation.
    `minimal_config_dict`'s `ssh.mode` defaults to `agent` (unset), so
    this exercises the same code path every other CLI test in this file
    already runs through without noticing.
    """
    config_path = _write_config(tmp_path, minimal_config_dict)

    db = open_db(minimal_config_dict["state_db_path"])
    db.execute("INSERT INTO repository (id, canonical_path) VALUES ('repo_test', '/repo')")
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_test', 'repo_test', 'persistent', '/workspace', 'stopped',
                '2026-01-01', '2026-01-01')
        """
    )
    db.commit()

    key_path = tmp_path / "orphan.ed25519"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)
    ssh_agent_manager = SSHAgentManager(
        db, runtime_dir=Path(minimal_config_dict["state_db_path"]).parent / "ssh-agents"
    )
    stale = ssh_agent_manager.ensure("ws_test", key_path)
    db.close()

    try:
        with (
            patch("dev_workspace_mcp.cli.docker_available", return_value=True),
            patch("dev_workspace_mcp.cli.run_server") as mock_run,
        ):
            result = CliRunner().invoke(main, ["serve", "--config", str(config_path)])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()

        db = open_db(minimal_config_dict["state_db_path"])
        after = SSHAgentManager(
            db, runtime_dir=Path(minimal_config_dict["state_db_path"]).parent / "ssh-agents"
        ).get("ws_test")
        assert after is None  # torn down: the workspace was not "running"

        try:
            os.kill(stale.pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(stale.pid, signal.SIGTERM)


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "dev-workspace-mcp" in result.output
