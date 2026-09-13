"""Shared machine-verifiable setup diagnostics for TUI, --check, and doctor."""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import click
import docker
from docker.errors import DockerException, ImageNotFound

from dev_workspace_mcp.config import ConfigError, ServerConfig, load_config
from dev_workspace_mcp.github_api import verify_remote_protection
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.setup.model import SetupRepositoryState
from dev_workspace_mcp.setup.repository_setup import verify_repository
from dev_workspace_mcp.setup.ssh_github_setup import CredentialChoices, verify_credential_files
from dev_workspace_mcp.setup.tunnel import check_tunnel_profile


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ACTION_REQUIRED = "ACTION REQUIRED"
    SKIPPED = "SKIPPED"


class DiagnosticCode(StrEnum):
    CONFIG_RELOAD = "config.reload"
    CONFIG_SAFETY = "config.safety"
    STATE_DB = "state_db.open"
    AUDIT_PATH = "audit.path"
    DOCKER_DAEMON = "docker.daemon"
    DOCKER_IMAGE = "docker.image"
    REPOSITORY_PATH = "repository.path"
    REPOSITORY_REMOTE = "repository.remote"
    SSH_KEYS = "ssh.keys"
    KNOWN_HOSTS = "ssh.known_hosts"
    GITHUB_PROTECTION = "github.protection"
    SERVER_CONSTRUCTION = "server.construct"
    TUNNEL_PROFILE = "tunnel.profile"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    code: DiagnosticCode
    label: str
    status: DiagnosticStatus
    message: str
    remediation: str | None = None
    mandatory: bool = True
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticsReport:
    results: tuple[DiagnosticResult, ...]

    @property
    def ready_to_finish(self) -> bool:
        return all(
            not result.mandatory or result.status is DiagnosticStatus.PASS
            for result in self.results
        )

    @property
    def exit_code(self) -> int:
        if self.ready_to_finish:
            return 0
        if any(
            result.mandatory and result.status is DiagnosticStatus.FAIL for result in self.results
        ):
            return 2
        return 1


def _result(
    code: DiagnosticCode,
    label: str,
    status: DiagnosticStatus,
    message: str,
    *,
    remediation: str | None = None,
    mandatory: bool = True,
    subject: str | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(code, label, status, message, remediation, mandatory, subject)


def _config_safety(path: Path) -> DiagnosticResult:
    try:
        metadata = path.lstat()
    except OSError:
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Config file safety",
            DiagnosticStatus.FAIL,
            "Configuration file could not be inspected safely.",
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Config file safety",
            DiagnosticStatus.FAIL,
            "Configuration target is not a regular non-symlink file.",
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Config file safety",
            DiagnosticStatus.ACTION_REQUIRED,
            "Configuration file is readable or writable by group/other users.",
            remediation=f"Restrict {path} to owner-only permissions (for example chmod 600).",
        )
    return _result(
        DiagnosticCode.CONFIG_SAFETY,
        "Config file safety",
        DiagnosticStatus.PASS,
        "Configuration is a regular owner-only file.",
    )


def _target_lstat(
    path: Path, *, code: DiagnosticCode, label: str
) -> os.stat_result | DiagnosticResult | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return _result(
            code,
            label,
            DiagnosticStatus.FAIL,
            f"{label} target could not be inspected safely.",
        )


def _unsafe_existing_target_result(
    metadata: os.stat_result,
    *,
    code: DiagnosticCode,
    label: str,
    message: str,
) -> DiagnosticResult | None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return _result(code, label, DiagnosticStatus.FAIL, message)
    return None


def _state_db_check(path: Path) -> DiagnosticResult:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.STATE_DB,
            "State database",
            DiagnosticStatus.ACTION_REQUIRED,
            "State database parent directory is not writable.",
        )
    metadata = _target_lstat(path, code=DiagnosticCode.STATE_DB, label="State database")
    if isinstance(metadata, DiagnosticResult):
        return metadata
    if metadata is not None:
        unsafe = _unsafe_existing_target_result(
            metadata,
            code=DiagnosticCode.STATE_DB,
            label="State database",
            message="Existing state database target is not a regular non-symlink file.",
        )
        if unsafe is not None:
            return unsafe
        try:
            connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
            connection.execute("PRAGMA schema_version").fetchone()
            connection.close()
        except sqlite3.Error:
            return _result(
                DiagnosticCode.STATE_DB,
                "State database",
                DiagnosticStatus.FAIL,
                "Existing state database could not be opened read/write.",
            )
    return _result(
        DiagnosticCode.STATE_DB,
        "State database",
        DiagnosticStatus.PASS,
        "State database location is usable.",
    )


