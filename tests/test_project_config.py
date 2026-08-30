"""S30: `.mcp-workspace.yml` parsing (§9, §10)."""

import json
from pathlib import Path

import pytest
import yaml

from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.project_config import devcontainer_json_is_ignored, load_project_config


def _write(repo_path: Path, data: dict, filename: str = ".mcp-workspace.yml") -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / filename).write_text(yaml.safe_dump(data))


def test_returns_none_when_no_project_config_file_exists(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    assert load_project_config(repo_path) is None


def test_parses_the_full_documented_example(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(
        repo_path,
        {
            "version": 1,
            "image": "ubuntu:24.04",
            "workspace": "/workspace",
            "setup": [
                "sudo apt-get update",
                "sudo apt-get install -y build-essential curl git pkg-config",
            ],
            "environment": {"RUST_BACKTRACE": "1"},
            "caches": [
                {"path": "$HOME/.cargo", "name": "cargo"},
                {"path": "$HOME/.rustup", "name": "rustup"},
            ],
            "github": {"repository": "ekkus93/example-project"},
            "validation": {
                "format": ["cargo fmt --check"],
                "lint": ["cargo clippy --all-targets --all-features -- -D warnings"],
                "test": ["cargo test --all-features"],
            },
        },
    )

    config = load_project_config(repo_path)

    assert config is not None
    assert config.version == 1
    assert config.image == "ubuntu:24.04"
    assert config.setup == [
        "sudo apt-get update",
        "sudo apt-get install -y build-essential curl git pkg-config",
    ]
    assert config.environment == {"RUST_BACKTRACE": "1"}
    assert [c.name for c in config.caches] == ["cargo", "rustup"]
    assert config.github.repository == "ekkus93/example-project"
    assert config.validation.test == ["cargo test --all-features"]


def test_minimal_config_only_requires_version(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1})
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image is None
    assert config.setup == []
    assert config.caches == []


def test_yaml_extension_is_also_accepted(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "image": "alpine"}, filename=".mcp-workspace.yaml")
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image == "alpine"


def test_unknown_top_level_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "not_a_real_field": True})
    with pytest.raises(ContainerCreateFailedError, match="invalid project configuration"):
        load_project_config(repo_path)


def test_missing_required_version_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"image": "alpine"})
    with pytest.raises(ContainerCreateFailedError, match="invalid project configuration"):
        load_project_config(repo_path)


@pytest.mark.parametrize("version", [999, 0, -1, 2])
def test_at_h032_an_unsupported_version_is_rejected(tmp_path: Path, version: int) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §10 row): `999` — or any
    version this daemon has never implemented — used to parse cleanly
    and get recorded as the workspace's `config_version` (§9.2's
    reproducibility record), silently as wrong as `version: 1`."""
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": version})
    with pytest.raises(ContainerCreateFailedError, match="unsupported version"):
        load_project_config(repo_path)


@pytest.mark.parametrize(
    "key", ["MCP_UID", "MCP_GID", "MCP_SUDO", "MCP_WORKSPACE_PATH", "MCP_SSH_MODE", "SSH_AUTH_SOCK"]
)
def test_at_hat005_environment_cannot_set_a_daemon_controlled_variable(
    tmp_path: Path, key: str
) -> None:
    """A real regression: `environment: {MCP_UID: "0"}` used to reach
    `build_workspace_run_kwargs`'s `extra_env` unfiltered and, merged
    last, silently override the daemon-computed container identity —
    running the container as root on a daemon with no user-namespace
    remapping, routing around the `container.user: root` refusal
    entirely (that refusal only ever inspects `container_config.user`)."""
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "environment": {key: "0"}})
    with pytest.raises(ContainerCreateFailedError, match="daemon-controlled variable"):
        load_project_config(repo_path)


def test_at_hat005_an_ordinary_environment_variable_is_still_accepted(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "environment": {"RUST_LOG": "debug"}})
    config = load_project_config(repo_path)
    assert config is not None
    assert config.environment == {"RUST_LOG": "debug"}


@pytest.mark.parametrize(
    "key", ["PATH", "BASH_ENV", "ENV", "SHELLOPTS", "IFS", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH"]
)
def test_at_gr004_environment_cannot_set_an_interpreter_or_resolution_variable(
    tmp_path: Path, key: str
) -> None:
    """GR-004 (`docs/GAP_SPEC.md`): `_environment_cannot_shadow_daemon_
    variables` (HR-006) rejects the daemon's own identity/credential
    variables, but `docker/default/entrypoint.sh` runs as root, PID 1,
    before the privilege drop, as `#!/bin/bash` with no `PATH=`
    assignment anywhere and unqualified calls to `id`/`git`/`getent`/
    `useradd`/`mkdir`/`chown`/`chmod`/`setpriv` — every other key in
    `environment:` used to reach that phase unfiltered. Verified in a
    live container (this task's own write-up) that a hostile `PATH` or
    `BASH_ENV` there is root code execution, and — the sharper
    consequence — a `PATH` shadowing `id` to print anything but `0`
    skips the entrypoint's privilege-drop check entirely."""
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "environment": {key: "/tmp/evil"}})
    with pytest.raises(ContainerCreateFailedError, match="interpreter or command resolution"):
        load_project_config(repo_path)


