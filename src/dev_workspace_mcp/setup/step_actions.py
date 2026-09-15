"""Textual tunnel, config-review, and final-diagnostic action orchestration.

The ordinary prerequisite/storage/Docker/repository/credential/runtime actions
live in :mod:`step_actions_core`; this module retains the cross-cutting actions
that coordinate persistent tunnel state, reviewed configuration commits, and
final diagnostics.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.widgets import Button, Checkbox, Input, Static

from dev_workspace_mcp.setup.config_writer import (
    SetupConfigError,
    commit_config_update,
    inspect_config,
    prepare_config_update,
)
from dev_workspace_mcp.setup.diagnostics import format_diagnostics, run_diagnostics
from dev_workspace_mcp.setup.materialize import materialize_setup
from dev_workspace_mcp.setup.model import SetupStep, clone_setup_state
from dev_workspace_mcp.setup.step_actions_common import _command_text
from dev_workspace_mcp.setup.step_actions_core import CoreStepActionsMixin
from dev_workspace_mcp.setup.tunnel import (
    OPENAI_TUNNEL_DOC_URL,
    SetupTunnelError,
    TunnelProfilePlan,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
    check_tunnel_profile,
    open_setup_documentation,
)


class SetupStepActionsMixin(CoreStepActionsMixin):
    """Tunnel, review/write, and final-diagnostic handlers for the setup app."""

    def _tunnel_plan_from_widgets(self) -> TunnelProfilePlan:
        host = self._action_host()
        return build_tunnel_profile_plan(
            profile=host.query_one("#tunnel-profile", Input).value,
            tunnel_id=host.query_one("#tunnel-id", Input).value,
            config_path=host.config_path,
            project_root=Path.cwd(),
        )

    @on(Button.Pressed, "#tunnel-open-docs")
    def open_tunnel_docs(self) -> None:
        host = self._action_host()
        try:
            opened = open_setup_documentation(
                OPENAI_TUNNEL_DOC_URL,
                no_browser=host.no_browser,
                approved=True,
            )
        except SetupTunnelError as exc:
            host.query_one("#tunnel-message", Static).update(str(exc))
            return
        host.query_one("#tunnel-message", Static).update(
            "Browser opened." if opened else f"Open this URL manually: {OPENAI_TUNNEL_DOC_URL}"
        )

    @on(Button.Pressed, "#tunnel-prepare")
    def prepare_tunnel(self) -> None:
        host = self._action_host()
        host._pending_tunnel_plan = None
        host.query_one("#tunnel-apply", Button).disabled = True
        try:
            plan = self._tunnel_plan_from_widgets()
        except SetupTunnelError as exc:
            host.set_step_validation(SetupStep.CHATGPT, valid=False, message=str(exc))
            return
        host._pending_tunnel_plan = plan
        host.query_one("#tunnel-command", Static).update(
            "Server command: "
            f"{plan.server_command}\nReviewed profile command: {_command_text(plan.argv)}"
        )
        host.query_one("#tunnel-apply", Button).disabled = False
        host.set_step_validation(
            SetupStep.CHATGPT,
            valid=False,
            message="Review the tunnel profile command, then apply the stored plan separately.",
        )

    @on(Button.Pressed, "#tunnel-apply")
    def apply_tunnel(self) -> None:
        host = self._action_host()
        plan = host._pending_tunnel_plan
        if plan is None:
            host.set_step_validation(
                SetupStep.CHATGPT,
                valid=False,
                message="Prepare and review a tunnel profile plan before applying it.",
            )
            return
        try:
            apply_tunnel_profile(plan, approved=True)
        except SetupTunnelError as exc:
            host.query_one("#tunnel-message", Static).update(str(exc))
            return
        host._pending_tunnel_plan = None
        host.query_one("#tunnel-apply", Button).disabled = True
        host.query_one("#tunnel-message", Static).update(
            "Reviewed tunnel profile mutation completed; run validation next."
        )

    @on(Button.Pressed, "#validate-chatgpt")
    def validate_chatgpt(self) -> None:
        host = self._action_host()
        enabled = host.query_one("#chatgpt-enabled", Checkbox).value
        candidate = clone_setup_state(host.state)
        if not enabled:
            candidate.chatgpt_requested = False
            candidate.choices.tunnel_id = None
            candidate.choices.chatgpt_user_attested = False
            host._save_validated_state(
                candidate,
                step=SetupStep.CHATGPT,
                message="Optional ChatGPT connection deliberately skipped.",
                skipped=True,
            )
            return
        try:
            plan = self._tunnel_plan_from_widgets()
        except SetupTunnelError as exc:
            host.set_step_validation(SetupStep.CHATGPT, valid=False, message=str(exc))
            return
        host.query_one("#tunnel-command", Static).update("Server command: " + plan.server_command)
        health = check_tunnel_profile(plan.profile)
        attested = host.query_one("#chatgpt-attested", Checkbox).value
        host.query_one("#tunnel-message", Static).update(
            f"{'PASS' if health.healthy else 'ACTION REQUIRED'}: {health.message}\n"
            f"{'USER-ATTESTED' if attested else 'WAITING FOR USER'}: ChatGPT custom app/refresh"
        )
        if not health.healthy or not attested:
            host.set_step_validation(
                SetupStep.CHATGPT,
                valid=False,
                message=(
                    "Tunnel health and user attestation are required when ChatGPT "
                    "setup is selected."
                ),
            )
            return
        candidate.chatgpt_requested = True
        candidate.choices.tunnel_id = plan.tunnel_id
        candidate.choices.tunnel_profile = plan.profile
        candidate.choices.chatgpt_user_attested = True
        host._save_validated_state(
            candidate,
            step=SetupStep.CHATGPT,
            message=(
                "Tunnel is machine-healthy; ChatGPT UI completion is recorded as USER-ATTESTED."
            ),
        )

    @on(Button.Pressed, "#prepare-review")
    def prepare_review(self) -> None:
        host = self._action_host()
        try:
            inspection = inspect_config(host.config_path)
            repair_approved = host.query_one("#repair-invalid", Checkbox).value
            if inspection.exists and not inspection.valid and not repair_approved:
                raise SetupConfigError(
                    "Existing configuration is invalid; explicitly approve repair before review."
                )
            materialized = materialize_setup(host.state, new_config=not inspection.valid)
            plan = prepare_config_update(
                host.config_path,
                wizard_values=materialized.values,
                managed_fields=materialized.managed_fields,
                repair_invalid=repair_approved,
            )
        except (SetupConfigError, ValueError) as exc:
            host._review_plan = None
            host.query_one("#write-reviewed-config", Button).disabled = True
            host.set_step_validation(SetupStep.REVIEW, valid=False, message=str(exc))
            return
        host._review_plan = plan
        target = (
            f"Target: {host.config_path}\n"
            f"Status: {'existing' if plan.source_exists else 'new'}\n"
            f"Backup: "
            f"{'timestamped backup before replacement' if plan.source_exists else 'not needed'}"
        )
        host.query_one("#review-target", Static).update(target)
        host.query_one("#review-summary", Static).update("\n".join(plan.summary))
        host.query_one("#write-reviewed-config", Button).disabled = False
        host.set_step_validation(
            SetupStep.REVIEW,
            valid=False,
            message="Review prepared. Write reviewed configuration before continuing.",
        )

    @on(Button.Pressed, "#write-reviewed-config")
    def write_reviewed_config(self) -> None:
        host = self._action_host()
        if host._review_plan is None:
            host.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message="Prepare a fresh configuration review first.",
            )
            return
        try:
            result = commit_config_update(host.config_path, host._review_plan)
        except SetupConfigError as exc:
            host._review_plan = None
            host.query_one("#write-reviewed-config", Button).disabled = True
            host.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message=f"{exc}. Review again before any retry.",
            )
            return
        backup = str(result.backup_path) if result.backup_path is not None else "none"
        host.query_one("#write-result", Static).update(
            f"Configuration written and production-reloaded successfully. Backup: {backup}"
        )
        host.set_step_validation(
            SetupStep.REVIEW,
            valid=True,
            message="Reviewed configuration was written, byte-verified, and production-reloaded.",
        )

    @on(Button.Pressed, "#run-final-diagnostics")
    def run_final_diagnostics(self) -> None:
        host = self._action_host()
        report = run_diagnostics(
            host.config_path,
            tunnel_profile=(
                host.state.choices.tunnel_profile if host.state.chatgpt_requested else None
            ),
            tunnel_requested=host.state.chatgpt_requested,
        )
        host.query_one("#diagnostic-matrix", Static).update(format_diagnostics(report))
        attestation = (
            "USER-ATTESTED ChatGPT custom app/refresh: complete"
            if host.state.chatgpt_requested and host.state.choices.chatgpt_user_attested
            else "SKIPPED optional ChatGPT UI attestation"
            if not host.state.chatgpt_requested
            else "WAITING FOR USER ChatGPT custom app/refresh"
        )
        host.query_one("#attestation-summary", Static).update(attestation)
        host.query_one("#smoke-guidance", Static).update(
            "Copyable smoke checks:\n"
            f"dev-workspace-mcp setup --check --config {host.config_path}\n"
            f"dev-workspace-mcp serve --config {host.config_path}"
        )
        host.set_step_validation(
            SetupStep.FINAL_VALIDATION,
            valid=report.ready_to_finish,
            message=(
                "All mandatory machine-verifiable diagnostics pass."
                if report.ready_to_finish
                else "Mandatory diagnostics remain unresolved; remediate and rerun."
            ),
        )
