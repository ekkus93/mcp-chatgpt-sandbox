"""Load, parse, and validate the server configuration file (§52, §73 step 1).

Also provides `docker_available()`, used by the CLI for §73 step 2 (verify
Docker connectivity) so a missing daemon degrades rather than crashes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import DockerException
from pydantic import ValidationError

from dev_workspace_mcp.config.models import ServerConfig, WorkspaceConfig


class ConfigError(Exception):
    """The configuration file is missing, malformed, or violates an
    invariant (§19.1, §53). Raised only at startup, before the server
    accepts any request — distinct from the per-tool-call error taxonomy in
    §31, which S4 defines for request handling."""


def validate_config_mapping(raw: dict[str, Any], *, home: Path | None = None) -> ServerConfig:
    """Validate one parsed mapping with production semantics and no mutations.

    This is the side-effect-free authority used by interactive setup when it
    prepares a candidate for review.  It intentionally performs the same model
    and cross-field/path validation as :func:`load_config`, but does not call
    :func:`ensure_known_hosts_file_exists`, because merely previewing a candidate
    must never create or modify host files.
    """

    try:
        config = _parse(raw)
        _check_ssh_key_not_under_host_ssh(config, home=home or Path.home())
        _check_secrets_outside_repository_roots(config)
        _check_repository_roots_exist(config)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
    return config


def load_config(path: Path, *, home: Path | None = None) -> ServerConfig:
    """Load and fully validate a configuration file.

    `home` overrides the host user's home directory, for testing the §19.1
    invariant without touching the real one.
    """
    raw = _read_yaml(path)
    config = validate_config_mapping(raw, home=home)
    try:
        ensure_known_hosts_file_exists(config)
    except ConfigError:
        raise
    except Exception as exc:
        # Preserve load_config's historical fail-closed error boundary for the
        # final operational side effect as well as pure validation.
        raise ConfigError(f"invalid configuration: {exc}") from exc
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
        except Exception as exc:
            # H30 (`docs/HARDENING_TODO.md`, HR-010): a malformed value
            # shaped like `workspace: 5` (not a mapping at all) reaches
            # `dict(raw)` inside `from_yaml_value` and raises a raw
            # `TypeError`, not the `ValueError` the clause above already
            # handled — every config-load failure SHALL surface as
            # `ConfigError`, not an unhandled exception escaping
            # `load_config` (§73 step 1's own "refuse to serve on invalid
            # config," not "crash on invalid config").
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
    if config.ssh.shared_key is not None:
        key_path = config.ssh.keys_dir / config.ssh.shared_key
        _reject_if_under_host_ssh(key_path, what="ssh.shared_key")
    for repo in config.repositories:
        if repo.ssh_key is not None:
            key_path = config.ssh.keys_dir / repo.ssh_key
            _reject_if_under_host_ssh(key_path, what=f"repositories[{repo.id!r}].ssh_key")


def _check_secrets_outside_repository_roots(config: ServerConfig) -> None:
    """§53: sensitive operational paths must stay outside client-readable repos."""
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
    _reject_if_under_a_repository(config.audit.path, what="audit.path")
    _reject_if_under_a_repository(config.state_db_path, what="state_db_path")


def _check_repository_roots_exist(config: ServerConfig) -> None:
    """§73 step 7: validate repository roots."""
    for root in config.repository_roots:
        if not root.is_dir():
            raise ConfigError(
                f"repository_roots entry does not exist or is not a directory: {root}"
            )


GITHUB_SSH_HOST_KEYS = (
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1"
    "aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYf"
    "NqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z"
    "8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIE"
    "s4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0Th"
    "S/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=",
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQE"
    "ezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=",
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
)


def ensure_known_hosts_file_exists(config: ServerConfig) -> None:
    """Ensure the pinned known-hosts file exists before Docker bind mounting."""
    known_hosts = config.ssh.known_hosts
    if known_hosts.exists() and known_hosts.stat().st_size > 0:
        return
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("\n".join(GITHUB_SSH_HOST_KEYS) + "\n")
    known_hosts.chmod(0o644)


def docker_available() -> bool:
    """§73 step 2: verify Docker connectivity without crashing the daemon."""
    try:
        client = docker.from_env()
        try:
            return bool(client.ping())
        finally:
            client.close()
    except (DockerException, OSError):
        return False
