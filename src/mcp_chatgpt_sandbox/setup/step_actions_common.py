"""Shared presentation helpers for setup step-action mixins."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

from textual.widgets import Input, Select

from mcp_chatgpt_sandbox.setup.runtime_setup import SetupRuntimeError

if TYPE_CHECKING:
    from mcp_chatgpt_sandbox.setup.config_writer import ConfigUpdatePlan
    from mcp_chatgpt_sandbox.setup.docker_setup import DockerBuildPlan
    from mcp_chatgpt_sandbox.setup.model import SetupState, SetupStep
    from mcp_chatgpt_sandbox.setup.tunnel import TunnelProfilePlan

_T = TypeVar("_T")


class _ActionHost(Protocol):
    state: SetupState
    config_path: Path
    no_browser: bool
    _review_plan: ConfigUpdatePlan | None
    _pending_docker_build_plan: DockerBuildPlan | None
    _pending_tunnel_plan: TunnelProfilePlan | None

    def query_one(self, selector: str, expect_type: type[_T]) -> _T: ...

    def set_step_validation(
        self, step: SetupStep, *, valid: bool, message: str | None = None
    ) -> None: ...

    def _save_validated_state(
        self,
        candidate: SetupState,
        *,
        step: SetupStep,
        message: str,
        skipped: bool = False,
    ) -> None: ...

    def _save(self, candidate: SetupState) -> bool: ...

    def _refresh_repository_table(self) -> None: ...

    def _refresh(self) -> None: ...


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _selected(host: _ActionHost, selector: str, *, label: str) -> str:
    value = host.query_one(selector, Select).value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be selected")
    return value


def _required_int(host: _ActionHost, selector: str, *, label: str) -> int:
    raw = host.query_one(selector, Input).value.strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be an integer") from exc


def _optional_int(host: _ActionHost, selector: str, *, label: str) -> int | None:
    raw = host.query_one(selector, Input).value.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be an integer or blank") from exc


def _optional_float(host: _ActionHost, selector: str, *, label: str) -> float | None:
    raw = host.query_one(selector, Input).value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be a number or blank") from exc


def _command_text(argv: tuple[str, ...]) -> str:
    return " ".join(argv)
