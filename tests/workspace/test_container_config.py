"""S7: container identity — run_kwargs construction and the root-mode
refusal (§5.3, AT-016). Pure logic; no real Docker daemon needed, so a
mock stands in for `docker.DockerClient`."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dev_workspace_mcp.config.models import ContainerConfig, SSHConfig
from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.workspace.container_config import (
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
