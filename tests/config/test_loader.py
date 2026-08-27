"""S2: config loading, startup-time validation, and the two config-time
invariants — ssh_key never under the host's own .ssh (§19.1), secrets never
under a repository root or path (§53)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from dev_workspace_mcp.config.loader import ConfigError, docker_available, load_config


def _write_config(path: Path, data: dict[str, Any]) -> Path:
    config_path = path / "config.yml"
    config_path.write_text(yaml.safe_dump(data))
    return config_path


def test_load_config_happy_path(tmp_path: Path, minimal_config_dict: dict[str, Any]) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)
    config = load_config(config_path, home=tmp_path / "home")
    assert config.docker.default_image == "mcp-dev-ubuntu:24.04"


def test_load_config_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config file"):
        load_config(tmp_path / "does-not-exist.yml")


def test_load_config_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("not: valid: yaml: [unterminated")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(config_path)


def test_load_config_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(config_path)


def test_load_config_rejects_schema_violations(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    del minimal_config_dict["docker"]  # required field missing
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(config_path, home=tmp_path / "home")


def test_load_config_parses_idle_stop_after(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    minimal_config_dict["workspace"] = {"idle_stop_after": "2h"}
    config_path = _write_config(tmp_path, minimal_config_dict)
    config = load_config(config_path, home=tmp_path / "home")
    assert config.workspace.idle_stop_after_seconds == 7200


def test_load_config_rejects_malformed_idle_stop_after(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    minimal_config_dict["workspace"] = {"idle_stop_after": "not-a-duration"}
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="workspace:"):
        load_config(config_path, home=tmp_path / "home")


# --- §19.1: the container's key is never the human's own ------------------


def test_ssh_keys_dir_under_host_ssh_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    home = tmp_path / "home"
    minimal_config_dict["ssh"]["keys_dir"] = str(home / ".ssh" / "mcp-keys")
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match=r"resolves under the host user's own \.ssh"):
        load_config(config_path, home=home)


def test_repository_ssh_key_under_host_ssh_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    home = tmp_path / "home"
    # keys_dir itself is fine, but a symlink-free path composition that
    # walks back into ~/.ssh via the repository's ssh_key must still be
    # caught — model it directly by pointing keys_dir at ~/.ssh's parent
    # and letting the per-repo key resolve inside it.
    minimal_config_dict["ssh"]["keys_dir"] = str(home)
    minimal_config_dict["repositories"] = [
        {
            "id": "example",
            "path": str(tmp_path / "work" / "example"),
            "github": "owner/example",
            "ssh_key": ".ssh/id_ed25519",
        }
    ]
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match=r"repositories\['example'\]\.ssh_key"):
        load_config(config_path, home=home)


def test_repository_ssh_key_outside_host_ssh_is_accepted(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    home = tmp_path / "home"
    minimal_config_dict["repositories"] = [
        {
            "id": "example",
            "path": str(tmp_path / "work" / "example"),
            "github": "owner/example",
            "ssh_key": "example.ed25519",
        }
    ]
    config_path = _write_config(tmp_path, minimal_config_dict)
    config = load_config(config_path, home=home)
    assert config.repositories[0].ssh_key == "example.ed25519"


def test_at_hat006_shared_ssh_key_under_host_ssh_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    """A real regression: `ssh.shared_key` (used under `scope: shared`)
    was never checked, so a value resolving under the host user's own
    `.ssh` — the human's personal key — was accepted."""
    home = tmp_path / "home"
    minimal_config_dict["ssh"]["keys_dir"] = str(home)
    minimal_config_dict["ssh"]["scope"] = "shared"
    minimal_config_dict["ssh"]["shared_key"] = ".ssh/id_ed25519"
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match=r"ssh\.shared_key"):
        load_config(config_path, home=home)


def test_at_hat006_shared_ssh_key_outside_host_ssh_is_accepted(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    home = tmp_path / "home"
    minimal_config_dict["ssh"]["scope"] = "shared"
    minimal_config_dict["ssh"]["shared_key"] = "shared.ed25519"
    config_path = _write_config(tmp_path, minimal_config_dict)
    config = load_config(config_path, home=home)
    assert config.ssh.shared_key == "shared.ed25519"


def test_every_ssh_config_key_bearing_field_is_checked() -> None:
    """HR-007's own structural guarantee: this test — not memory — is
    what makes "a new key-bearing field fails a test rather than passing
    silently" true. It fails the moment a field is added to `SSHConfig`
    or `RepositoryConfig` without a corresponding decision recorded in
    the two sets below (and, for a key-bearing field, a matching check
    added to `_check_ssh_key_not_under_host_ssh`)."""
    from dev_workspace_mcp.config.models import RepositoryConfig, SSHConfig

    checked_ssh_fields = {"keys_dir", "shared_key"}
    known_non_key_bearing_ssh_fields = {"mode", "scope", "known_hosts"}
    assert set(SSHConfig.model_fields) == checked_ssh_fields | known_non_key_bearing_ssh_fields

    checked_repository_fields = {"ssh_key"}
    known_non_key_bearing_repository_fields = {"id", "path", "github"}
    assert (
        set(RepositoryConfig.model_fields)
        == checked_repository_fields | known_non_key_bearing_repository_fields
    )


# --- §53: secrets never live under a repository root or path ---------------


def test_ssh_keys_dir_under_repository_root_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    root = tmp_path / "work"
    minimal_config_dict["repository_roots"] = [str(root)]
    minimal_config_dict["ssh"]["keys_dir"] = str(root / "secrets" / "keys")
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="lies inside a repository root"):
        load_config(config_path, home=tmp_path / "home")


