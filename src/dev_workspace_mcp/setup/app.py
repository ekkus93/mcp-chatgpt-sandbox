"""Textual setup wizard lifecycle and navigation shell."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Select, Static

from dev_workspace_mcp.config import ConfigError
from dev_workspace_mcp.setup.config_writer import ConfigUpdatePlan
from dev_workspace_mcp.setup.docker_setup import DockerBuildPlan
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    backup_regular_file,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.materialize import (
    fresh_choices,
    state_from_existing_config,
)
from dev_workspace_mcp.setup.model import (
    DEFAULT_SETUP_STATE_PATH,
    SetupState,
    SetupStateError,
    SetupStep,
    SetupStepStatus,
    clone_setup_state,
    invalidate_from_step,
    load_setup_state,
    rewind_for_missing_process_local_material,
    save_setup_state,
)
from dev_workspace_mcp.setup.runtime_setup import runtime_editor_values
from dev_workspace_mcp.setup.step_actions import SetupStepActionsMixin
from dev_workspace_mcp.setup.step_panels import WizardStepPanels
from dev_workspace_mcp.setup.tunnel import TunnelProfilePlan


@dataclass(frozen=True, slots=True)
class WizardStepDefinition:
    step: SetupStep
    title: str
    instructions: str


STEPS = (
    WizardStepDefinition(SetupStep.PREREQUISITES, "Local prerequisites", "Check this host."),
    WizardStepDefinition(SetupStep.STORAGE, "Server storage", "Choose protected host paths."),
    WizardStepDefinition(SetupStep.DOCKER, "Docker workspace", "Configure workspace runtime."),
    WizardStepDefinition(
        SetupStep.REPOSITORIES, "Repository access", "Register and verify local repositories."
    ),
    WizardStepDefinition(
        SetupStep.CREDENTIALS, "SSH and GitHub", "Configure isolated repository credentials."
    ),
    WizardStepDefinition(
        SetupStep.GIT_POLICY, "Git safety policy", "Review structured Git safety policy."
    ),
    WizardStepDefinition(
        SetupStep.RUNTIME_LIMITS,
        "Runtime and resources",
        "Keep defaults or review advanced runtime limits.",
    ),
    WizardStepDefinition(
        SetupStep.CHATGPT, "ChatGPT connection", "Optionally configure Secure MCP Tunnel."
    ),
    WizardStepDefinition(
        SetupStep.REVIEW, "Configuration review", "Review exact changes before any config write."
    ),
    WizardStepDefinition(
        SetupStep.FINAL_VALIDATION, "Final validation", "Run machine-verifiable diagnostics."
    ),
)

_DOCKER_BUILD_PLAN_CONTROL_IDS = frozenset(
    {
        "docker-image",
        "docker-workspace-path",
        "docker-user",
        "docker-git-dir-mode",
        "docker-idle-stop",
        "docker-defer-image",
    }
)
_TUNNEL_PROFILE_PLAN_CONTROL_IDS = frozenset({"chatgpt-enabled", "tunnel-id", "tunnel-profile"})


class SetupStartChoice(StrEnum):
    RESUME = "resume"
    REPAIR = "repair"
    EDIT = "edit"
    START_FRESH = "start_fresh"
    CANCEL = "cancel"


class ConfirmationDialog(ModalScreen[bool]):
    """Explicit confirmation for setup cancellation."""

    def __init__(self, title: str, message: str, *, confirm_label: str) -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmation-dialog"):
            yield Label(self.dialog_title)
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button("Keep working", id="confirmation-cancel")
                yield Button(self.confirm_label, id="confirmation-confirm", variant="warning")

    @on(Button.Pressed, "#confirmation-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirmation-confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class ExistingSetupDialog(ModalScreen[SetupStartChoice]):
    """Explicit lifecycle choice when setup/config material already exists."""

    def __init__(self, *, resume_available: bool, error_message: str | None = None) -> None:
        super().__init__()
        self.resume_available = resume_available
        self.error_message = error_message

    def compose(self) -> ComposeResult:
        with Vertical(id="existing-setup-dialog"):
            yield Label("Existing dev-workspace-mcp setup detected")
            yield Static(
                "Resume continues valid incomplete progress. Repair revalidates setup material. "
                "Edit starts from the current production-valid config. Start fresh resets only "
                "wizard progress; it never deletes repositories, keys, state DB contents, Docker "
                "volumes, or workspace containers.",
                markup=False,
            )
            if self.error_message:
                yield Static(self.error_message, id="existing-setup-error", markup=False)
            with Horizontal():
                resume = Button("Resume", id="welcome-resume", variant="primary")
                resume.disabled = not self.resume_available
                yield resume
                yield Button("Repair", id="welcome-repair")
                yield Button("Edit configuration", id="welcome-edit")
                yield Button("Start fresh", id="welcome-fresh", variant="warning")
                yield Button("Cancel", id="welcome-cancel")

    @on(Button.Pressed, "#welcome-resume")
    def choose_resume(self) -> None:
        self.dismiss(SetupStartChoice.RESUME)

    @on(Button.Pressed, "#welcome-repair")
    def choose_repair(self) -> None:
        self.dismiss(SetupStartChoice.REPAIR)

    @on(Button.Pressed, "#welcome-edit")
    def choose_edit(self) -> None:
        self.dismiss(SetupStartChoice.EDIT)

    @on(Button.Pressed, "#welcome-fresh")
    def choose_fresh(self) -> None:
        self.dismiss(SetupStartChoice.START_FRESH)

    @on(Button.Pressed, "#welcome-cancel")
    def choose_cancel(self) -> None:
        self.dismiss(SetupStartChoice.CANCEL)


def setup_state_is_incomplete(state: SetupState) -> bool:
    return state.statuses.get(SetupStep.FINAL_VALIDATION) is not SetupStepStatus.COMPLETE


def _path_present(path: Path) -> bool:
    try:
        path.expanduser().lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SetupStateError(f"Could not inspect setup path {path.expanduser()}") from exc
    return True


class SetupWizardApp(SetupStepActionsMixin, App[int]):
    """Reusable validation-gated setup navigation shell."""

    TITLE = "dev-workspace-mcp Setup"
    BINDINGS = [("escape", "request_cancel", "Cancel setup")]
    CSS = """
    #wizard-body { height: 1fr; }
    #progress-panel {
        width: 32;
        min-width: 22;
        height: 1fr;
        padding: 1;
        border-right: solid $surface-lighten-2;
    }
    #wizard-main { width: 1fr; height: 1fr; padding: 1 2; }
    #step-title { height: 3; text-style: bold; }
    #instructions-scroll { height: 1fr; border: round $surface-lighten-2; padding: 1 2; }
    #step-content { height: auto; margin-top: 1; }
    #runtime-advanced-fields { display: none; height: auto; }
    .step-panel { display: none; height: auto; }
    .step-panel Input { margin-bottom: 1; }
    .step-panel Select { margin-bottom: 1; }
    .step-panel Button { margin-top: 1; }
    #validation-message { height: auto; min-height: 2; margin-top: 1; }
    #wizard-controls { height: 3; align-horizontal: right; margin-top: 1; }
    #wizard-controls Button { margin-left: 1; }
    #confirmation-dialog, #existing-setup-dialog {
        width: 80%; max-width: 90; height: auto; padding: 1 2;
        border: round $accent; background: $surface;
    }
    ConfirmationDialog, ExistingSetupDialog { align: center middle; }
    """

    def __init__(
        self,
        *,
        config_path: Path,
        state_path: Path = DEFAULT_SETUP_STATE_PATH,
        resume: bool = False,
        no_browser: bool = False,
        advanced: bool = False,
    ) -> None:
        super().__init__()
        self.config_path = config_path.expanduser()
        self.state_path = state_path.expanduser()
        self.resume_requested = resume
        self.no_browser = no_browser
        self.advanced = advanced
        self.state = SetupState(advanced=advanced)
        self.setup_exit_code: int | None = None
        self.fatal_error: str | None = None
        self._current_index = 0
        self._validation: dict[SetupStep, bool] = {}
        self._validation_messages: dict[SetupStep, str] = {}
        self._loaded_state: SetupState | None = None
        self._state_load_error: str | None = None
        self._review_plan: ConfigUpdatePlan | None = None
        self._pending_docker_build_plan: DockerBuildPlan | None = None
        self._pending_tunnel_plan: TunnelProfilePlan | None = None
        self._populating = False

    @property
    def current_step(self) -> SetupStep:
        return STEPS[self._current_index].step

    def compose(self) -> ComposeResult:
        with Horizontal(id="wizard-body"):
            yield Static(id="progress-panel", markup=False)
            with Vertical(id="wizard-main"):
                yield Static(id="step-title", markup=False)
                with VerticalScroll(id="instructions-scroll"):
                    yield Static(id="instructions", markup=False)
                    yield WizardStepPanels(advanced=self.advanced)
                yield Static(id="validation-message", markup=False)
                with Horizontal(id="wizard-controls"):
                    yield Button("Back", id="back")
                    yield Button("Cancel", id="cancel")
                    yield Button("Next", id="next", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repository-table", DataTable)
        table.add_columns("ID", "Path", "GitHub", "SSH key")
        try:
            self._loaded_state = load_setup_state(self.state_path)
        except SetupStateError as exc:
            self._state_load_error = str(exc)

        if self.resume_requested:
            if self._loaded_state is None or not setup_state_is_incomplete(self._loaded_state):
                self._fatal(self._state_load_error or "No incomplete setup state is available.")
                return
            resumed = rewind_for_missing_process_local_material(
                self._loaded_state,
                required_steps={SetupStep.CHATGPT}
                if self._loaded_state.chatgpt_requested
                else set(),
                available_steps=set(),
            )
            self._activate(resumed)
            return

        try:
            config_exists = _path_present(self.config_path)
            state_exists = _path_present(self.state_path)
        except SetupStateError as exc:
            self._fatal(str(exc))
            return
        if config_exists or state_exists:
            self.push_screen(
                ExistingSetupDialog(
                    resume_available=self._loaded_state is not None
                    and setup_state_is_incomplete(self._loaded_state),
                    error_message=self._state_load_error,
                ),
                self._handle_start_choice,
            )
            return
        fresh = SetupState(
            advanced=self.advanced,
            config_path=str(self.config_path),
            choices=fresh_choices(self.config_path),
        )
        if self._save(fresh):
            self._activate(fresh)

    def _fatal(self, message: str) -> None:
        self.fatal_error = message
        self.setup_exit_code = 2
        self.exit(2, return_code=2)

    def _save(self, candidate: SetupState) -> bool:
        try:
            save_setup_state(candidate, self.state_path)
        except SetupStateError as exc:
            self._fatal(str(exc))
            return False
        return True

    def _activate(self, state: SetupState) -> None:
        candidate = clone_setup_state(state)
        self.state = candidate
        self._current_index = next(
            index
            for index, definition in enumerate(STEPS)
            if definition.step is candidate.current_step
        )
        self.state.statuses.setdefault(self.current_step, SetupStepStatus.ACTION_REQUIRED)
        self._populate_widgets()
        self._refresh()

    def _progress_text(self) -> str:
        lines: list[str] = []
        for index, definition in enumerate(STEPS):
            marker = ">" if index == self._current_index else " "
            status = self.state.statuses.get(definition.step, SetupStepStatus.NOT_STARTED)
            lines.append(
                f"{marker} [{index + 1}/{len(STEPS)}] {definition.title}\n"
                f"    {status.value.replace('_', ' ').upper()}"
            )
        return "\n".join(lines)

    def _refresh(self) -> None:
        definition = STEPS[self._current_index]
        self.state.current_step = definition.step
        self.query_one("#progress-panel", Static).update(self._progress_text())
        self.query_one("#step-title", Static).update(definition.title)
        self.query_one("#instructions", Static).update(definition.instructions)
        for panel in self.query(".step-panel"):
            panel.display = panel.id == f"panel-{definition.step.value}"
        valid = self._validation.get(
            definition.step,
            self.state.statuses.get(definition.step)
            in {
                SetupStepStatus.COMPLETE,
                SetupStepStatus.SKIPPED_OPTIONAL,
            },
        )
        message = self._validation_messages.get(
            definition.step,
            "Complete and validate this step before continuing." if not valid else "",
        )
        self.query_one("#validation-message", Static).update(message)
        self.query_one("#back", Button).disabled = self._current_index == 0
        next_button = self.query_one("#next", Button)
        next_button.disabled = not valid
        next_button.label = "Finish" if self._current_index == len(STEPS) - 1 else "Next"

    @staticmethod
    def _select_value(value: object, *, label: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be selected")
        return value

    @staticmethod
    def _runtime_section(values: dict[str, dict[str, object]], name: str) -> dict[str, object]:
        return values[name]

    @staticmethod
    def _display_runtime_value(value: object) -> str:
        return "" if value is None else str(value)

    def _populate_widgets(self) -> None:
        self._populating = True
        try:
            choices = self.state.choices
            input_values = {
                "#storage-state-db": choices.state_db_path or "",
                "#storage-audit": choices.audit_path or "",
                "#storage-keys": choices.keys_dir or "",
                "#storage-known-hosts": choices.known_hosts or "",
                "#docker-image": choices.docker_image,
                "#docker-workspace-path": choices.workspace_path,
                "#docker-idle-stop": choices.idle_stop_after,
                "#repository-roots": ", ".join(choices.repository_roots),
                "#ssh-shared-key": choices.shared_key or "",
                "#github-auth-file": choices.github_auth_file or "",
                "#github-api-base-url": choices.github_api_base_url,
                "#git-policy-custom": json.dumps(choices.git_policy_custom or {}, sort_keys=True),
                "#tunnel-id": choices.tunnel_id or "",
                "#tunnel-profile": choices.tunnel_profile,
            }
            runtime_values = runtime_editor_values(choices.runtime_overrides)
            filesystem = self._runtime_section(runtime_values, "filesystem")
            execution = self._runtime_section(runtime_values, "execution")
            resources = self._runtime_section(runtime_values, "resources")
            input_values.update(
                {
                    "#runtime-fs-max-read-bytes": self._display_runtime_value(
                        filesystem["max_read_bytes"]
                    ),
                    "#runtime-fs-max-write-bytes": self._display_runtime_value(
                        filesystem["max_write_bytes"]
                    ),
                    "#runtime-fs-max-patch-bytes": self._display_runtime_value(
                        filesystem["max_patch_bytes"]
                    ),
                    "#runtime-fs-max-listing-entries": self._display_runtime_value(
                        filesystem["max_listing_entries"]
                    ),
                    "#runtime-fs-max-search-results": self._display_runtime_value(
                        filesystem["max_search_results"]
                    ),
                    "#runtime-exec-sync-timeout-seconds": self._display_runtime_value(
                        execution["sync_timeout_seconds"]
                    ),
                    "#runtime-exec-default-job-timeout-seconds": self._display_runtime_value(
                        execution["default_job_timeout_seconds"]
                    ),
                    "#runtime-exec-max-output-bytes": self._display_runtime_value(
                        execution["max_output_bytes"]
                    ),
                    "#runtime-exec-tail-keep-bytes": self._display_runtime_value(
                        execution["tail_keep_bytes"]
                    ),
                    "#runtime-exec-response-tail-lines": self._display_runtime_value(
                        execution["response_tail_lines"]
                    ),
                    "#runtime-exec-job-retention-days": self._display_runtime_value(
                        execution["job_retention_days"]
                    ),
                    "#runtime-exec-job-log-max-total-bytes": self._display_runtime_value(
                        execution["job_log_max_total_bytes"]
                    ),
                    "#runtime-exec-default-output-response-bytes": self._display_runtime_value(
                        execution["default_output_response_bytes"]
                    ),
                    "#runtime-resources-cpus": self._display_runtime_value(resources["cpus"]),
                    "#runtime-resources-memory": self._display_runtime_value(resources["memory"]),
                    "#runtime-resources-pids-limit": self._display_runtime_value(
                        resources["pids_limit"]
                    ),
                }
            )
            for selector, value in input_values.items():
                self.query_one(selector, Input).value = value

            select_values = {
                "#docker-user": choices.container_user,
                "#docker-git-dir-mode": choices.git_dir_mode,
                "#ssh-mode": choices.ssh_mode,
                "#ssh-scope": choices.ssh_scope,
                "#github-api-auth": choices.github_api_auth,
                "#git-policy-preset": choices.git_policy_preset,
                "#runtime-logging-level": self._select_value(
                    self._runtime_section(runtime_values, "logging")["level"],
                    label="logging level",
                ),
            }
            for selector, value in select_values.items():
                self.query_one(selector, Select).value = value

            edit_runtime = bool(choices.runtime_overrides) or self.advanced
            self.query_one("#runtime-edit", Checkbox).value = edit_runtime
            self.query_one("#runtime-advanced-fields", Vertical).display = edit_runtime
            self.query_one("#chatgpt-enabled", Checkbox).value = self.state.chatgpt_requested
            self.query_one("#chatgpt-attested", Checkbox).value = choices.chatgpt_user_attested
            self._refresh_repository_table()
        finally:
            self._populating = False

    def _refresh_repository_table(self) -> None:
        table = self.query_one("#repository-table", DataTable)
        table.clear(columns=False)
        for repository in self.state.repositories:
            table.add_row(
                repository.id,
                repository.path,
                repository.github,
                repository.ssh_key or "",
            )

    def _clear_pending_reviewed_host_mutation(self, widget_id: str | None) -> None:
        clear_docker = widget_id is None or widget_id in _DOCKER_BUILD_PLAN_CONTROL_IDS
        clear_tunnel = widget_id is None or widget_id in _TUNNEL_PROFILE_PLAN_CONTROL_IDS
        if clear_docker:
            self._pending_docker_build_plan = None
            if self.is_running:
                self.query_one("#build-docker-image", Button).disabled = True
        if clear_tunnel:
            self._pending_tunnel_plan = None
            if self.is_running:
                self.query_one("#tunnel-apply", Button).disabled = True

    def _invalidate_review(self) -> None:
        self._review_plan = None
        self._validation.pop(SetupStep.REVIEW, None)
        self._validation.pop(SetupStep.FINAL_VALIDATION, None)
        if self.is_running:
            self.query_one("#write-reviewed-config", Button).disabled = True

    def _mark_changed(self, step: SetupStep) -> None:
        candidate = invalidate_from_step(self.state, step)
        self.state = candidate
        self._validation.pop(step, None)
        self._invalidate_review()
        self._save(candidate)
        if step is self.current_step:
            self._refresh()

    def _mark_widget_changed(self, step: SetupStep, widget_id: str | None) -> None:
        self._clear_pending_reviewed_host_mutation(widget_id)
        self._mark_changed(step)

    def _save_validated_state(
        self,
        candidate: SetupState,
        *,
        step: SetupStep,
        message: str,
        skipped: bool = False,
    ) -> None:
        candidate.statuses[step] = (
            SetupStepStatus.SKIPPED_OPTIONAL if skipped else SetupStepStatus.ACTION_REQUIRED
        )
        if self._save(candidate):
            self.state = candidate
            self.set_step_validation(step, valid=True, message=message)

    def set_step_validation(
        self, step: SetupStep, *, valid: bool, message: str | None = None
    ) -> None:
        self._validation[step] = valid
        if message is None:
            self._validation_messages.pop(step, None)
        else:
            self._validation_messages[step] = message
        if step is self.current_step:
            self._refresh()

    def _is_current_step_control(self, widget: DOMNode) -> bool:
        """Return True only for controls owned by the currently visible step panel.

        Textual may deliver queued Changed events after navigation. Hidden panels
        remain mounted, so accepting any descendant of ``#step-content`` lets a
        stale event invalidate the newly active step. Bind invalidation to the
        control's owning panel instead.
        """

        expected_panel_id = f"panel-{self.current_step.value}"
        node: DOMNode | None = widget
        while node is not None:
            if node.id == expected_panel_id:
                return True
            if node.id == "step-content":
                return False
            node = node.parent
        return False

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        if self._populating or not self._is_current_step_control(event.input):
            return
        if self.current_step not in {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}:
            self._mark_widget_changed(self.current_step, event.input.id)

    @on(Checkbox.Changed)
    def checkbox_changed(self, event: Checkbox.Changed) -> None:
        if self._populating or not self._is_current_step_control(event.checkbox):
            return
        if event.checkbox.id == "runtime-edit":
            self.query_one("#runtime-advanced-fields", Vertical).display = event.checkbox.value
        if self.current_step not in {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}:
            self._mark_widget_changed(self.current_step, event.checkbox.id)

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        if self._populating or not self._is_current_step_control(event.select):
            return
        if self.current_step not in {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}:
            self._mark_widget_changed(self.current_step, event.select.id)

    @on(Button.Pressed, "#back")
    def go_back(self) -> None:
        if self._current_index == 0:
            return
        candidate = clone_setup_state(self.state)
        self._current_index -= 1
        candidate.current_step = STEPS[self._current_index].step
        if self._save(candidate):
            self.state = candidate
            self._refresh()

    @on(Button.Pressed, "#next")
    def go_next(self) -> None:
        step = self.current_step
        valid = self._validation.get(
            step,
            self.state.statuses.get(step)
            in {
                SetupStepStatus.COMPLETE,
                SetupStepStatus.SKIPPED_OPTIONAL,
            },
        )
        if not valid:
            self._refresh()
            return
        candidate = clone_setup_state(self.state)
        candidate.statuses[step] = (
            SetupStepStatus.SKIPPED_OPTIONAL
            if candidate.statuses.get(step) is SetupStepStatus.SKIPPED_OPTIONAL
            else SetupStepStatus.COMPLETE
        )
        if self._current_index == len(STEPS) - 1:
            if self._save(candidate):
                self.state = candidate
                self.setup_exit_code = 0
                self.exit(0, return_code=0)
            return
        self._current_index += 1
        candidate.current_step = STEPS[self._current_index].step
        candidate.statuses.setdefault(candidate.current_step, SetupStepStatus.ACTION_REQUIRED)
        if self._save(candidate):
            self.state = candidate
            self._refresh()

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_request_cancel()

    def action_request_cancel(self) -> None:
        self.push_screen(
            ConfirmationDialog(
                "Cancel setup?",
                "Existing configuration is not deleted. Non-secret progress remains resumable.",
                confirm_label="Cancel setup",
            ),
            self._handle_cancel,
        )

    def _handle_cancel(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        self._save(clone_setup_state(self.state))
        self.setup_exit_code = 130
        self.exit(130, return_code=130)

    def _handle_start_choice(self, choice: SetupStartChoice | None) -> None:
        if choice in {None, SetupStartChoice.CANCEL}:
            self.setup_exit_code = 130
            self.exit(130, return_code=130)
            return
        try:
            if choice is SetupStartChoice.RESUME:
                if self._loaded_state is None or not setup_state_is_incomplete(self._loaded_state):
                    self._fatal("No incomplete setup state is available to resume.")
                    return
                resumed = rewind_for_missing_process_local_material(
                    self._loaded_state,
                    required_steps={SetupStep.CHATGPT}
                    if self._loaded_state.chatgpt_requested
                    else set(),
                    available_steps=set(),
                )
                self._activate(resumed)
                return
            if choice is SetupStartChoice.EDIT:
                state = state_from_existing_config(self.config_path, advanced=self.advanced)
                if self._save(state):
                    self._activate(state)
                return
            if choice is SetupStartChoice.REPAIR:
                try:
                    state = state_from_existing_config(self.config_path, advanced=self.advanced)
                except ConfigError:
                    state = self._loaded_state or SetupState(
                        advanced=self.advanced,
                        config_path=str(self.config_path),
                        choices=fresh_choices(self.config_path),
                    )
                if self._save(state):
                    self._activate(state)
                return
            if choice is SetupStartChoice.START_FRESH:
                reject_unsafe_sensitive_target(self.state_path)
                if _path_present(self.state_path):
                    backup_regular_file(self.state_path)
                state = SetupState(
                    advanced=self.advanced,
                    config_path=str(self.config_path),
                    choices=fresh_choices(self.config_path),
                )
                if self._save(state):
                    self._activate(state)
                return
        except (ConfigError, SetupFilesystemError, SetupStateError) as exc:
            self._fatal(str(exc))


def run_setup_app(
    *,
    config_path: Path,
    resume: bool = False,
    no_browser: bool = False,
    advanced: bool = False,
) -> int:
    """Run setup synchronously and return documented process-style exit code."""

    app = SetupWizardApp(
        config_path=config_path,
        resume=resume,
        no_browser=no_browser,
        advanced=advanced,
    )
    result = app.run()
    if app.fatal_error is not None:
        print(f"dev-workspace-mcp setup failed: {app.fatal_error}", file=sys.stderr)
    if app.setup_exit_code is not None:
        return app.setup_exit_code
    return 0 if result is None else result
