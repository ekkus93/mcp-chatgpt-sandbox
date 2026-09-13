"""Reviewable OpenAI Secure MCP Tunnel profile operations for setup."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TUNNEL_DOC_URL = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"
CHATGPT_APP_URL = "https://chatgpt.com/plugins"
CONTROL_PLANE_API_KEY_ENV = "CONTROL_PLANE_API_KEY"
DEFAULT_TUNNEL_PROFILE = "chatgpt-sandbox"
_TUNNEL_COMMAND_TIMEOUT_SECONDS = 30.0


class TunnelSetupError(RuntimeError):
    """Secret-safe tunnel-client planning or execution failure."""


@dataclass(frozen=True, slots=True)
class TunnelProfilePlan:
    profile: str
    tunnel_id: str
    mcp_command: str
    init_argv: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return shlex.join(self.init_argv)


def tunnel_client_available() -> bool:
    return shutil.which("tunnel-client") is not None


def build_tunnel_profile_plan(
    config_path: Path,
    tunnel_id: str,
    *,
    profile: str = DEFAULT_TUNNEL_PROFILE,
) -> TunnelProfilePlan:
    clean_tunnel_id = tunnel_id.strip()
    clean_profile = profile.strip()
    if not clean_tunnel_id:
        raise TunnelSetupError("Tunnel ID must not be empty")
    if not clean_profile or any(character.isspace() for character in clean_profile):
        raise TunnelSetupError("Tunnel profile name must be a non-empty token")
    config = config_path.expanduser().absolute()
    mcp_command = shlex.join(["dev-workspace-mcp", "serve", "--config", str(config)])
    argv = (
        "tunnel-client",
        "init",
        "--sample",
        "sample_mcp_stdio_local",
        "--profile",
        clean_profile,
        "--tunnel-id",
        clean_tunnel_id,
        "--mcp-command",
        mcp_command,
    )
    return TunnelProfilePlan(
        profile=clean_profile,
        tunnel_id=clean_tunnel_id,
        mcp_command=mcp_command,
        init_argv=argv,
    )


def _runtime_env(runtime_key: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if runtime_key:
        env[CONTROL_PLANE_API_KEY_ENV] = runtime_key
    return env


def create_or_repair_tunnel_profile(
    plan: TunnelProfilePlan,
    *,
    runtime_key: str | None,
) -> None:
    """Run the exact reviewed ``tunnel-client init`` argv after approval."""

    if not tunnel_client_available():
        raise TunnelSetupError("tunnel-client is not installed or not available on PATH")
    try:
        result = subprocess.run(
            list(plan.init_argv),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TUNNEL_COMMAND_TIMEOUT_SECONDS,
            env=_runtime_env(runtime_key),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TunnelSetupError("tunnel-client profile initialization failed") from exc
    if result.returncode != 0:
        raise TunnelSetupError("tunnel-client profile initialization returned a failure")


def doctor_tunnel_profile(
    profile: str = DEFAULT_TUNNEL_PROFILE,
    *,
    runtime_key: str | None,
) -> bool:
    """Run the bounded supported tunnel-client doctor command without echoing output."""

    if not tunnel_client_available():
        return False
    try:
        result = subprocess.run(
            ["tunnel-client", "doctor", "--profile", profile, "--explain"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TUNNEL_COMMAND_TIMEOUT_SECONDS,
            env=_runtime_env(runtime_key),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


__all__ = [
    "CHATGPT_APP_URL",
    "CONTROL_PLANE_API_KEY_ENV",
    "DEFAULT_TUNNEL_PROFILE",
    "TUNNEL_DOC_URL",
    "TunnelProfilePlan",
    "TunnelSetupError",
    "build_tunnel_profile_plan",
    "create_or_repair_tunnel_profile",
    "doctor_tunnel_profile",
    "tunnel_client_available",
]