def test_ssh_keys_dir_under_repository_path_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    repo_path = tmp_path / "work" / "example"
    minimal_config_dict["repositories"] = [
        {"id": "example", "path": str(repo_path), "github": "owner/example"}
    ]
    minimal_config_dict["ssh"]["keys_dir"] = str(repo_path / ".mcp-keys")
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="lies inside a repository root"):
        load_config(config_path, home=tmp_path / "home")


def test_github_api_token_path_under_repository_root_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    root = tmp_path / "work"
    minimal_config_dict["repository_roots"] = [str(root)]
    minimal_config_dict["github"] = {"api_token_path": str(root / "token")}
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="github.api_token_path"):
        load_config(config_path, home=tmp_path / "home")


def test_secrets_outside_every_repository_root_are_accepted(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    (tmp_path / "work").mkdir()
    minimal_config_dict["repository_roots"] = [str(tmp_path / "work")]
    config_path = _write_config(tmp_path, minimal_config_dict)
    # ssh.keys_dir already lives under tmp_path/keys in the fixture, which
    # is a sibling of tmp_path/work, not beneath it.
    config = load_config(config_path, home=tmp_path / "home")
    assert config.ssh.keys_dir == tmp_path / "keys"


# --- H30 (`docs/HARDENING_TODO.md`, HR-010): audit.path/state_db_path extend
# the §53 secrets-outside-repository check -----------------------------------


def test_audit_path_under_repository_root_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    minimal_config_dict["repository_roots"] = [str(root)]
    minimal_config_dict["audit"]["path"] = str(root / "audit.jsonl")
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="audit.path"):
        load_config(config_path, home=tmp_path / "home")


def test_state_db_path_under_repository_root_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    minimal_config_dict["repository_roots"] = [str(root)]
    minimal_config_dict["state_db_path"] = str(root / "state.db")
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="state_db_path"):
        load_config(config_path, home=tmp_path / "home")


# --- H30 (`docs/HARDENING_TODO.md`, HR-010): §73 step 7 — repository roots
# validated for existence at startup -------------------------------------------


def test_repository_roots_entry_that_does_not_exist_is_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    """A real gap: `RepositoryRegistry`'s own discovery silently yields
    nothing for a nonexistent `repository_roots` entry — an operator with
    a typo'd or since-removed root was never told anything was wrong."""
    minimal_config_dict["repository_roots"] = [str(tmp_path / "does-not-exist")]
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="repository_roots entry does not exist"):
        load_config(config_path, home=tmp_path / "home")


def test_repository_roots_entry_that_is_a_file_not_a_directory_is_rejected(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    not_a_dir = tmp_path / "not-a-directory"
    not_a_dir.write_text("")
    minimal_config_dict["repository_roots"] = [str(not_a_dir)]
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="repository_roots entry does not exist"):
        load_config(config_path, home=tmp_path / "home")


def test_repository_roots_entry_that_exists_is_accepted(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    minimal_config_dict["repository_roots"] = [str(root)]
    config_path = _write_config(tmp_path, minimal_config_dict)
    config = load_config(config_path, home=tmp_path / "home")
    assert config.repository_roots == [root]


# --- H30 (`docs/HARDENING_TODO.md`, HR-010): every config-load failure
# surfaces as ConfigError, never an unhandled exception -----------------------


def test_workspace_as_a_scalar_raises_config_error_not_a_raw_type_error(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    """A real regression: `workspace: 5` (a scalar, not a mapping) reached
    `WorkspaceConfig.from_yaml_value`'s own `dict(raw)` and raised a bare
    `TypeError`, not the `ValueError` the surrounding `except` clause
    already handled — escaping `load_config` entirely, past §73 step 1's
    "refuse to serve on invalid config"."""
    minimal_config_dict["workspace"] = 5
    config_path = _write_config(tmp_path, minimal_config_dict)
    with pytest.raises(ConfigError, match="workspace"):
        load_config(config_path, home=tmp_path / "home")


# --- Docker connectivity probe (§73 step 2) ---------------------------------


def test_docker_available_never_raises() -> None:
    """Whatever the sandbox's Docker state, this must return a bool rather
    than propagate — a missing daemon degrades the health report, it does
    not crash startup (§73)."""
    assert isinstance(docker_available(), bool)


# --- known_hosts must exist before the first bind mount (§19.3, S24) --------------


def test_load_config_creates_a_missing_known_hosts_file(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    config_path = _write_config(tmp_path, minimal_config_dict)
    known_hosts = Path(minimal_config_dict["ssh"]["known_hosts"])
    assert not known_hosts.exists()

    config = load_config(config_path, home=tmp_path / "home")

    assert config.ssh.known_hosts.exists()
    assert config.ssh.known_hosts.is_file()
    assert config.ssh.known_hosts.read_text() == ""


def test_load_config_leaves_an_existing_known_hosts_file_untouched(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    known_hosts = Path(minimal_config_dict["ssh"]["known_hosts"])
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("github.com ssh-ed25519 AAAA...\n")
    config_path = _write_config(tmp_path, minimal_config_dict)

    config = load_config(config_path, home=tmp_path / "home")

    assert config.ssh.known_hosts.read_text() == "github.com ssh-ed25519 AAAA...\n"


def test_load_config_creates_missing_known_hosts_parent_directories(
    tmp_path: Path, minimal_config_dict: dict[str, Any]
) -> None:
    minimal_config_dict["ssh"]["known_hosts"] = str(tmp_path / "deep" / "nested" / "known_hosts")
    config_path = _write_config(tmp_path, minimal_config_dict)

    config = load_config(config_path, home=tmp_path / "home")

    assert config.ssh.known_hosts.exists()
