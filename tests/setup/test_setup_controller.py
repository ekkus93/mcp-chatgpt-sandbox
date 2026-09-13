"""Presentation-independent setup lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_workspace_mcp.setup.config_plan import ConfigInspection
from dev_workspace_mcp.setup.controller import SetupController, SetupControllerError
from dev_workspace_mcp.setup.diagnostics import (
    DiagnosticResult,
    DiagnosticStatus,
    SetupDiagnosticsReport,
)
from dev_workspace_mcp.setup.model import SetupState, SetupStep, SetupStepStatus
from dev_workspace_mcp.setup.state import save_setup_state


def _valid_config_mapping(tmp_path: Path) -> dict[str, object]:
    return {
        "version": 1,
        "state_db_path": str(tmp_path / "state.db"),
        "docker": {"default_image": "mcp-dev-test-s7:24.04"},
        "ssh": {
            "keys_dir": str(tmp_path / "keys"),
            "known_hosts": str(tmp_path / "known_hosts"),
        },
        "audit": {"path": str(tmp_path / "audit.jsonl")},
        "logging": {"level": "DEBUG"},
    }


def _write_valid_config(tmp_path: Path) -> Path:
    target = tmp_path / "config.yml"
    target.write_text(
        yaml.safe_dump(_valid_config_mapping(tmp_path), sort_keys=False),
        encoding="utf-8",
    )
    return target


def test_fresh_controller_starts_at_first_step(tmp_path: Path) -> None:
    controller = SetupController.create(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "setup-state.json",
    )

    assert not controller.has_existing_material
    assert controller.state.current_step is SetupStep.LOCAL_PREREQUISITES


def test_resume_uses_valid_incomplete_state(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    state_path = tmp_path / "setup-state.json"
    state = SetupState(
        config_path=str(config_path),
        current_step=SetupStep.DOCKER_WORKSPACE,
        statuses={SetupStep.SERVER_STORAGE: SetupStepStatus.COMPLETE},
    )
    save_setup_state(state, state_path)

    controller = SetupController.create(
        config_path=config_path,
        state_path=state_path,
        resume=True,
    )

    assert controller.state.current_step is SetupStep.DOCKER_WORKSPACE
    assert controller.state.statuses[SetupStep.SERVER_STORAGE] is SetupStepStatus.COMPLETE


def test_completed_state_cannot_false_resume(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    state_path = tmp_path / "setup-state.json"
    state = SetupState(
        config_path=str(config_path),
        statuses={SetupStep.FINAL_VALIDATION: SetupStepStatus.COMPLETE},
    )
    save_setup_state(state, state_path)

    with pytest.raises(SetupControllerError, match="already complete"):
        SetupController.create(
            config_path=config_path,
            state_path=state_path,
            resume=True,
        )


def test_corrupt_state_start_fresh_preserves_production_config(tmp_path: Path) -> None:
    config_path = _write_valid_config(tmp_path)
    original = config_path.read_bytes()
    state_path = tmp_path / "setup-state.json"
    state_path.write_text("not-json", encoding="utf-8")

    controller = SetupController.create(config_path=config_path, state_path=state_path)
    assert controller.state_load_error is not None
    assert controller.inspection == ConfigInspection(exists=True, valid=True)

    controller.choose_entry("start_fresh")

    assert config_path.read_bytes() == original
    assert (tmp_path / "state.db").exists() is False
    assert controller.state.current_step is SetupStep.LOCAL_PREREQUISITES


def test_edit_seeds_from_valid_existing_advanced_values(tmp_path: Path) -> None:
    config_path = _write_valid_config(tmp_path)
    controller = SetupController.create(
        config_path=config_path,
        state_path=tmp_path / "setup-state.json",
    )

    controller.choose_entry("edit")
    _filesystem, _execution, _resources, logging = controller.effective_advanced()

    assert logging.level == "DEBUG"
    assert controller.state.logging_overrides is None
    assert config_path.exists()


def test_repair_invalid_config_does_not_replace_before_review(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    original = b"not: production-valid\n"
    config_path.write_bytes(original)
    controller = SetupController.create(
        config_path=config_path,
        state_path=tmp_path / "setup-state.json",
    )

    controller.choose_entry("repair")

    assert controller.repair_invalid_config
    assert config_path.read_bytes() == original
    assert controller.reviewed_plan is None


def test_final_diagnostics_receive_current_state_and_process_local_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = SetupController(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "setup-state.json",
        state=SetupState(chatgpt_enabled=True, tunnel_id="tunnel_example"),
        inspection=ConfigInspection(exists=True, valid=True),
        existing_config=None,
        runtime_tunnel_key="runtime-only",
    )
    report = SetupDiagnosticsReport(
        (
            DiagnosticResult(
                code="config.reload",
                label="Production configuration",
                status=DiagnosticStatus.PASS,
                message="ok",
            ),
        )
    )
    observed: dict[str, object] = {}

    def fake_diagnostics(
        path: Path,
        *,
        setup_state: SetupState | None = None,
        tunnel_runtime_key: str | None = None,
        setup_state_error: str | None = None,
    ) -> SetupDiagnosticsReport:
        observed.update(
            path=path,
            setup_state=setup_state,
            tunnel_runtime_key=tunnel_runtime_key,
            setup_state_error=setup_state_error,
        )
        return report

    monkeypatch.setattr(
        "dev_workspace_mcp.setup.controller.run_setup_diagnostics",
        fake_diagnostics,
    )
    monkeypatch.setattr(controller, "persist", lambda **_kwargs: controller.state_path)

    assert controller.run_final_diagnostics() is report
    assert observed["path"] == controller.config_path
    assert observed["setup_state"] is controller.state
    assert observed["tunnel_runtime_key"] == "runtime-only"
    assert observed["setup_state_error"] is None
    assert controller.state.statuses[SetupStep.FINAL_VALIDATION] is SetupStepStatus.COMPLETE


def test_unexpected_final_diagnostic_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = SetupController(
        config_path=tmp_path / "config.yml",
        state_path=tmp_path / "setup-state.json",
        state=SetupState(),
        inspection=ConfigInspection(exists=True, valid=True),
        existing_config=None,
    )

    def fail(*_args: object, **_kwargs: object) -> SetupDiagnosticsReport:
        raise RuntimeError("external response must not escape")

    monkeypatch.setattr("dev_workspace_mcp.setup.controller.run_setup_diagnostics", fail)
    monkeypatch.setattr(controller, "persist", lambda **_kwargs: controller.state_path)

    with pytest.raises(SetupControllerError, match="Unexpected setup diagnostic failure") as exc_info:
        controller.run_final_diagnostics()

    assert "external response" not in str(exc_info.value)
    assert controller.state.statuses[SetupStep.FINAL_VALIDATION] is SetupStepStatus.FAILED
