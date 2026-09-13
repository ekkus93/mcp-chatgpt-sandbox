"""Shared setup diagnostics used by the TUI, setup --check, and doctor."""

from __future__ import annotations

import os
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
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.repository_setup import verify_repository
from dev_workspace_mcp.setup.ssh_github_setup import verify_credential_files
from dev_workspace_mcp.setup.tunnel import check_tunnel_profile


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ACTION_REQUIRED = "ACTION REQUIRED"
    SKIPPED = "SKIPPED"


class DiagnosticCode(StrEnum):
    CONFIG_RELOAD = "config.reload"
    CONFIG_SAFETY = "config.safety"
    STATE_DB = "state_db"
    AUDIT = "audit"
    DOCKER_DAEMON = "docker.daemon"
    DOCKER_IMAGE = "docker.image"
    REPOSITORY = "repository"
    SSH_KEY = "ssh.key"
    KNOWN_HOSTS = "ssh.known_hosts"
    GITHUB_PROTECTION = "github.protection"
    SERVER_CONSTRUCTION = "server.construction"
    TUNNEL_PROFILE = "tunnel.profile"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    code: DiagnosticCode
    label: str
    status: DiagnosticStatus
    message: str
    mandatory: bool = True
    remediation: str | None = None
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticsReport:
    results: tuple[DiagnosticResult, ...]

    @property
    def ready_to_finish(self) -> bool:
        return all(
            not result.mandatory
            or result.status not in {DiagnosticStatus.FAIL, DiagnosticStatus.ACTION_REQUIRED}
            for result in self.results
        )

    @property
    def exit_code(self) -> int:
        return 0 if self.ready_to_finish else 1


def _result(
    code: DiagnosticCode,
    label: str,
    status: DiagnosticStatus,
    message: str,
    *,
    mandatory: bool = True,
    remediation: str | None = None,
    subject: str | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(
        code=code,
        label=label,
        status=status,
        message=message,
        mandatory=mandatory,
        remediation=remediation,
        subject=subject,
    )


def _config_safety(config_path: Path) -> DiagnosticResult:
    try:
        reject_unsafe_sensitive_target(config_path)
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except (OSError, SetupFilesystemError):
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Configuration file safety",
            DiagnosticStatus.FAIL,
            "Configuration file is missing, unsafe, or cannot be inspected.",
        )
    if mode & 0o022:
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Configuration file safety",
            DiagnosticStatus.FAIL,
            "Configuration file is writable by group or other users.",
        )
    return _result(
        DiagnosticCode.CONFIG_SAFETY,
        "Configuration file safety",
        DiagnosticStatus.PASS,
        f"Configuration file mode is {mode:04o}.",
    )


def _state_db_check(path: Path) -> DiagnosticResult:
    parent = path.expanduser().parent
    if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.STATE_DB,
            "State database path",
            DiagnosticStatus.PASS,
            f"State database parent is writable: {parent}",
        )
    return _result(
        DiagnosticCode.STATE_DB,
        "State database path",
        DiagnosticStatus.ACTION_REQUIRED,
        f"State database parent is not writable: {parent}",
    )


def _audit_check(path: Path) -> DiagnosticResult:
    parent = path.expanduser().parent
    if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.AUDIT,
            "Audit path",
            DiagnosticStatus.PASS,
            f"Audit parent is writable: {parent}",
        )
    return _result(
        DiagnosticCode.AUDIT,
        "Audit path",
        DiagnosticStatus.ACTION_REQUIRED,
        f"Audit parent is not writable: {parent}",
    )


