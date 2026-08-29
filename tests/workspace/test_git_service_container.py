"""S19/H36 (`docs/HARDENING_TODO.md`): `run_git_in_container` — the
in-container replacement for `run_git_host_side` — against a real Docker
daemon and the real S7 default image.
"""

import subprocess
from pathlib import Path

import pytest

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import CommandFailedError
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.service import run_git_in_container
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_run_git_in_container_returns_stdout_and_exit_code(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    result = await run_git_in_container(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )

    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() == "main"


async def test_run_git_in_container_reports_failure(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    result = await run_git_in_container(
        ["rev-parse", "no-such-ref"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )

    assert not result.ok
    assert result.returncode != 0
    assert result.stderr != ""


async def test_run_git_in_container_applies_no_host_side_neutralization(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client,
    tmp_path: Path,
) -> None:
    """H36: this is the whole point of the migration — a code-executing
    config key (`filter.*.clean`) that `run_git_host_side` would have
    neutralized (HR-002, `_discover_code_executing_overrides`) now
    genuinely executes, in-container, exactly as `exec("git status")`
    already could. Reproduced directly rather than assumed: a
    `.gitattributes` binding every file to a `clean` filter that writes a
    marker file, then a real `git status` run through
    `run_git_in_container` — followed by a direct (non-`git`) exec that
    confirms the marker file the filter driver wrote actually exists."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    subprocess.run(
        ["git", "config", "filter.marker.clean", "sh -c 'touch /tmp/h36-marker; cat'"],
        cwd=repo_path,
        check=True,
    )
    (repo_path / ".gitattributes").write_text("* filter=marker\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "attrs"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("changed\n")

    server = create_server(config)
    workspace_id = await _open(server)

    db = open_db(config.state_db_path)
    manager = DockerWorkspaceManager(docker_client, db)
    record = manager.get(workspace_id)
    assert record.container_id is not None
    execution_service = ExecutionService(docker_client)

    check = await run_git_in_container(
        ["status", "--porcelain"],
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )
    assert check.ok

    # Not a `git` invocation — a direct exec, the same way `exec("test -e
    # ...")` would run — confirming the filter driver's side effect
    # actually happened, not merely that the config key survived.
    marker_exists = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["test", "-e", "/tmp/h36-marker"]}
    )
    assert marker_exists.structured_content["exit_code"] == 0


# --- truncation is reported, never silent (review finding, H36 regression) ---------
#
# `ExecutionService.capture` caps at `max_bytes`. Before this fix it did so
# with no signal, so every parser downstream of `run_git_in_container`
# silently received incomplete input: `git_log`/`git_status` crashed with an
# untranslated `ValueError`/`IndexError` (not a `WorkspaceError`, so
# `translate_workspace_errors` could not classify it), and `git_diff`
# reported `truncated: false` on an incomplete diff. Pre-H36 the host-side
# path used `subprocess.run(capture_output=True)` — no cap at all — so this
# was a regression introduced by the migration, not a pre-existing gap.
#
# A deliberately tiny `execution.max_output_bytes` forces the boundary
# cheaply; the real default is 10 MiB.


def _tiny_capture(config: ServerConfig, max_output_bytes: int = 256) -> ServerConfig:
    return config.model_copy(
        update={
            "execution": config.execution.model_copy(update={"max_output_bytes": max_output_bytes})
        }
    )


async def _running_container(server, docker_client, config: ServerConfig):
    workspace_id = await _open(server)
    db = open_db(config.state_db_path)
    record = DockerWorkspaceManager(docker_client, db).get(workspace_id)
    assert record.container_id is not None
    return record, ExecutionService(docker_client)


async def test_capture_reports_truncation(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """The primitive itself: `capture` SHALL say when it capped."""
    config = _tiny_capture(
        _configured(minimal_config, default_image_tag, tmp_path / "w" / "example")
    )
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    exec_id = execution_service.start(
        record.container_id,
        ["sh", "-c", "printf 'x%.0s' $(seq 1 5000)"],
        cwd=record.workspace_path,
        user="",
        env=None,
    )
    captured = execution_service.capture(exec_id, max_bytes=256)
    assert len(captured.stdout) == 256
    assert captured.stdout_truncated is True

    exec_id = execution_service.start(
        record.container_id,
        ["echo", "small"],
        cwd=record.workspace_path,
        user="",
        env=None,
    )
    assert execution_service.capture(exec_id, max_bytes=256).stdout_truncated is False


async def test_capture_reports_lossy_decode(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """AR-001 (`docs/AUDIT_SPEC.md` §4): `capture` SHALL say when its
    decode replaced a byte — distinct from `stdout_truncated`, which
    stays `False` here (well under the byte cap)."""
    config = _tiny_capture(
        _configured(minimal_config, default_image_tag, tmp_path / "w" / "example")
    )
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    # printf's own \xNN escape writes the literal byte, not valid UTF-8.
    exec_id = execution_service.start(
        record.container_id,
        ["printf", "bad\\377name"],
        cwd=record.workspace_path,
        user="",
        env=None,
    )
    captured = execution_service.capture(exec_id, max_bytes=256)
    assert captured.stdout_truncated is False
    assert captured.stdout_lossy is True
    assert "�" in captured.stdout

    exec_id = execution_service.start(
        record.container_id,
        ["echo", "small"],
        cwd=record.workspace_path,
        user="",
        env=None,
    )
    assert execution_service.capture(exec_id, max_bytes=256).stdout_lossy is False


async def test_run_git_in_container_raises_typed_error_on_truncation(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """Fail closed in the shared primitive, so every caller inherits it."""
    repo = tmp_path / "w" / "example"
    config = _tiny_capture(_configured(minimal_config, default_image_tag, repo))
    for i in range(200):
        (repo / f"file{i:03d}.txt").write_text("x\n")
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    with pytest.raises(CommandFailedError) as excinfo:
        await run_git_in_container(
            ["status", "--porcelain=v2", "--untracked-files=all", "-z"],
            container_id=record.container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=config.container,
            execution_config=config.execution,
        )
    assert "truncated" in str(excinfo.value).lower()


async def test_read_git_status_fails_typed_not_indexerror_on_truncation(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """Pre-fix this raised a bare `IndexError` from `parts[8]` on a
    porcelain-v2 record cut mid-way — not a `WorkspaceError`, so
    `translate_workspace_errors` could not classify it and it reached the
    client as an unstructured crash."""
    from dev_workspace_mcp.git.status import read_git_status

    repo = tmp_path / "w" / "example"
    config = _tiny_capture(_configured(minimal_config, default_image_tag, repo))
    for i in range(200):
        (repo / f"file{i:03d}.txt").write_text("x\n")
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    with pytest.raises(CommandFailedError):
        await read_git_status(
            repo,
            container_id=record.container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=config.container,
            execution_config=config.execution,
        )


async def test_read_git_log_fails_typed_not_valueerror_on_truncation(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """Pre-fix: a trailing record cut before its third field separator
    raised `ValueError: not enough values to unpack`. `max_entries` is
    client-supplied and unbounded, so this was client-reachable."""
    from dev_workspace_mcp.git.log import read_git_log

    repo = tmp_path / "w" / "example"
    config = _tiny_capture(_configured(minimal_config, default_image_tag, repo))
    for i in range(12):
        (repo / f"c{i}.txt").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit number {i}"], cwd=repo, check=True)
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    with pytest.raises(CommandFailedError):
        await read_git_log(
            repo,
            container_id=record.container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=config.container,
            execution_config=config.execution,
            max_entries=100,
        )


async def test_read_git_diff_never_reports_a_truncated_diff_as_complete(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """The silent-data-loss half: pre-fix a diff larger than the capture
    cap came back with `truncated: false` and a `total_bytes` computed
    from the *capped* bytes, so a client paginating to `next_offset ==
    total_bytes` believed it had the whole diff. §46 requires truncation
    be explicit; the daemon cannot serve what it never captured, so this
    fails closed rather than lying."""
    from dev_workspace_mcp.git.diff import read_git_diff

    repo = tmp_path / "w" / "example"
    config = _tiny_capture(_configured(minimal_config, default_image_tag, repo))
    (repo / "big.txt").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    (repo / "big.txt").write_text("".join(f"line {i}\n" for i in range(400)))
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    with pytest.raises(CommandFailedError):
        await read_git_diff(
            repo,
            container_id=record.container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=config.container,
            execution_config=config.execution,
            max_bytes=10**9,
        )


async def test_read_basic_git_state_degrades_rather_than_raising(
    minimal_config: ServerConfig, default_image_tag: str, docker_client, tmp_path: Path
) -> None:
    """`read_basic_git_state`'s documented contract is best-effort —
    `None` fields, never an exception, because it feeds `workspace_info`'s
    diagnostic block. It is the one caller that SHALL absorb a truncation
    rather than propagate it: a pathological working tree must not make
    `workspace_info` itself unavailable."""
    from dev_workspace_mcp.git_state import read_basic_git_state

    repo = tmp_path / "w" / "example"
    config = _tiny_capture(_configured(minimal_config, default_image_tag, repo))
    for i in range(200):
        (repo / f"file{i:03d}.txt").write_text("x\n")
    server = create_server(config)
    record, execution_service = await _running_container(server, docker_client, config)

    state = await read_basic_git_state(
        container_id=record.container_id,
        workspace_path=record.workspace_path,
        execution_service=execution_service,
        container_config=config.container,
        execution_config=config.execution,
    )
    assert state.clean is None  # the truncated `status` read is absorbed
    assert state.branch == "main"  # small reads are unaffected
