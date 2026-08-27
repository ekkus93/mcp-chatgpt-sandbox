"""Load, parse, and validate the server configuration file (§52, §73 step 1).

Also provides `docker_available()`, used by the CLI for §73 step 2 (verify
Docker connectivity) so a missing daemon degrades rather than crashes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docker.errors import DockerException
from pydantic import ValidationError

import docker
from dev_workspace_mcp.config.models import ServerConfig, WorkspaceConfig


class ConfigError(Exception):
    """The configuration file is missing, malformed, or violates an
    invariant (§19.1, §53). Raised only at startup, before the server
    accepts any request — distinct from the per-tool-call error taxonomy in
    §31, which S4 defines for request handling."""


def load_config(path: Path, *, home: Path | None = None) -> ServerConfig:
    """Load and fully validate a configuration file.

    `home` overrides the host user's home directory, for testing the §19.1
    invariant without touching the real one.
    """
    raw = _read_yaml(path)
    config = _parse(raw)
    _check_ssh_key_not_under_host_ssh(config, home=home or Path.home())
    _check_secrets_outside_repository_roots(config)
    ensure_known_hosts_file_exists(config)
    return config


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def _parse(raw: dict[str, Any]) -> ServerConfig:
    data = dict(raw)
    # `workspace.idle_stop_after` is authored as a duration string but the
    # model stores it parsed (§8.1) — convert before handing the rest to
    # pydantic, which does not know about duration syntax.
    if "workspace" in data:
        try:
            data["workspace"] = WorkspaceConfig.from_yaml_value(data["workspace"])
        except ValueError as exc:
            raise ConfigError(f"workspace: {exc}") from exc

    try:
        return ServerConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{exc}") from exc


def _check_ssh_key_not_under_host_ssh(config: ServerConfig, *, home: Path) -> None:
    """§19.1: the daemon SHALL refuse to provision a key file that resolves
    under the host user's own `.ssh` directory. Checked at config-load time
    so a misconfiguration is caught before anything runs, not discovered
    when a workspace tries to provision the key."""
    host_ssh = (home / ".ssh").resolve()

    def _reject_if_under_host_ssh(candidate: Path, *, what: str) -> None:
        resolved = candidate.resolve()
        if resolved == host_ssh or resolved.is_relative_to(host_ssh):
            raise ConfigError(
                f"{what} resolves under the host user's own .ssh directory "
                f"({host_ssh}): {resolved}. The container's key SHALL never be "
                f"the human's personal key (§19.1)."
            )

    _reject_if_under_host_ssh(config.ssh.keys_dir, what="ssh.keys_dir")
    for repo in config.repositories:
        if repo.ssh_key is not None:
            key_path = config.ssh.keys_dir / repo.ssh_key
            _reject_if_under_host_ssh(key_path, what=f"repositories[{repo.id!r}].ssh_key")


def _check_secrets_outside_repository_roots(config: ServerConfig) -> None:
    """§53: secrets SHALL NOT live in project configuration committed to
    Git — checked here as "no secret-holding path is under any repository
    root or any individual repository's path," since either would place the
    secret inside a Git working tree the client can read and potentially
    write through the tools in §12."""
    protected_roots = list(config.repository_roots) + [r.path for r in config.repositories]
    resolved_roots = [r.resolve() for r in protected_roots]

    def _reject_if_under_a_repository(candidate: Path, *, what: str) -> None:
        resolved = candidate.resolve()
        for root in resolved_roots:
            if resolved == root or resolved.is_relative_to(root):
                raise ConfigError(
                    f"{what} ({resolved}) lies inside a repository root or "
                    f"repository path ({root}). Secrets SHALL NOT live under a "
                    f"path the filesystem tools or a container can read (§53)."
                )

    _reject_if_under_a_repository(config.ssh.keys_dir, what="ssh.keys_dir")
    if config.github.api_token_path is not None:
        _reject_if_under_a_repository(config.github.api_token_path, what="github.api_token_path")


def ensure_known_hosts_file_exists(config: ServerConfig) -> None:
    """§19.3: `ssh.known_hosts` gets bind-mounted read-only into every
    workspace container. Docker turns a bind mount of a *missing* host
    file into an empty *directory* at the target path instead — silently
    breaking `UserKnownHostsFile` — so a real file needs to exist before
    the first mount, not be created lazily per workspace. An empty file is
    the correct starting point regardless, not merely a convenient one:
    `StrictHostKeyChecking yes` against an empty known_hosts trusts
    nothing at all, which is the safe side of "SHALL NOT rely on
    trust-on-first-use" until an operator pins GitHub's real host keys
    into it."""
    if not config.ssh.known_hosts.exists():
        config.ssh.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        config.ssh.known_hosts.touch(mode=0o644)


def docker_available() -> bool:
    """§73 step 2: verify Docker connectivity. Returns False rather than
    raising on any failure (daemon down, permission denied, socket
    missing) — the caller reports a degraded health state instead of
    crashing the daemon (§73)."""
    try:
        client = docker.from_env()
        try:
            return bool(client.ping())
        finally:
            client.close()
    except (DockerException, OSError):
        return False
