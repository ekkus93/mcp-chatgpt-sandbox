"""Load, parse, and validate the server configuration file (§52, §73 step 1).

Also provides `docker_available()`, used by the CLI for §73 step 2 (verify
Docker connectivity) so a missing daemon degrades rather than crashes.
"""

from __future__ import annotations

from collections.abc import Mapping
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


def load_config(path: Path, *, home: Path | None = None) -> ServerConfig:
    """Load and fully validate a configuration file.

    `home` overrides the host user's home directory, for testing the §19.1
    invariant without touching the real one.
    """
    raw = _read_yaml(path)
    try:
        config = parse_config_mapping(raw)
        _check_ssh_key_not_under_host_ssh(config, home=home or Path.home())
        _check_secrets_outside_repository_roots(config)
        _check_repository_roots_exist(config)
        ensure_known_hosts_file_exists(config)
    except ConfigError:
        raise
    except Exception as exc:
        # H30 (`docs/HARDENING_TODO.md`, HR-010): the final safety net —
        # a real regression had `workspace: 5` (a scalar, not a mapping)
        # reach `parse_config_mapping`'s own `dict(raw)` and raise a bare
        # `TypeError` straight out of `load_config`, past §73 step 1's
        # "refuse to serve on invalid config" and into the CLI as an
        # unhandled crash instead of the clean `error: ...` message every
        # other malformed-config shape produces. `parse_config_mapping`
        # now converts that one specific shape itself (a better,
        # "workspace: ..."-prefixed message); this catches anything else,
        # from any step above.
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


def parse_config_mapping(raw: Mapping[str, Any]) -> ServerConfig:
    """Apply the production YAML-to-model conversion to an in-memory mapping.

    Interactive setup uses this to validate a reviewed candidate without
    duplicating configuration semantics. Filesystem/runtime invariants that
    require the final path remain the responsibility of :func:`load_config`
    after the candidate is atomically written.
    """
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
    # HR-007 (`docs/HARDENING_SPEC.md` §6): every configuration path that
    # can name a key file, not only the per-repository one. A real
    # regression: `ssh.shared_key` (used under `scope: shared` — §19.1's
    # own "one key installed on every configured repository" mode) was
    # never checked here, and a value like `"../fakehome/.ssh/id_ed25519"`
    # resolved straight to the host user's personal key.
    # `tests/config/test_loader.py`'s
    # `test_every_ssh_config_key_bearing_field_is_checked` is this
    # function's other half: it fails, on its own, the moment a new
    # key-bearing field is added to either `SSHConfig` or
    # `RepositoryConfig` without a matching line added here.
    if config.ssh.shared_key is not None:
        key_path = config.ssh.keys_dir / config.ssh.shared_key
        _reject_if_under_host_ssh(key_path, what="ssh.shared_key")
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
    # H30 (`docs/HARDENING_TODO.md`, HR-010): `audit.path`/`state_db_path`
    # are not credential files themselves, but they hold the same class
    # of information §53 exists to keep off a client-readable path — an
    # audit log records command argv (best-effort redacted, not a
    # guarantee — HR-013), and the state DB is the daemon's own internal
    # state. Neither belongs inside a repository the filesystem tools or
    # a container can read any more than an SSH key or API token does.
    _reject_if_under_a_repository(config.audit.path, what="audit.path")
    _reject_if_under_a_repository(config.state_db_path, what="state_db_path")


def _check_repository_roots_exist(config: ServerConfig) -> None:
    """§73 step 7: validate repository roots. `RepositoryRegistry`'s own
    discovery (`repositories.py`'s `_discover_under_root`) silently
    yields nothing for a `repository_roots` entry that doesn't exist —
    correct there (an unrelated request has no business failing over an
    unrelated discovery root), but that leaves an operator with a typo'd
    or since-removed root never told anything is wrong: repositories
    they expected to be auto-discovered just aren't, with no error and
    no warning. Checked once, loudly, at startup instead."""
    for root in config.repository_roots:
        if not root.is_dir():
            raise ConfigError(
                f"repository_roots entry does not exist or is not a directory: {root}"
            )


#: §19.3: "`known_hosts_mcp` SHALL contain GitHub's published SSH host
#: keys, pinned. The daemon SHALL NOT rely on trust-on-first-use." — H32
#: (`docs/HARDENING_SPEC.md` §9's §26/known_hosts row): before this, the
#: file was created *empty*, satisfying neither sentence — trust-on-
#: first-use was avoided only by trusting *nothing*, which a fresh
#: install could never get past (every SSH connection to GitHub failed
#: host verification, not just lacked a credential).
#:
#: These three lines are GitHub's own currently-published host keys,
#: verified two independent ways before being pinned here: a live
#: `ssh-keyscan -t rsa,ecdsa,ed25519 github.com` against the real
#: internet, and GitHub's own published fingerprint documentation
#: (https://docs.github.com/en/authentication/keeping-your-account-and-
#: data-secure/githubs-ssh-key-fingerprints) — both matching exactly.
#: Should GitHub ever rotate these, an operator's own `known_hosts`
#: content (below, never overwritten once the file has any content)
#: always takes precedence over this seed.
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
    """§19.3: `ssh.known_hosts` gets bind-mounted read-only into every
    workspace container. Docker turns a bind mount of a *missing* host
    file into an empty *directory* at the target path instead — silently
    breaking `UserKnownHostsFile` — so a real file needs to exist before
    the first mount, not be created lazily per workspace.

    Seeded with `GITHUB_SSH_HOST_KEYS` whenever the file is genuinely
    empty — whether it does not exist yet (a fresh install) or exists
    with zero bytes (an install from before this task, when this
    function only ever `touch`ed an empty file and never wrote to it
    again). Both cases can only be this daemon's own doing: nothing
    upstream of this call ever writes to `ssh.known_hosts` with content
    and then leaves it at zero bytes on purpose. A file with *any*
    existing content, by contrast, is never touched — it is either an
    operator's own deliberately customized `known_hosts` (additional
    keys, a GitHub Enterprise Server host, intentionally different
    content) or this same seed from an earlier run, and either way
    overwriting it silently would be the wrong call."""
    known_hosts = config.ssh.known_hosts
    if known_hosts.exists() and known_hosts.stat().st_size > 0:
        return
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("\n".join(GITHUB_SSH_HOST_KEYS) + "\n")
    known_hosts.chmod(0o644)


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
