"""S31: `reset`/`rebuild`/`delete` — §8.4's operator path, exposed as
real CLI subcommands, against a real Docker daemon and the real S7
default image.
"""

import subprocess
from pathlib import Path
from typing import Any

import docker
import pytest
import yaml
from click.testing import CliRunner

from mcp_chatgpt_sandbox.cli import main
from mcp_chatgpt_sandbox.config import load_config
from mcp_chatgpt_sandbox.server import create_server


def _write_config(path: Path, data: dict[str, Any]) -> Path:
    config_path = path / "config.yml"
    config_path.write_text(yaml.safe_dump(data))
    return config_path


def _configured_repo(
    minimal_config_dict: dict[str, Any], default_image_tag: str, repo_path: Path
) -> dict[str, Any]:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    minimal_config_dict["docker"]["default_image"] = default_image_tag
    minimal_config_dict["repositories"] = [
        {"id": "example", "path": str(repo_path), "github": "owner/example"}
    ]
    return minimal_config_dict


async def _open(config_path: Path) -> str:
    server = create_server(load_config(config_path))
    result = await server.call_tool("workspace_open", {"repository_id": "example"})
    return result.structured_content["workspace_id"]


async def _exec(config_path: Path, workspace_id: str, shell: str):
    server = create_server(load_config(config_path))
    return await server.call_tool("exec", {"workspace_id": workspace_id, "shell": shell})


async def test_at_005_reset_recreates_the_container_and_preserves_the_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-005: repository contents remain intact; container-local
    installed state is removed."""
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)

    workspace_id = await _open(config_path)
    # a real host-side write to the repository (bind mount) — must
    # survive the reset...
    (repo_path / "committed.txt").write_text("keep me\n")
    # ...and a container-local marker — must NOT survive it.
    await _exec(config_path, workspace_id, "echo container-local > /tmp/marker")

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert "reset" in cli_result.output

    assert (repo_path / "committed.txt").read_text() == "keep me\n"

    marker_check = await _exec(config_path, workspace_id, "test -f /tmp/marker; echo $?")
    assert marker_check.structured_content["stdout_tail"].strip() != "0"  # gone: a fresh container

    repo_check = await _exec(config_path, workspace_id, "cat /workspace/committed.txt")
    assert repo_check.structured_content["stdout_tail"].strip() == "keep me"


async def test_reset_uses_the_project_configs_image_when_present(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, "this-image-does-not-exist:latest", repo_path)
    # `load_project_config` reads this straight off the filesystem — it
    # does not need to be committed to git.
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump({"version": 1, "image": default_image_tag})
    )
    config_path = _write_config(tmp_path, minimal_config_dict)

    await _open(config_path)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output


async def test_reset_fails_clearly_for_a_workspace_never_opened(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code != 0
    assert "nothing to reset" in cli_result.output


async def test_reset_fails_clearly_for_an_unknown_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "does-not-exist"]
    )
    assert cli_result.exit_code != 0
    assert "not authorized" in cli_result.output or "error" in cli_result.output.lower()


async def test_rebuild_recreates_the_container(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    await _exec(config_path, workspace_id, "echo x > /tmp/marker")

    cli_result = CliRunner().invoke(
        main, ["rebuild", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    marker_check = await _exec(config_path, workspace_id, "test -f /tmp/marker; echo $?")
    assert marker_check.structured_content["stdout_tail"].strip() != "0"


# --- H32: reset and rebuild have distinct behavior, not one shared code path -------


async def test_at_h032_rebuild_attempts_an_image_pull_reset_does_not(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §8.4 row): the observable
    difference between the two commands — `default_image_tag` is a
    locally-built-only test image (never pushed anywhere), so a real
    pull attempt against it genuinely fails and surfaces the fallback
    warning this test looks for; `reset` never attempts one at all."""
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    await _open(config_path)

    reset_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert reset_result.exit_code == 0, reset_result.output
    assert "pull" not in reset_result.output

    rebuild_result = CliRunner().invoke(
        main, ["rebuild", "--config", str(config_path), "--repository", "example"]
    )
    assert rebuild_result.exit_code == 0, rebuild_result.output
    assert "could not pull image" in rebuild_result.output


# --- H32: .mcp-workspace.yml setup steps re-run after reset/rebuild ----------------