def _audit_check(path: Path) -> DiagnosticResult:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.AUDIT_PATH,
            "Audit log",
            DiagnosticStatus.ACTION_REQUIRED,
            "Audit log parent directory is not writable.",
        )
    metadata = _target_lstat(path, code=DiagnosticCode.AUDIT_PATH, label="Audit log")
    if isinstance(metadata, DiagnosticResult):
        return metadata
    if metadata is not None:
        unsafe = _unsafe_existing_target_result(
            metadata,
            code=DiagnosticCode.AUDIT_PATH,
            label="Audit log",
            message="Existing audit target is not a regular non-symlink file.",
        )
        if unsafe is not None:
            return unsafe
    return _result(
        DiagnosticCode.AUDIT_PATH,
        "Audit log",
        DiagnosticStatus.PASS,
        "Audit log location is usable.",
    )


def _docker_checks(config: ServerConfig) -> tuple[DiagnosticResult, DiagnosticResult]:
    try:
        client = docker.from_env()
        try:
            reachable = bool(client.ping())
            if not reachable:
                raise DockerException("daemon ping failed")
            daemon = _result(
                DiagnosticCode.DOCKER_DAEMON,
                "Docker daemon",
                DiagnosticStatus.PASS,
                "Docker daemon is reachable.",
            )
            try:
                client.images.get(config.docker.default_image)
            except ImageNotFound:
                image = _result(
                    DiagnosticCode.DOCKER_IMAGE,
                    "Default Docker image",
                    DiagnosticStatus.ACTION_REQUIRED,
                    "Configured default workspace image is not present locally.",
                    remediation=(
                        f"Build or pull the configured image {config.docker.default_image!r}."
                    ),
                )
            else:
                image = _result(
                    DiagnosticCode.DOCKER_IMAGE,
                    "Default Docker image",
                    DiagnosticStatus.PASS,
                    "Configured default workspace image is present locally.",
                )
        finally:
            client.close()
    except (DockerException, OSError):
        daemon = _result(
            DiagnosticCode.DOCKER_DAEMON,
            "Docker daemon",
            DiagnosticStatus.ACTION_REQUIRED,
            "Docker daemon is not reachable from this host.",
        )
        image = _result(
            DiagnosticCode.DOCKER_IMAGE,
            "Default Docker image",
            DiagnosticStatus.SKIPPED,
            "Image inspection is skipped until Docker is reachable.",
        )
    return daemon, image


def _repository_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    results: list[DiagnosticResult] = []
    for repository in config.repositories:
        state = SetupRepositoryState.from_dict(
            {
                "id": repository.id,
                "path": str(repository.path),
                "github": repository.github,
                "ssh_key": repository.ssh_key,
            }
        )
        verification = verify_repository(state)
        results.append(
            _result(
                DiagnosticCode.REPOSITORY_PATH,
                "Repository path",
                DiagnosticStatus.PASS
                if verification.is_git_repository
                else DiagnosticStatus.ACTION_REQUIRED,
                "Local path is a Git working tree."
                if verification.is_git_repository
                else "Local path is not a usable Git working tree.",
                subject=repository.id,
            )
        )
        results.append(
            _result(
                DiagnosticCode.REPOSITORY_REMOTE,
                "Repository remote identity",
                DiagnosticStatus.PASS
                if verification.matches_configured_identity
                else DiagnosticStatus.ACTION_REQUIRED,
                verification.message,
                subject=repository.id,
            )
        )
    return tuple(results)


def _ssh_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    choices = CredentialChoices(
        mode=config.ssh.mode,
        scope=config.ssh.scope,
        keys_dir=config.ssh.keys_dir,
        known_hosts=config.ssh.known_hosts,
        shared_key=config.ssh.shared_key,
        github_api_auth=config.github.api_auth,
        github_api_token_path=config.github.api_token_path,
        github_api_base_url=config.github.api_base_url,
    )
    repositories = tuple(
        SetupRepositoryState.from_dict(
            {
                "id": repository.id,
                "path": str(repository.path),
                "github": repository.github,
                "ssh_key": repository.ssh_key,
            }
        )
        for repository in config.repositories
    )
    checks = verify_credential_files(choices, repositories)
    results: list[DiagnosticResult] = []
    if checks:
        known = checks[0]
        results.append(
            _result(
                DiagnosticCode.KNOWN_HOSTS,
                "Known hosts",
                DiagnosticStatus.PASS if known.valid else DiagnosticStatus.ACTION_REQUIRED,
                known.message,
            )
        )
    for check in checks[1:]:
        status = DiagnosticStatus.PASS if check.valid else DiagnosticStatus.ACTION_REQUIRED
        if check.status == "unverified":
            status = DiagnosticStatus.SKIPPED
        results.append(
            _result(
                DiagnosticCode.SSH_KEYS,
                "SSH/GitHub credential references",
                status,
                check.message,
                mandatory=check.status != "unverified",
            )
        )
    return tuple(results)


