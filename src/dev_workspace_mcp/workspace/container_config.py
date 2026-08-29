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
from dev_workspace_mcp.config.models import ContainerConfig, ResourcesConfig, SSHConfig
from dev_workspace_mcp.errors import ContainerCreateFailedError

#: docker-py's own `nano_cpus` unit (§26): one whole CPU is 1e9 nanocpus.
_NANO_CPUS_PER_CPU = 1_000_000_000

DEFAULT_IMAGE_TAG = "mcp-dev-ubuntu:24.04"

#: `src/dev_workspace_mcp/workspace/container_config.py` -> repo root -> `docker/default`.
DEFAULT_IMAGE_BUILD_CONTEXT = Path(__file__).resolve().parents[3] / "docker" / "default"

#: §19.3: where the pinned GitHub host-key file is mounted inside every
#: workspace container — fixed, outside the working tree, never inside
#: the bind mount. `entrypoint.sh` points `UserKnownHostsFile` at this
#: same literal path; there is no shared-constant mechanism between
#: Python and a shell script, so keep the two in sync by hand.
SSH_KNOWN_HOSTS_CONTAINER_PATH = "/etc/mcp/ssh/known_hosts"

#: §19.2 `ssh.mode: agent`: the fixed container-side path the per-workspace
#: agent's host socket is bind-mounted at, matching `SSH_AUTH_SOCK` below.
SSH_AGENT_SOCKET_CONTAINER_PATH = "/run/mcp/ssh/agent.sock"

#: `entrypoint.sh`'s own default (`MCP_USER="${MCP_USER:-mcpuser}"`, never
#: overridden anywhere in this codebase) — `useradd -m` creates its home
#: at exactly this path. `workspace/caches.py` (§11, S30) needs to compute
#: a cache mount's container-side target *before* the container (and
#: therefore its actual home directory) exists, so this is the one place
#: that convention is written down for Python to rely on too.
DEFAULT_CONTAINER_HOME = "/home/mcpuser"

