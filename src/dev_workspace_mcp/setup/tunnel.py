"""Optional Secure MCP Tunnel setup without persisting runtime secrets."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

OPENAI_TUNNEL_DOC_URL = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"
OPENAI_CHATGPT_CONNECT_DOC_URL = "https://developers.openai.com/plugins/deploy/connect-chatgpt"
OPENAI_TUNNEL_SETTINGS_URL = "https://platform.openai.com/settings/organization/tunnels"


class SetupTunnelError(RuntimeError):
    """Secret-safe tunnel setup failure."""


@dataclass(frozen=True, slots=True)
class TunnelProfilePlan:
    """Reviewable, non-secret tunnel-client profile mutation."""

    profile: str
    tunnel_id: str
    config_path: Path
    python_executable: Path
    server_argv: tuple[str, ...]
    server_command: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TunnelHealth:
    available: bool
    healthy: bool
    message: str


def tunnel_client_path() -> str | None:
    return shutil.which("tunnel-client")


def _server_python_executable(python_executable: Path | None = None) -> Path:
    """Return an absolute, executable interpreter path without dereferencing venv links."""

    candidate = Path(sys.executable) if python_executable is None else python_executable
    candidate = candidate.expanduser()
    if not candidate.is_absolute():
        candidate = Path(os.path.abspath(candidate))
    try:
        usable = candidate.is_file() and os.access(candidate, os.X_OK)
    except OSError as exc:
        raise SetupTunnelError(
            "ACTION REQUIRED: the Python environment running setup cannot be reused by "
            "the tunnel profile; rerun setup from the environment that provides "
            "dev-workspace-mcp"
        ) from exc
    if not usable:
        raise SetupTunnelError(
            "ACTION REQUIRED: the Python environment running setup cannot be reused by "
            "the tunnel profile; rerun setup from the environment that provides "
            "dev-workspace-mcp"
        )
    return candidate


def build_tunnel_profile_plan(
    *,
    profile: str,
    tunnel_id: str,
    config_path: Path,
    project_root: Path | None = None,
    python_executable: Path | None = None,
) -> TunnelProfilePlan:
    """Build the exact profile command an operator must review before mutation.

    ``project_root`` is retained only for source compatibility with callers from
    before SWPH-001. It is intentionally ignored: persistent tunnel profiles are
    bound to the active Python environment, never to the caller's working directory.
    """

    del project_root
    profile = profile.strip()
    tunnel_id = tunnel_id.strip()
    if not profile or any(character.isspace() for character in profile):
        raise SetupTunnelError("Tunnel profile name must be a non-empty token")
    if not tunnel_id or any(character.isspace() for character in tunnel_id):
        raise SetupTunnelError("Tunnel id must be a non-empty token")
    resolved_config = config_path.expanduser().resolve(strict=False)
    resolved_python = _server_python_executable(python_executable)
    command_argv = (
        str(resolved_python),
        "-m",
        "dev_workspace_mcp",
        "serve",
        "--config",
        str(resolved_config),
    )
    server_command = shlex.join(command_argv)
    argv = (
        "tunnel-client",
        "init",
        "--sample",
        "sample_mcp_stdio_local",
        "--profile",
        profile,
        "--tunnel-id",
        tunnel_id,
        "--mcp-command",
        server_command,
    )
    return TunnelProfilePlan(
        profile=profile,
        tunnel_id=tunnel_id,
        config_path=resolved_config,
        python_executable=resolved_python,
        server_argv=command_argv,
        server_command=server_command,
        argv=argv,
    )


def apply_tunnel_profile(
    plan: TunnelProfilePlan,
    *,
    approved: bool,
    runtime_environment: Mapping[str, str] | None = None,
) -> None:
    """Apply only an explicitly reviewed profile; runtime secrets stay process-local."""

    if not approved:
        raise SetupTunnelError("Tunnel profile creation requires explicit approval")
    if tunnel_client_path() is None:
        raise SetupTunnelError("tunnel-client is not installed or not on PATH")
    child_environment = None
    if runtime_environment is not None:
        child_environment = {**os.environ, **runtime_environment}
    try:
        result = subprocess.run(
            list(plan.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=child_environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupTunnelError("tunnel-client profile creation could not be completed") from exc
    if result.returncode != 0:
        raise SetupTunnelError("tunnel-client rejected the reviewed profile change")


def check_tunnel_profile(profile: str) -> TunnelHealth:
    """Run bounded tunnel-client doctor without echoing command output or credentials."""

    if tunnel_client_path() is None:
        return TunnelHealth(False, False, "tunnel-client is not installed or not on PATH")
    try:
        result = subprocess.run(
            ["tunnel-client", "doctor", "--profile", profile, "--explain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return TunnelHealth(True, False, "tunnel-client doctor could not complete")
    if result.returncode != 0:
        return TunnelHealth(True, False, "tunnel-client doctor reported an unhealthy profile")
    return TunnelHealth(True, True, "tunnel-client doctor reports the profile healthy")


def open_setup_documentation(url: str, *, no_browser: bool, approved: bool) -> bool:
    """Open a public setup URL only when explicitly requested and browser use is allowed."""

    if no_browser or not approved:
        return False
    if url not in {
        OPENAI_TUNNEL_DOC_URL,
        OPENAI_CHATGPT_CONNECT_DOC_URL,
        OPENAI_TUNNEL_SETTINGS_URL,
    }:
        raise SetupTunnelError("Refusing to open an unrecognized setup URL")
    return bool(webbrowser.open(url, new=2))
