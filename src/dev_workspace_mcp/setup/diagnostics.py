"""Shared, secret-safe setup diagnostics for the TUI and headless CLI."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound

from dev_workspace_mcp.config import ConfigError, ServerConfig, load_config
from dev_workspace_mcp.github_api import verify_remote_protection
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.setup.filesystem import SetupFilesystemError, reject_unsafe_sensitive_target
from dev_workspace_mcp.setup.model import SetupRepository, SetupState
from dev_workspace_mcp.setup.repository_verification import detect_default_branch, verify_repository
from dev_workspace_mcp.setup.tunnel import DEFAULT_TUNNEL_PROFILE, doctor_tunnel_profile
from dev_workspace_mcp.workspace.ssh_agent import resolve_ssh_key_path

_SERVER_PROBE_TIMEOUT_SECONDS = 15.0


class DiagnosticStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ACTION_REQUIRED = "action_required"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    code: str
    label: str
    status: DiagnosticStatus
    message: str
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class SetupDiagnosticsReport:
    diagnostics: tuple[DiagnosticResult, ...]

    @property
    def unresolved(self) -> tuple[DiagnosticResult, ...]:
        return tuple(
            item
            for item in self.diagnostics
            if item.status in {DiagnosticStatus.FAIL, DiagnosticStatus.ACTION_REQUIRED}
        )

    @property
    def valid(self) -> bool:
        return not self.unresolved


def _result(
    code: str,
    label: str,
    status: DiagnosticStatus,
    message: str,
    remediation: str | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(code, label, status, message, remediation)


def _config_path_check(config_file: Path) -> DiagnosticResult:
    path = config_file.expanduser().absolute()
    try:
        reject_unsafe_sensitive_target(path)
        mode = stat.S_IMODE(path.stat().st_mode)
    except (OSError, SetupFilesystemError):
        return _result(
            "config.path_safety",
            "Configuration file safety",
            DiagnosticStatus.FAIL,
            "Configuration target is missing, unsafe, or cannot be inspected",
            "Use a regular non-symlink configuration file at a safe path.",
        )
    if mode & 0o022:
        return _result(
            "config.path_safety",
            "Configuration file safety",
            DiagnosticStatus.FAIL,
            "Configuration file is writable by group or other users",
            "Remove group/other write permission before starting the server.",
        )
    return _result(
        "config.path_safety",
        "Configuration file safety",
        DiagnosticStatus.PASS,
        f"Safe regular configuration file with mode {mode:04o}: {path}",
    )


def _path_parent_check(code: str, label: str, path: Path) -> DiagnosticResult:
    parent = path.expanduser().parent
    if not parent.exists():
        return _result(
            code,
            label,
            DiagnosticStatus.ACTION_REQUIRED,
            f"Parent directory does not exist: {parent}",
            "Create/review the parent directory before starting the server.",
        )
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return _result(
            code,
            label,
            DiagnosticStatus.FAIL,
            f"Parent directory is not writable: {parent}",
        )
    return _result(code, label, DiagnosticStatus.PASS, f"Writable parent: {parent}")


def _known_hosts_check(config: ServerConfig) -> DiagnosticResult:
    try:
        valid = config.ssh.known_hosts.is_file() and config.ssh.known_hosts.stat().st_size > 0
    except OSError:
        valid = False
    if valid:
        return _result(
            "ssh.known_hosts",
            "SSH known hosts",
            DiagnosticStatus.PASS,
            f"Pinned known-hosts file exists: {config.ssh.known_hosts}",
        )
    return _result(
        "ssh.known_hosts",
        "SSH known hosts",
        DiagnosticStatus.FAIL,
        "Pinned known-hosts file is missing or empty",
    )


def _docker_checks(config: ServerConfig) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    try:
        client = docker.from_env()
        try:
            client.ping()
            results.append(
                _result(
                    "docker.daemon", "Docker daemon", DiagnosticStatus.PASS, "Docker is reachable"
                )
            )
            try:
                client.images.get(config.docker.default_image)
            except ImageNotFound:
                results.append(
                    _result(
                        "docker.image",
                        "Default workspace image",
                        DiagnosticStatus.ACTION_REQUIRED,
                        f"Image is not present locally: {config.docker.default_image}",
                        "Build or pull the configured workspace image.",
                    )
                )
            else:
                results.append(
                    _result(
                        "docker.image",
                        "Default workspace image",
                        DiagnosticStatus.PASS,
                        f"Image is available: {config.docker.default_image}",
                    )
                )
        finally:
            client.close()
    except (DockerException, OSError):
        results.append(
            _result(
                "docker.daemon",
                "Docker daemon",
                DiagnosticStatus.ACTION_REQUIRED,
                "Docker is not reachable",
                "Start Docker and ensure the current user can access the daemon.",
            )
        )
        results.append(
            _result(
                "docker.image",
                "Default workspace image",
                DiagnosticStatus.SKIPPED,
                "Image check skipped because Docker is unavailable",
            )
        )
    return results


def _repository_checks(config: ServerConfig) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    registry = RepositoryRegistry(config)
    for repository in config.repositories:
        setup_repository = SetupRepository(
            id=repository.id,
            path=str(repository.path),
            github=repository.github,
            ssh_key=repository.ssh_key,
        )
        verification = verify_repository(setup_repository)
        results.append(
            _result(
                f"repository.{repository.id}.identity",
                f"Repository {repository.id}",
                DiagnosticStatus.PASS if verification.valid else DiagnosticStatus.FAIL,
                verification.message,
            )
        )
        record = registry.get(repository.id)
        key_path = resolve_ssh_key_path(config.ssh, record)
        if key_path is None:
            results.append(
                _result(
                    f"repository.{repository.id}.ssh_key",
                    f"Repository {repository.id} SSH key",
                    DiagnosticStatus.SKIPPED,
                    "No SSH key is configured for this repository",
                )
            )
        elif key_path.is_file():
            results.append(
                _result(
                    f"repository.{repository.id}.ssh_key",
                    f"Repository {repository.id} SSH key",
                    DiagnosticStatus.PASS,
                    f"Configured key file exists: {key_path}",
                )
            )
        else:
            results.append(
                _result(
                    f"repository.{repository.id}.ssh_key",
                    f"Repository {repository.id} SSH key",
                    DiagnosticStatus.ACTION_REQUIRED,
                    f"Configured key file is missing: {key_path}",
                )
            )
    return results


def _github_checks(config: ServerConfig) -> list[DiagnosticResult]:
    if config.github.api_auth == "none":
        return [
            _result(
                "github.api",
                "GitHub API verification",
                DiagnosticStatus.SKIPPED,
                "GitHub API integration is disabled",
            )
        ]
    if config.github.api_auth == "app":
        return [
            _result(
                "github.api",
                "GitHub API verification",
                DiagnosticStatus.ACTION_REQUIRED,
                "Production api_auth=app is not implemented",
            )
        ]
    if config.github.api_token_path is None or not config.github.api_token_path.is_file():
        return [
            _result(
                "github.api",
                "GitHub API verification",
                DiagnosticStatus.ACTION_REQUIRED,
                "GitHub token mode is configured but its credential file is unavailable",
            )
        ]

    results = [
        _result(
            "github.api",
            "GitHub API credential",
            DiagnosticStatus.PASS,
            "GitHub API credential file is available",
        )
    ]
    policy = config.git_policy.require_remote_protection
    for repository in config.repositories:
        default_branch = detect_default_branch(repository.path)
        protection = verify_remote_protection(
            github_config=config.github,
            owner_repo=repository.github,
            default_branch=default_branch,
            checks=config.git_policy.remote_protection_checks,
        )
        code = f"repository.{repository.id}.remote_protection"
        label = f"Repository {repository.id} remote protection"
        if protection.status == "verified":
            results.append(
                _result(
                    code,
                    label,
                    DiagnosticStatus.PASS,
                    "Configured remote-protection checks are verified",
                )
            )
            continue
        reason = protection.reason or f"remote protection status is {protection.status}"
        if policy == "deny":
            results.append(
                _result(
                    code,
                    label,
                    DiagnosticStatus.ACTION_REQUIRED,
                    f"Remote protection is {protection.status}: {reason}",
                    "Resolve GitHub verification/protection before using deny policy.",
                )
            )
        else:
            results.append(
                _result(
                    code,
                    label,
                    DiagnosticStatus.SKIPPED,
                    f"Remote protection is {protection.status}: {reason}; policy={policy} is nonblocking",
                )
            )
    return results


def _server_construction_check(config_file: Path) -> DiagnosticResult:
    script = (
        "import sys; from pathlib import Path; "
        "from dev_workspace_mcp.config import load_config; "
        "from dev_workspace_mcp.server import create_server; "
        "create_server(load_config(Path(sys.argv[1])))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(config_file)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_SERVER_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _result(
            "server.construction",
            "Server construction",
            DiagnosticStatus.FAIL,
            "Bounded server-construction probe failed",
        )
    if result.returncode != 0:
        return _result(
            "server.construction",
            "Server construction",
            DiagnosticStatus.FAIL,
            "Production create_server() probe returned a failure",
        )
    return _result(
        "server.construction",
        "Server construction",
        DiagnosticStatus.PASS,
        "Production create_server() completed without starting a transport",
    )


def _tunnel_check(state: SetupState | None, runtime_key: str | None) -> DiagnosticResult:
    if state is None or not state.chatgpt_enabled:
        return _result(
            "tunnel.profile",
            "Secure MCP Tunnel",
            DiagnosticStatus.SKIPPED,
            "Optional tunnel health check is not selected",
        )
    if doctor_tunnel_profile(DEFAULT_TUNNEL_PROFILE, runtime_key=runtime_key):
        return _result(
            "tunnel.profile",
            "Secure MCP Tunnel",
            DiagnosticStatus.PASS,
            f"tunnel-client doctor passed for profile {DEFAULT_TUNNEL_PROFILE!r}",
        )
    return _result(
        "tunnel.profile",
        "Secure MCP Tunnel",
        DiagnosticStatus.ACTION_REQUIRED,
        f"tunnel-client doctor did not pass for profile {DEFAULT_TUNNEL_PROFILE!r}",
        "Reacquire any process-local runtime credential and repair/rerun tunnel-client doctor.",
    )


def run_setup_diagnostics(
    config_file: Path,
    *,
    setup_state: SetupState | None = None,
    tunnel_runtime_key: str | None = None,
    setup_state_error: str | None = None,
) -> SetupDiagnosticsReport:
    """Run deterministic checks; ordinary failures become explicit results.

    ``setup_state_error`` is accepted only from the strict setup-state loader.
    A corrupt resumable state is reported rather than silently ignored; absence
    of setup state remains valid for operators who configure the server by hand.
    """

    results: list[DiagnosticResult] = []
    if setup_state_error is not None:
        results.append(
            _result(
                "setup.state",
                "Setup wizard state",
                DiagnosticStatus.ACTION_REQUIRED,
                "Persisted setup state is invalid and cannot be trusted",
                "Use interactive setup Repair or Start fresh; production configuration is untouched.",
            )
        )
    elif setup_state is None:
        results.append(
            _result(
                "setup.state",
                "Setup wizard state",
                DiagnosticStatus.SKIPPED,
                "No matching wizard state; manual configuration remains supported",
            )
        )
    else:
        results.append(
            _result(
                "setup.state",
                "Setup wizard state",
                DiagnosticStatus.PASS,
                "Strict non-secret setup state loaded",
            )
        )

    try:
        config = load_config(config_file)
    except ConfigError:
        results.append(
            _result(
                "config.reload",
                "Production configuration",
                DiagnosticStatus.FAIL,
                "Production load_config() rejected the configuration",
                "Repair the configuration and run diagnostics again.",
            )
        )
        return SetupDiagnosticsReport(tuple(results))

    results.append(
        _result(
            "config.reload",
            "Production configuration",
            DiagnosticStatus.PASS,
            "Production load_config() accepted the configuration",
        )
    )
    results.append(_config_path_check(config_file))
    results.append(_path_parent_check("state_db.parent", "State database", config.state_db_path))
    results.append(_path_parent_check("audit.parent", "Audit log", config.audit.path))
    results.append(_known_hosts_check(config))
    results.extend(_docker_checks(config))
    results.extend(_repository_checks(config))
    results.extend(_github_checks(config))
    results.append(_server_construction_check(config_file))
    results.append(_tunnel_check(setup_state, tunnel_runtime_key))
    return SetupDiagnosticsReport(tuple(results))


__all__ = [
    "DiagnosticResult",
    "DiagnosticStatus",
    "SetupDiagnosticsReport",
    "run_setup_diagnostics",
]