#: HR-006 / VR-003 (`docs/REVIEW_SPEC.md` §4): every environment variable
#: `entrypoint.sh`'s own contract (this module's docstring) treats as
#: daemon-controlled — stripped from a caller's `extra_env` before either
#: branch of `build_workspace_run_kwargs` merges it, regardless of
#: whether that branch goes on to set its own value for a given key.
#: `MCP_USER` is included even though no branch ever daemon-sets it:
#: `entrypoint.sh` still reads it (default `"mcpuser"`), so an
#: unprotected project-supplied value would be exactly the kind of
#: daemon-controlled-variable injection this set exists to block, even
#: though no branch below "restores" a correct value for it afterward.
_DAEMON_CONTROLLED_ENV_KEYS = frozenset(
    {
        "MCP_UID",
        "MCP_GID",
        "MCP_USER",
        "MCP_SUDO",
        "MCP_WORKSPACE_PATH",
        "MCP_SSH_MODE",
        "MCP_CACHE_PATHS",
        "SSH_AUTH_SOCK",
    }
)


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
    ssh_config: SSHConfig,
    workspace_path: str = "/workspace",
    checkout_uid: int | None = None,
    checkout_gid: int | None = None,
    extra_env: dict[str, str] | None = None,
    resources_config: ResourcesConfig | None = None,
    cache_paths: list[str] | None = None,
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

    `MCP_SSH_MODE` (§19.3, S24) tells `entrypoint.sh` whether to write an
    `IdentityFile` line into the container-local SSH config — only
    meaningful under `container.user: root`'s *non*-root sibling branch,
    since entrypoint.sh's user-creation step (where the SSH config gets
    written) never runs at all for `container.user: root` (no `MCP_UID`/
    `MCP_GID` to create a user from). Root-mode SSH configuration is out
    of this task's scope.

    `cache_paths` (§11, S30) is the caller's already-resolved cache
    entries' `$HOME`-relative paths (`caches.py`'s `ResolvedCache.
    home_relative_path`) — not accepted through `extra_env` even though
    it, like `environment:`, is a per-project value, because RR-031
    (`docs/REMEDIATION_SPEC.md` §32) requires it get the same
    ordering guarantee `MCP_UID`/`MCP_GID`/etc. get below.
    """
    # HR-006 (`docs/HARDENING_SPEC.md` §6) / RR-031 / VR-003
    # (`docs/REVIEW_SPEC.md` §4): `extra_env` has every daemon-controlled
    # key stripped *before* either branch below merges it — not merged
    # first and overridden last, the way this comment used to describe.
    # Ordering-alone override only protects a key the branch that runs
    # goes on to set explicitly, and the root branch does not set
    # `MCP_UID`/`MCP_GID`/`MCP_SUDO`/`MCP_SSH_MODE`/`SSH_AUTH_SOCK` at
    # all — `MCP_UID`/`MCP_GID` in particular have no "correct value" to
    # override with in root mode, since their *absence* (not any
    # particular value) is what `entrypoint.sh`'s
    # `[ -z "${MCP_UID:-}" ]` reads as "run as root" (§5.3). Stripping
    # first makes both branches correct by construction: a key neither
    # branch re-adds simply stays absent, regardless of what `extra_env`
    # tried to inject. `MCP_USER` is included even though no branch ever
    # daemon-sets it (`entrypoint.sh`'s own default, "mcpuser", is never
    # overridden anywhere in this codebase, per this module's own
    # docstring) — exactly why it needs stripping instead of overriding:
    # there is no branch-side value that would have protected it before.
    #
    # `MCP_CACHE_PATHS` is *always* set in both branches (`""` when
    # there are no caches, matching what leaving it unset means to
    # `entrypoint.sh`) rather than only when `cache_paths` is non-empty —
    # an unconditional override is what actually gives it the same
    # guarantee as `MCP_UID`/`MCP_GID`, which are never conditionally
    # omitted either; merging it in only when truthy would leave a
    # project with no caches configured today with no override at all if
    # `extra_env` carried a value regardless.
    stripped_extra_env = {
        key: value
        for key, value in (extra_env or {}).items()
        if key not in _DAEMON_CONTROLLED_ENV_KEYS
    }
    cache_env = {"MCP_CACHE_PATHS": ":".join(cache_paths) if cache_paths else ""}
    if container_config.user == "root":
        if not userns_remap_active(docker_client):
            raise ContainerCreateFailedError(
                "container.user: root requires Docker user-namespace remapping "
                "to be active; without it, container uid 0 is host uid 0 (§5.3)",
                details={"container_user": "root"},
            )
        environment = {
            **stripped_extra_env,
            "MCP_WORKSPACE_PATH": workspace_path,
            **cache_env,
        }
    else:
        uid = os.getuid() if checkout_uid is None else checkout_uid
        gid = os.getgid() if checkout_gid is None else checkout_gid
        environment = {
            **stripped_extra_env,
            "MCP_UID": str(uid),
            "MCP_GID": str(gid),
            "MCP_SUDO": "true" if container_config.sudo else "false",
            "MCP_WORKSPACE_PATH": workspace_path,
            "MCP_SSH_MODE": ssh_config.mode,
            **(
                {"SSH_AUTH_SOCK": SSH_AGENT_SOCKET_CONTAINER_PATH}
                if ssh_config.mode == "agent"
                else {}
            ),
            **cache_env,
        }

    run_kwargs: dict[str, Any] = {
        "environment": environment,
        # §41: no-new-privileges blocks the setuid escalation sudo depends
        # on — never set it while sudo is enabled.
        "security_opt": [] if container_config.sudo else ["no-new-privileges:true"],
    }

    # §26: every field `None` (the default `ResourcesConfig`) means no
    # limit — omitted entirely rather than passed as `None`, so Docker's
    # own unbounded default applies exactly as if `resources:` were never
    # configured at all.
    if resources_config is not None:
        if resources_config.cpus is not None:
            run_kwargs["nano_cpus"] = int(resources_config.cpus * _NANO_CPUS_PER_CPU)
        if resources_config.memory is not None:
            run_kwargs["mem_limit"] = resources_config.memory
        if resources_config.pids_limit is not None:
            run_kwargs["pids_limit"] = resources_config.pids_limit

    return run_kwargs


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