def _local_default_branch(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value.removeprefix("origin/") or None


def _github_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    if config.git_policy.require_remote_protection == "off":
        return (
            _result(
                DiagnosticCode.GITHUB_PROTECTION,
                "GitHub protection",
                DiagnosticStatus.SKIPPED,
                "Remote-protection verification is disabled by Git policy.",
                mandatory=False,
            ),
        )
    if config.github.api_auth != "token":
        return (
            _result(
                DiagnosticCode.GITHUB_PROTECTION,
                "GitHub protection",
                DiagnosticStatus.SKIPPED,
                "No supported GitHub API token verification is configured.",
                mandatory=config.git_policy.require_remote_protection == "deny",
            ),
        )
    results: list[DiagnosticResult] = []
    for repository in config.repositories:
        branch = _local_default_branch(repository.path)
        if branch is None:
            results.append(
                _result(
                    DiagnosticCode.GITHUB_PROTECTION,
                    "GitHub protection",
                    DiagnosticStatus.SKIPPED,
                    "Local default branch could not be determined without contacting the remote.",
                    mandatory=config.git_policy.require_remote_protection == "deny",
                    subject=repository.id,
                )
            )
            continue
        report = verify_remote_protection(
            github_config=config.github,
            owner_repo=repository.github,
            default_branch=branch,
            checks=config.git_policy.remote_protection_checks,
        )
        if report.status == "verified":
            status = DiagnosticStatus.PASS
        else:
            status = DiagnosticStatus.ACTION_REQUIRED
        results.append(
            _result(
                DiagnosticCode.GITHUB_PROTECTION,
                "GitHub protection",
                status,
                f"Remote protection status is {report.status}.",
                mandatory=config.git_policy.require_remote_protection == "deny",
                subject=repository.id,
            )
        )
    return tuple(results)


def _server_construction(config: ServerConfig) -> DiagnosticResult:
    try:
        create_server(config)
    except Exception:
        return _result(
            DiagnosticCode.SERVER_CONSTRUCTION,
            "MCP server construction",
            DiagnosticStatus.FAIL,
            "MCP server construction failed; details are intentionally omitted from diagnostics.",
        )
    return _result(
        DiagnosticCode.SERVER_CONSTRUCTION,
        "MCP server construction",
        DiagnosticStatus.PASS,
        "MCP server can be constructed without starting a transport.",
    )


def run_diagnostics(
    config_path: Path,
    *,
    tunnel_profile: str | None = None,
    tunnel_requested: bool = False,
) -> DiagnosticsReport:
    """Run the shared final diagnostic matrix and fail closed on surprises."""

    expanded = config_path.expanduser()
    try:
        config = load_config(expanded)
    except ConfigError:
        return DiagnosticsReport(
            (
                _result(
                    DiagnosticCode.CONFIG_RELOAD,
                    "Production config reload",
                    DiagnosticStatus.FAIL,
                    "Configuration is rejected by the production loader.",
                ),
            )
        )
    results: list[DiagnosticResult] = [
        _result(
            DiagnosticCode.CONFIG_RELOAD,
            "Production config reload",
            DiagnosticStatus.PASS,
            "Production configuration reload succeeded.",
        ),
        _config_safety(expanded),
        _state_db_check(config.state_db_path),
        _audit_check(config.audit.path),
    ]
    try:
        results.extend(_docker_checks(config))
        results.extend(_repository_checks(config))
        results.extend(_ssh_checks(config))
        results.extend(_github_checks(config))
        results.append(_server_construction(config))
        if tunnel_requested:
            if tunnel_profile is None:
                results.append(
                    _result(
                        DiagnosticCode.TUNNEL_PROFILE,
                        "Secure MCP Tunnel",
                        DiagnosticStatus.ACTION_REQUIRED,
                        "ChatGPT connection was selected but no tunnel profile is configured.",
                    )
                )
            else:
                health = check_tunnel_profile(tunnel_profile)
                results.append(
                    _result(
                        DiagnosticCode.TUNNEL_PROFILE,
                        "Secure MCP Tunnel",
                        DiagnosticStatus.PASS
                        if health.healthy
                        else DiagnosticStatus.ACTION_REQUIRED,
                        health.message,
                    )
                )
        else:
            results.append(
                _result(
                    DiagnosticCode.TUNNEL_PROFILE,
                    "Secure MCP Tunnel",
                    DiagnosticStatus.SKIPPED,
                    "Optional ChatGPT tunnel setup was not selected.",
                    mandatory=False,
                )
            )
    except Exception:
        results.append(
            _result(
                DiagnosticCode.SERVER_CONSTRUCTION,
                "Unexpected setup diagnostic",
                DiagnosticStatus.FAIL,
                "An unexpected diagnostic failure occurred; details are intentionally omitted.",
            )
        )
    return DiagnosticsReport(tuple(results))


def format_diagnostics(report: DiagnosticsReport) -> str:
    lines: list[str] = []
    for result in report.results:
        subject = f" [{result.subject}]" if result.subject else ""
        lines.append(f"{result.status.value:<15} {result.code.value}{subject}: {result.message}")
        if result.remediation:
            lines.append(f"                remediation: {result.remediation}")
    return "\n".join(lines)


def run_check_cli(config_path: Path) -> int:
    report = run_diagnostics(config_path)
    click.echo(format_diagnostics(report))
    return report.exit_code