def test_unknown_cache_entry_field_is_a_validation_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(
        repo_path,
        {"version": 1, "caches": [{"path": "$HOME/.cargo", "name": "cargo", "extra": "x"}]},
    )
    with pytest.raises(ContainerCreateFailedError):
        load_project_config(repo_path)


def test_invalid_yaml_is_a_clear_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".mcp-workspace.yml").write_text("not: valid: yaml: [unterminated")
    with pytest.raises(ContainerCreateFailedError, match="not valid YAML"):
        load_project_config(repo_path)


def test_at_hat011_a_secret_in_the_malformed_yaml_line_does_not_reach_the_client(
    tmp_path: Path,
) -> None:
    """HR-013 (`docs/HARDENING_SPEC.md` §8): PyYAML's own error message
    echoes the offending source line verbatim — a real path for a secret
    accidentally left in a malformed `.mcp-workspace.yml` to leak through
    `ContainerCreateFailedError`'s `message`, which `to_tool_error`
    redacts the same way `details` is."""
    from dev_workspace_mcp.errors import to_tool_error

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    # Short enough (24 chars past "ghp_") that PyYAML's own snippet
    # formatting doesn't split it across two truncated excerpts, which
    # would hide it before this test's own redaction is ever exercised —
    # verified empirically: PyYAML keeps a token whole up to ~28 chars
    # here, splits a 32-char one across "..." on both sides.
    token = "ghp_" + "B" * 24
    (repo_path / ".mcp-workspace.yml").write_text(f"x: [{token}")

    with pytest.raises(ContainerCreateFailedError) as excinfo:
        load_project_config(repo_path)

    payload = json.loads(str(to_tool_error(excinfo.value)))
    assert token not in payload["message"]


def test_non_mapping_top_level_is_a_clear_error(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".mcp-workspace.yml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ContainerCreateFailedError, match="must be a mapping"):
        load_project_config(repo_path)


def test_mcp_workspace_yml_takes_precedence_over_yaml_extension(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1, "image": "yml-wins"}, filename=".mcp-workspace.yml")
    _write(repo_path, {"version": 1, "image": "yaml-loses"}, filename=".mcp-workspace.yaml")
    config = load_project_config(repo_path)
    assert config is not None
    assert config.image == "yml-wins"


# --- devcontainer_json_is_ignored (§9/§30, H32) -------------------------------------


def test_devcontainer_json_is_ignored_is_false_when_absent(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    assert devcontainer_json_is_ignored(repo_path) is False


def test_devcontainer_json_is_ignored_is_true_when_present(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    (repo_path / ".devcontainer").mkdir(parents=True)
    (repo_path / ".devcontainer" / "devcontainer.json").write_text("{}\n")
    assert devcontainer_json_is_ignored(repo_path) is True


def test_devcontainer_json_is_ignored_alongside_a_configured_mcp_workspace_yml(
    tmp_path: Path,
) -> None:
    """§9's precedence list ranks `.mcp-workspace.yml` first; that does
    not make a co-present `devcontainer.json` stop being ignored."""
    repo_path = tmp_path / "repo"
    _write(repo_path, {"version": 1})
    (repo_path / ".devcontainer").mkdir(parents=True)
    (repo_path / ".devcontainer" / "devcontainer.json").write_text("{}\n")
    assert devcontainer_json_is_ignored(repo_path) is True