def _docker_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    try:
        client = docker.from_env()
        try:
            client.ping()
            try:
                client.images.get(config.docker.default_image)
            except ImageNotFound:
                image = _result(
                    DiagnosticCode.DOCKER_IMAGE,
                    "Default workspace image",
                    DiagnosticStatus.ACTION_REQUIRED,
                    f"Image is not available locally: {config.docker.default_image}",
                )
            else:
                image = _result(
                    DiagnosticCode.DOCKER_IMAGE,
                    "Default workspace image",
                    DiagnosticStatus.PASS,
                    f"Image is available: {config.docker.default_image}",
                )
        finally:
            client.close()
    except (DockerException, OSError):
        return (
            _result(
                DiagnosticCode.DOCKER_DAEMON,
                "Docker daemon",
                DiagnosticStatus.ACTION_REQUIRED,
                "Docker is not reachable.",
            ),
            _result(
                DiagnosticCode.DOCKER_IMAGE,
                "Default workspace image",
                DiagnosticStatus.SKIPPED,
                "Image check skipped because Docker is unavailable.",
            ),
        )
    return (
        _result(
            DiagnosticCode.DOCKER_DAEMON,
            "Docker daemon",
            DiagnosticStatus.PASS,
            "Docker is reachable.",
        ),
        image,
    )


def _repository_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    results: list[DiagnosticResult] = []
    for repository in config.repositories:
        report = verify_repository(
            {
                "id": repository.id,
                "path": str(repository.path),
                "github": repository.github,
                "ssh_key": repository.ssh_key,
            }
        )
        results.append(
            _result(
                DiagnosticCode.REPOSITORY,
                "Repository",
                DiagnosticStatus.PASS if report.valid else DiagnosticStatus.ACTION_REQUIRED,
                report.message,
                subject=repository.id,
            )
        )
    return tuple(results)


def _ssh_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    checks = verify_credential_files(
        {
            "mode": config.ssh.mode,
            "scope": config.ssh.scope,
            "keys_dir": config.ssh.keys_dir,
            "known_hosts": config.ssh.known_hosts,
            "shared_key": config.ssh.shared_key,
            "github_api_auth": config.github.api_auth,
            "github_api_token_path": config.github.api_token_path,
            "github_api_base_url": config.github.api_base_url,
        },
        tuple(
            {
                "id": repository.id,
                "path": str(repository.path),
                "github": repository.github,
                "ssh_key": repository.ssh_key,
            }
            for repository in config.repositories
        ),
    )
    results: list[DiagnosticResult] = []
    for check in checks:
        status = (
            DiagnosticStatus.ACTION_REQUIRED
            if check.status == "action_required"
            else DiagnosticStatus.SKIPPED
            if check.status == "skipped"
            else DiagnosticStatus.PASS
        )
        code = DiagnosticCode.KNOWN_HOSTS if "known_hosts" in check.message else DiagnosticCode.SSH_KEY
        results.append(_result(code, "SSH credential", status, check.message))
    if config.ssh.known_hosts.is_file() and config.ssh.known_hosts.stat().st_size > 0:
        results.append(
            _result(
                DiagnosticCode.KNOWN_HOSTS,
                "Pinned known_hosts",
                DiagnosticStatus.PASS,
                "Pinned known_hosts exists and is non-empty.",
            )
        )
    else:
        results.append(
            _result(
                DiagnosticCode.KNOWN_HOSTS,
                "Pinned known_hosts",
                DiagnosticStatus.ACTION_REQUIRED,
                "Pinned known_hosts is missing or empty.",
            )
        )
    return tuple(results)


def _local_default_branch(repository_path: Path) -> str | None:
    try:
        symbolic = subprocess.run(
            [
                "git",
                "-C",
                str(repository_path),
                "symbolic-ref",
                "--quiet",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if symbolic.returncode == 0:
        ref = symbolic.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix) and len(ref) > len(prefix):
            return ref[len(prefix) :]
    head_file = repository_path / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    try:
        if head_file.is_file():
            text = head_file.read_text(encoding="utf-8").strip()
            prefix = "ref: refs/remotes/origin/"
            if text.startswith(prefix) and len(text) > len(prefix):
                return text[len(prefix) :]
    except (OSError, UnicodeError):
        return None
    return None


def _github_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    if config.github.api_auth != "token" or config.github.api_token_path is None:
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
        status = (
            DiagnosticStatus.PASS
            if report.status == "verified"
            else DiagnosticStatus.ACTION_REQUIRED
        )
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
    except Exception:  # WCR-013: terminal diagnostic boundary; fail closed and suppress details.
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
    except Exception:  # WCR-013: outer diagnostic boundary; fail closed and suppress details.
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