async def test_at_h032_reset_re_runs_mcp_workspace_yml_setup_steps(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §8.4/§10 rows): before this
    task, a `setup` step never ran again for an already-existing
    workspace id — `reset`/`rebuild` handed back a genuinely fresh,
    zero-setup container, and the *next* `workspace_open` silently
    treated it as already-provisioned (`was_created`'s own "did a DB
    row already exist" check can never see a *container* being fresh,
    only a *row*)."""
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump({"version": 1, "setup": ["touch /tmp/setup-ran"]})
    )
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    # the setup step already ran once, for the original creation — prove
    # `reset` re-runs it, not merely that it ran the first time.
    first_check = await _exec(config_path, workspace_id, "test -f /tmp/setup-ran; echo $?")
    assert first_check.structured_content["stdout_tail"].strip() == "0"

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    check = await _exec(config_path, workspace_id, "test -f /tmp/setup-ran; echo $?")
    assert check.structured_content["stdout_tail"].strip() == "0"


async def test_at_h032_a_failing_setup_step_after_reset_is_a_clear_cli_error(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    await _open(config_path)  # no setup steps yet — this open must succeed

    # `load_project_config` reads live off disk on every call — adding
    # a failing step now only affects what runs *after* this point.
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump({"version": 1, "setup": ["exit 1"]})
    )
    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )

    assert cli_result.exit_code != 0
    assert "setup step failed" in cli_result.output


# --- H32: reset/rebuild restore project environment: and cache mounts --------------


async def test_at_h032_reset_restores_project_environment_and_cache_mounts(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §11/§10 rows): `reset`
    discards *container-local* state — it never had a reason to also
    drop the project's own configured `environment:` or cache-volume
    mounts, both of which `resolve_caches`/`cache_volume_name` compute
    the same way `workspace_open` itself would, with no live state to
    "duplicate"."""
    from mcp_chatgpt_sandbox.workspace.caches import cache_volume_name

    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "environment": {"RUST_BACKTRACE": "1"},
                "caches": [{"path": "$HOME/.cargo", "name": "cargo"}],
            }
        )
    )
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    volume_name = cache_volume_name(repository_id="example", cache_name="cargo")
    docker_client.volumes.get(volume_name)  # sanity: workspace_open created it

    cli_result = CliRunner().invoke(
        main, ["reset", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    env_check = await _exec(config_path, workspace_id, "echo $RUST_BACKTRACE")
    assert env_check.structured_content["stdout_tail"].strip() == "1"

    cache_write = await _exec(
        config_path, workspace_id, "echo hello > ~/.cargo/reset-survives-here"
    )
    assert cache_write.structured_content["exit_code"] == 0, cache_write.structured_content
    docker_client.volumes.get(volume_name)  # still the same volume, not recreated


async def test_delete_removes_the_workspace_and_preserves_the_repository(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    config_path = _write_config(tmp_path, minimal_config_dict)
    workspace_id = await _open(config_path)
    (repo_path / "committed.txt").write_text("still here\n")

    cli_result = CliRunner().invoke(
        main, ["delete", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    assert (repo_path / "committed.txt").read_text() == "still here\n"

    server = create_server(load_config(config_path))
    with pytest.raises(ToolError, match="WORKSPACE_NOT_FOUND"):
        await server.call_tool("workspace_info", {"workspace_id": workspace_id})


async def test_at_hr009_delete_reclaims_cache_volumes(
    minimal_config_dict: dict[str, Any],
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-009 (`docs/HARDENING_SPEC.md` §4.2): the `delete` CLI command
    recomputes cache volume names from the repository's own
    `.mcp-workspace.yml` and reclaims them — otherwise a repository id
    later reused for a different project would silently inherit the old
    project's cache volumes."""
    from mcp_chatgpt_sandbox.workspace.caches import cache_volume_name

    repo_path = tmp_path / "work" / "example"
    _configured_repo(minimal_config_dict, default_image_tag, repo_path)
    (repo_path / ".mcp-workspace.yml").write_text(
        yaml.safe_dump({"version": 1, "caches": [{"name": "cargo", "path": "$HOME/.cargo"}]})
    )
    config_path = _write_config(tmp_path, minimal_config_dict)
    await _open(config_path)

    volume_name = cache_volume_name(repository_id="example", cache_name="cargo")
    docker_client.volumes.get(volume_name)  # sanity: opening the workspace created it

    cli_result = CliRunner().invoke(
        main, ["delete", "--config", str(config_path), "--repository", "example"]
    )
    assert cli_result.exit_code == 0, cli_result.output

    with pytest.raises(docker.errors.NotFound):
        docker_client.volumes.get(volume_name)
