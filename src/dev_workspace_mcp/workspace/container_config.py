"""Container identity: the default image (§9.1) and the per-workspace run
configuration that gives a container the checkout owner's identity (§5.3).

The image itself — `docker/default/Dockerfile` and `entrypoint.sh` — does
not bake in a uid. `entrypoint.sh` creates the container user at start,
from `MCP_UID`/`MCP_GID` set here, then drops from root to that user
before exec'ing the real command via `setpriv`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import docker
from dev_workspace_mcp.config.models import ContainerConfig
from dev_workspace_mcp.errors import ContainerCreateFailedError

DEFAULT_IMAGE_TAG = "mcp-dev-ubuntu:24.04"

#: `src/dev_workspace_mcp/workspace/container_config.py` -> repo root -> `docker/default`.
DEFAULT_IMAGE_BUILD_CONTEXT = Path(__file__).resolve().parents[3] / "docker" / "default"


def userns_remap_active(docker_client: docker.DockerClient) -> bool:
    """§5.3: whether the daemon has user-namespace remapping configured,
    so a container's uid 0 maps to an unprivileged host uid rather than
    host uid 0. `container.user: root` is refused without this."""
    info = docker_client.info()
    security_options = info.get("SecurityOptions") or []
    return any("userns" in option for option in security_options)


def build_workspace_run_kwargs(
    *,
    container_config: ContainerConfig,
    docker_client: docker.DockerClient,
    workspace_path: str = "/workspace",
    checkout_uid: int | None = None,
    checkout_gid: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the `run_kwargs` passed to `DockerWorkspaceManager.ensure_workspace`
    (and on to `docker.containers.run`) that give the container the
    identity §5.3 requires.

    `checkout_uid`/`checkout_gid` default to the daemon's own uid/gid — §40
    already requires the daemon to run as the checkout owner, so that is
    the correct default; a caller MAY override, which the S7 tests do to
    exercise a uid distinct from whichever account runs them.

    Raises `ContainerCreateFailedError` for `container.user: root` without
    active user-namespace remapping (§5.3, AT-016) — before any container
    is created, not after.
    """
    if container_config.user == "root":
        if not userns_remap_active(docker_client):
            raise ContainerCreateFailedError(
                "container.user: root requires Docker user-namespace remapping "
                "to be active; without it, container uid 0 is host uid 0 (§5.3)",
                details={"container_user": "root"},
            )
        environment = {"MCP_WORKSPACE_PATH": workspace_path, **(extra_env or {})}
    else:
        uid = os.getuid() if checkout_uid is None else checkout_uid
        gid = os.getgid() if checkout_gid is None else checkout_gid
        environment = {
            "MCP_UID": str(uid),
            "MCP_GID": str(gid),
            "MCP_SUDO": "true" if container_config.sudo else "false",
            "MCP_WORKSPACE_PATH": workspace_path,
            **(extra_env or {}),
        }

    return {
        "environment": environment,
        # §41: no-new-privileges blocks the setuid escalation sudo depends
        # on — never set it while sudo is enabled.
        "security_opt": [] if container_config.sudo else ["no-new-privileges:true"],
    }


def resolve_exec_user(container_config: ContainerConfig, *, checkout_uid: int | None = None) -> str:
    """The `user=` string for `docker exec` (§14, S12): the same uid
    convention `build_workspace_run_kwargs` gives the container at start —
    `entrypoint.sh` already created that `/etc/passwd` entry, which is
    what lets Docker resolve `$HOME` correctly for an exec'd process even
    though it does not inherit anything the entrypoint's shell `export`ed.
    Empty string (Docker's own default, uid 0) under `container.user: root`.
    """
    if container_config.user == "root":
        return ""
    uid = os.getuid() if checkout_uid is None else checkout_uid
    return str(uid)
