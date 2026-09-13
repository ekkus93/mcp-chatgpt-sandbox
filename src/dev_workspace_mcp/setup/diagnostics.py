"""Shared deterministic setup diagnostics for TUI, setup --check, and doctor."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import click
import docker
from docker.errors import DockerException, ImageNotFound

from dev_workspace_mcp.config import ConfigError, ServerConfig, load_config
from dev_workspace_mcp.github_api import verify_remote_protection
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.server import create_server
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    inspect_regular_text_file,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.repository_setup import verify_repository
from dev_workspace_mcp.setup.tunnel import check_tunnel_profile
from dev_workspace_mcp.workspace.ssh_agent import resolve_ssh_key_path


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    SKIPPED = "SKIPPED"


class DiagnosticCode(StrEnum):
    CONFIG_RELOAD = "config.reload"
    CONFIG_SAFETY = "config.safety"
    STATE_DB = "state_db.open"
    AUDIT_PATH = "audit.path"
    DOCKER = "docker.daemon"
    DOCKER_IMAGE = "docker.image"
    REPOSITORY = "repository.verify"
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

    @property
    def unresolved(self) -> bool:
        return self.mandatory and self.status in {
            DiagnosticStatus.FAIL,
            DiagnosticStatus.ACTION_REQUIRED,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticsReport:
    results: tuple[DiagnosticResult, ...]

    @property
    def ready_to_finish(self) -> bool:
        return not any(result.unresolved for result in self.results)

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
        code,
        label,
        status,
        message,
        mandatory=mandatory,
        remediation=remediation,
        subject=subject,
    )


def _config_safety(path: Path) -> DiagnosticResult:
    try:
        reject_unsafe_sensitive_target(path)
        inspected = inspect_regular_text_file(path, required=True)
    except SetupFilesystemError:
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Configuration target safety",
            DiagnosticStatus.FAIL,
            "Configuration target is missing, unsafe, unreadable, oversized, or not UTF-8.",
        )
    if inspected.mode is not None and inspected.mode & 0o022:
        return _result(
            DiagnosticCode.CONFIG_SAFETY,
            "Configuration target safety",
            DiagnosticStatus.FAIL,
            "Configuration file is group/world-writable.",
            remediation="Use owner-only or otherwise non-group/world-writable permissions.",
        )
    return _result(
        DiagnosticCode.CONFIG_SAFETY,
        "Configuration target safety",
        DiagnosticStatus.PASS,
        "Configuration file is a safe regular UTF-8 text file.",
    )


def _state_db_check(path: Path) -> DiagnosticResult:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.STATE_DB,
            "State database parent",
            DiagnosticStatus.ACTION_REQUIRED,
            "State database parent is missing or not writable.",
        )
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".setup-state-check-", dir=parent)
        os.close(fd)
        Path(temp_name).unlink()
    except OSError:
        return _result(
            DiagnosticCode.STATE_DB,
            "State database parent",
            DiagnosticStatus.FAIL,
            "Could not create and remove a temporary state-db sibling.",
        )
    return _result(
        DiagnosticCode.STATE_DB,
        "State database parent",
        DiagnosticStatus.PASS,
        "State database parent is writable.",
    )


def _audit_check(path: Path) -> DiagnosticResult:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return _result(
            DiagnosticCode.AUDIT_PATH,
            "Audit path parent",
            DiagnosticStatus.ACTION_REQUIRED,
            "Audit path parent is missing or not writable.",
        )
    return _result(
        DiagnosticCode.AUDIT_PATH,
        "Audit path parent",
        DiagnosticStatus.PASS,
        "Audit path parent is writable.",
    )


def _docker_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    try:
        client = docker.from_env()
    except DockerException:
        return (
            _result(
                DiagnosticCode.DOCKER,
                "Docker daemon",
                DiagnosticStatus.ACTION_REQUIRED,
                "Docker daemon is unavailable.",
            ),
        )
    try:
        client.ping()
        daemon = _result(
            DiagnosticCode.DOCKER,
            "Docker daemon",
            DiagnosticStatus.PASS,
            "Docker daemon is reachable.",
        )
        try:
            client.images.get(config.docker.default_image)
        except ImageNotFound:
            image = _result(
                DiagnosticCode.DOCKER_IMAGE,
                "Workspace image",
                DiagnosticStatus.ACTION_REQUIRED,
                f"Workspace image {config.docker.default_image!r} is not present.",
                remediation="Build the reviewed default workspace image and rerun diagnostics.",
            )
        except DockerException:
            image = _result(
                DiagnosticCode.DOCKER_IMAGE,
                "Workspace image",
                DiagnosticStatus.FAIL,
                "Could not inspect configured workspace image.",
            )
        else:
            image = _result(
                DiagnosticCode.DOCKER_IMAGE,
                "Workspace image",
                DiagnosticStatus.PASS,
                f"Workspace image {config.docker.default_image!r} is present.",
            )
        return daemon, image
    except DockerException:
        return (
            _result(
                DiagnosticCode.DOCKER,
                "Docker daemon",
                DiagnosticStatus.ACTION_REQUIRED,
                "Docker daemon is unavailable.",
            ),
        )
    finally:
        client.close()


def _repository_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    results: list[DiagnosticResult] = []
    for repository in config.repositories:
        report = verify_repository(
            __import__(
                "dev_workspace_mcp.setup.model",
                fromlist=["SetupRepositoryState"],
            ).SetupRepositoryState.from_dict(repository.model_dump(mode="json"))
        )
        results.append(
            _result(
                DiagnosticCode.REPOSITORY,
                "Repository verification",
                DiagnosticStatus.PASS if report.valid else DiagnosticStatus.ACTION_REQUIRED,
                report.message,
                subject=repository.id,
            )
        )
    return tuple(results)


def _ssh_checks(config: ServerConfig) -> tuple[DiagnosticResult, ...]:
    results: list[DiagnosticResult] = []
    registry = RepositoryRegistry(config)
    for repository in config.repositories:
        record = registry.get(repository.id)
        key = resolve_ssh_key_path(config.ssh, record)
        if key is None:
            results.append(
                _result(
                    DiagnosticCode.SSH_KEY,
                    "Repository SSH key",
                    DiagnosticStatus.SKIPPED,
                    "No SSH key is configured for this repository.",
                    mandatory=False,
                    subject=repository.id,
                )
            )
        elif key.is_file():
            results.append(
                _result(
                    DiagnosticCode.SSH_KEY,
                    "Repository SSH key",
                    DiagnosticStatus.PASS,
                    "Configured repository SSH key exists.",
                    subject=repository.id,
                )
            )
        else:
            results.append(
                _result(
                    DiagnosticCode.SSH_KEY,
                    "Repository SSH key",
                    DiagnosticStatus.ACTION_REQUIRED,
                    "Configured repository SSH key is missing.",
                    subject=repository.id,
                )
            )
    known_hosts = config.ssh.known_hosts
    if known_hosts.is_file() and known_hosts.stat().st_size > 0:
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
            ["git", "-C", str(repository_path), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
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
    # WCR-013 fail-closed boundary: server construction may raise arbitrary
    # runtime/library exceptions; diagnostics must suppress their text and fail.
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
    # WCR-013 fail-closed boundary: the diagnostic matrix combines Docker,
    # Git, GitHub, MCP construction, and tunnel libraries. Any unexpected
    # exception must become a secret-safe FAIL result rather than escape or PASS.
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
