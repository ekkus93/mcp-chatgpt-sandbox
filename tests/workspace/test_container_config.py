"""S7: container identity — run_kwargs construction and the root-mode
refusal (§5.3, AT-016). Pure logic; no real Docker daemon needed, so a
mock stands in for `docker.DockerClient`."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_chatgpt_sandbox.config.models import ContainerConfig, SSHConfig
from mcp_chatgpt_sandbox.errors import ContainerCreateFailedError
from mcp_chatgpt_sandbox.workspace.container_config import (
    build_workspace_run_kwargs,
    userns_remap_active,
)

#: A minimal, valid SSHConfig — S7's tests only care about identity/env
#: construction, not SSH itself, but `ssh_config` is a required parameter
#: since S24 (it feeds MCP_SSH_MODE).
_SSH_CONFIG = SSHConfig(
    keys_dir=Path("/tmp/mcp-test-keys"), known_hosts=Path("/tmp/mcp-test-known-hosts")
)


def _fake_client(security_options: list[str]) -> MagicMock:
    client = MagicMock()
    client.info.return_value = {"SecurityOptions": security_options}
    return client


def test_userns_remap_active_true_when_daemon_advertises_it() -> None:
    client = _fake_client(["name=userns"])
    assert userns_remap_active(client) is True


def test_userns_remap_active_false_without_it() -> None:
    client = _fake_client(["name=apparmor", "name=seccomp,profile=builtin"])
    assert userns_remap_active(client) is False


def test_userns_remap_active_false_with_no_security_options_key() -> None:
    client = MagicMock()
    client.info.return_value = {}
    assert userns_remap_active(client) is False


def test_host_mode_sets_uid_gid_and_sudo_env() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=True)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1234,
        checkout_gid=5678,
    )
    assert kwargs["environment"]["MCP_UID"] == "1234"
    assert kwargs["environment"]["MCP_GID"] == "5678"
    assert kwargs["environment"]["MCP_SUDO"] == "true"
    assert kwargs["security_opt"] == []  # §41: no-new-privileges would break sudo


def test_host_mode_sudo_disabled_sets_no_new_privileges() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
    )
    assert kwargs["environment"]["MCP_SUDO"] == "false"
    assert kwargs["security_opt"] == ["no-new-privileges:true"]


def test_host_mode_defaults_to_the_daemons_own_uid_gid() -> None:
    import os

    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=True)
    kwargs = build_workspace_run_kwargs(
        container_config=config, docker_client=client, ssh_config=_SSH_CONFIG
    )
    assert kwargs["environment"]["MCP_UID"] == str(os.getuid())
    assert kwargs["environment"]["MCP_GID"] == str(os.getgid())


def test_host_mode_merges_extra_env() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=True)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"RUST_BACKTRACE": "1"},
    )
    assert kwargs["environment"]["RUST_BACKTRACE"] == "1"
    assert kwargs["environment"]["MCP_UID"] == "1000"


# --- HR-006 (docs/HARDENING_SPEC.md §6): ordering, independent of the reject filter --


def test_at_hat005_extra_env_cannot_override_the_daemon_computed_identity_by_ordering() -> None:
    """The second, independent HR-006 control: even if `extra_env`
    somehow contains a daemon-controlled key (the allowlist reject in
    `ProjectWorkspaceConfig` is meant to catch this before it gets this
    far — this test simulates that control being bypassed), merge
    ordering alone must still prevent it from winning."""
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"MCP_UID": "0", "MCP_GID": "0", "MCP_SUDO": "true"},
    )
    assert kwargs["environment"]["MCP_UID"] == "1000"
    assert kwargs["environment"]["MCP_GID"] == "1000"
    assert kwargs["environment"]["MCP_SUDO"] == "false"


def test_at_hat005_extra_env_cannot_override_ssh_auth_sock_by_ordering() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host")
    ssh_config = SSHConfig(
        keys_dir=Path("/tmp/mcp-test-keys"),
        known_hosts=Path("/tmp/mcp-test-known-hosts"),
        mode="agent",
    )
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=ssh_config,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"SSH_AUTH_SOCK": "/tmp/evil.sock"},
    )
    assert kwargs["environment"]["SSH_AUTH_SOCK"] != "/tmp/evil.sock"


def test_at_hat005_root_mode_extra_env_cannot_override_workspace_path() -> None:
    """The `container.user: root` branch merges `extra_env` separately —
    covered on its own, not only the non-root branch above."""
    client = _fake_client(["name=userns"])  # userns active, root permitted
    config = ContainerConfig(user="root")
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        extra_env={"MCP_WORKSPACE_PATH": "/evil"},
    )
    assert kwargs["environment"]["MCP_WORKSPACE_PATH"] == "/workspace"


def test_at_vat003_root_mode_extra_env_cannot_inject_daemon_controlled_keys() -> None:
    """VR-003 (`docs/REVIEW_SPEC.md` §4): the root branch used to set
    only `MCP_WORKSPACE_PATH`/`MCP_CACHE_PATHS`, so `MCP_UID`/`MCP_GID`/
    `MCP_SUDO`/`MCP_SSH_MODE`/`SSH_AUTH_SOCK` passed through from
    `extra_env` unopposed — the docstring's "ordering alone still stops
    a project-supplied `MCP_UID`/`MCP_GID`/`SSH_AUTH_SOCK`" claim was
    false in this branch, since the daemon never set them here for
    ordering to act on. `MCP_UID`/`MCP_GID`'s *absence* is what signals
    root mode to `entrypoint.sh` (`[ -z "${MCP_UID:-}" ]`) — there is no
    value that "means root mode" the way there is for the other keys, so
    the fix strips every daemon-controlled key from `extra_env` before
    either branch merges it, rather than copying the non-root branch's
    values across."""
    client = _fake_client(["name=userns"])  # userns active, root permitted
    config = ContainerConfig(user="root")
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        extra_env={
            "MCP_UID": "0",
            "MCP_GID": "0",
            "MCP_USER": "evil",
            "MCP_SUDO": "true",
            "MCP_SSH_MODE": "file",
            "SSH_AUTH_SOCK": "/evil",
        },
    )
    environment = kwargs["environment"]
    assert "MCP_UID" not in environment
    assert "MCP_GID" not in environment
    assert "MCP_USER" not in environment
    assert "MCP_SUDO" not in environment
    assert "MCP_SSH_MODE" not in environment
    assert "SSH_AUTH_SOCK" not in environment


def test_at_vat003_host_mode_extra_env_cannot_inject_mcp_user() -> None:
    """`MCP_USER` is never daemon-set in either branch (entrypoint.sh's
    own default, "mcpuser", is never overridden anywhere in this
    codebase — `container_config.py`'s own module docstring says so) —
    but that is a reason to strip it from `extra_env`, not a reason it
    was safe to leave unprotected by ordering."""
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"MCP_USER": "evil"},
    )
    assert "MCP_USER" not in kwargs["environment"]


# --- RR-031 (docs/REMEDIATION_SPEC.md §32): MCP_CACHE_PATHS gets the same
# ordering guarantee as every other daemon-controlled variable above -----------


def test_rr031_cache_paths_are_joined_into_mcp_cache_paths() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        cache_paths=[".cargo", ".rustup"],
    )
    assert kwargs["environment"]["MCP_CACHE_PATHS"] == ".cargo:.rustup"


def test_rr031_no_cache_paths_sets_an_empty_string_not_a_missing_key() -> None:
    """Entrypoint.sh treats an empty `MCP_CACHE_PATHS` identically to an
    unset one, and an always-present key is what lets the ordering
    guarantee below hold even when a project has no caches configured at
    all — a key merged in only when `cache_paths` is truthy would leave
    nothing to override a hostile `extra_env` entry with in that case."""
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
    )
    assert kwargs["environment"]["MCP_CACHE_PATHS"] == ""


def test_rr031_extra_env_cannot_override_mcp_cache_paths_by_ordering() -> None:
    """RR-031: `MCP_CACHE_PATHS` used to reach `environment` only through
    the caller's own `extra_env`, which this function merges FIRST —
    ordering gave it none of the protection `MCP_UID`/`MCP_GID`/
    `SSH_AUTH_SOCK` get from `test_at_hat005_*` above. Simulates the same
    "allowlist reject bypassed" scenario those tests do."""
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"MCP_CACHE_PATHS": "../../etc"},
        cache_paths=[".cargo"],
    )
    assert kwargs["environment"]["MCP_CACHE_PATHS"] == ".cargo"


def test_rr031_extra_env_cannot_inject_mcp_cache_paths_when_none_are_configured() -> None:
    """The same bypass scenario, but with the daemon itself resolving no
    caches at all — the hostile `extra_env` entry must still lose, not
    survive because there was nothing to override it with."""
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=False)
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        checkout_uid=1000,
        checkout_gid=1000,
        extra_env={"MCP_CACHE_PATHS": "../../etc"},
    )
    assert kwargs["environment"]["MCP_CACHE_PATHS"] == ""


def test_rr031_root_mode_extra_env_cannot_override_mcp_cache_paths() -> None:
    """The `container.user: root` branch merges `extra_env` separately —
    covered on its own, same as `test_at_hat005_root_mode_*` above."""
    client = _fake_client(["name=userns"])  # userns active, root permitted
    config = ContainerConfig(user="root")
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=_SSH_CONFIG,
        extra_env={"MCP_CACHE_PATHS": "../../etc"},
        cache_paths=[".cargo"],
    )
    assert kwargs["environment"]["MCP_CACHE_PATHS"] == ".cargo"


def test_root_mode_without_userns_remap_is_refused() -> None:
    """§5.3, AT-016: refused before any container is created."""
    client = _fake_client(["name=apparmor"])  # no userns
    config = ContainerConfig(user="root", sudo=True)
    with pytest.raises(ContainerCreateFailedError) as excinfo:
        build_workspace_run_kwargs(
            container_config=config, docker_client=client, ssh_config=_SSH_CONFIG
        )
    assert excinfo.value.details == {"container_user": "root"}
    client.images.build.assert_not_called()
    client.containers.run.assert_not_called()


def test_root_mode_with_userns_remap_is_allowed() -> None:
    client = _fake_client(["name=userns"])
    config = ContainerConfig(user="root", sudo=True)
    kwargs = build_workspace_run_kwargs(
        container_config=config, docker_client=client, ssh_config=_SSH_CONFIG
    )
    assert "MCP_UID" not in kwargs["environment"]
    assert "MCP_GID" not in kwargs["environment"]


def test_host_mode_sets_mcp_ssh_mode_from_config() -> None:
    client = _fake_client([])
    config = ContainerConfig(user="host", sudo=True)
    file_mode_ssh_config = SSHConfig(
        mode="file",
        keys_dir=Path("/tmp/mcp-test-keys"),
        known_hosts=Path("/tmp/mcp-test-known-hosts"),
    )
    kwargs = build_workspace_run_kwargs(
        container_config=config,
        docker_client=client,
        ssh_config=file_mode_ssh_config,
        checkout_uid=1000,
        checkout_gid=1000,
    )
    assert kwargs["environment"]["MCP_SSH_MODE"] == "file"
