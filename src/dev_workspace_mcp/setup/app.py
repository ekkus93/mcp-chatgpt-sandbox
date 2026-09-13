"""Textual setup wizard lifecycle and concrete guided step actions."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import docker
from docker.errors import DockerException
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Select, Static

from dev_workspace_mcp.config import ConfigError
from dev_workspace_mcp.setup.config_writer import (
    ConfigUpdatePlan,
    SetupConfigError,
    commit_config_update,
    inspect_config,
    prepare_config_update,
)
from dev_workspace_mcp.setup.diagnostics import format_diagnostics, run_diagnostics
from dev_workspace_mcp.setup.docker_setup import (
    DockerSetupChoices,
    SetupDockerError,
    build_default_image,
    default_image_build_command,
    image_exists,
)
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    backup_regular_file,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.git_policy_setup import GitPolicyPreset, materialize_policy
from dev_workspace_mcp.setup.host import (
    SetupHostError,
    StorageChoices,
    check_prerequisites,
    parent_is_writable,
    prepare_storage_directories,
)
from dev_workspace_mcp.setup.materialize import (
    fresh_choices,
    materialize_setup,
    state_from_existing_config,
)
from dev_workspace_mcp.setup.model import (
    DEFAULT_SETUP_STATE_PATH,
    SetupRepositoryState,
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
from dev_workspace_mcp.setup.repository_setup import (
    SetupRepositoryError,
    add_repository,
    edit_repository,
    remove_repository,
    validate_repository_roots,
    verify_repository,
)
from dev_workspace_mcp.setup.runtime_setup import (
    RuntimeChoices,
    SetupRuntimeError,
    runtime_editor_values,
)
from dev_workspace_mcp.setup.ssh_github_setup import (
    CredentialChoices,
    SetupCredentialError,
    verify_credential_files,
)
from dev_workspace_mcp.setup.step_panels import WizardStepPanels
from dev_workspace_mcp.setup.tunnel import (
    OPENAI_TUNNEL_DOC_URL,
    SetupTunnelError,
    TunnelProfilePlan,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
    check_tunnel_profile,
    open_setup_documentation,
)


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


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


class SetupWizardApp(App[int]):
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

        Textual may deliver queued Changed events after navigation. Hidden panels remain mounted, so
        accepting any descendant of ``#step-content`` lets a stale event invalidate the newly active
        step. Bind invalidation to the control's owning panel instead.
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
            self._mark_changed(self.current_step)

    @on(Checkbox.Changed)
    def checkbox_changed(self, event: Checkbox.Changed) -> None:
        if self._populating or not self._is_current_step_control(event.checkbox):
            return
        if event.checkbox.id == "runtime-edit":
            self.query_one("#runtime-advanced-fields", Vertical).display = event.checkbox.value
        if self.current_step not in {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}:
            self._mark_changed(self.current_step)

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        if self._populating or not self._is_current_step_control(event.select):
            return
        if self.current_step not in {SetupStep.REVIEW, SetupStep.FINAL_VALIDATION}:
            self._mark_changed(self.current_step)

    @on(Button.Pressed, "#validate-prerequisites")
    def validate_prerequisites(self) -> None:
        report = check_prerequisites()
        text = (
            f"Python supported: {'PASS' if report.python_supported else 'FAIL'}\n"
            f"uv: {report.uv_path or 'not found (only required for source workflows)'}\n"
            f"Docker daemon: {'PASS' if report.docker_reachable else 'ACTION REQUIRED at final validation'}"
        )
        self.query_one("#prerequisite-report", Static).update(text)
        self.set_step_validation(
            SetupStep.PREREQUISITES,
            valid=not report.fatal,
            message=(
                "Host prerequisite checks completed."
                if not report.fatal
                else "Supported Python runtime is required."
            ),
        )

    def _storage_from_widgets(self) -> StorageChoices:
        return StorageChoices(
            state_db_path=Path(self.query_one("#storage-state-db", Input).value).expanduser(),
            audit_path=Path(self.query_one("#storage-audit", Input).value).expanduser(),
            keys_dir=Path(self.query_one("#storage-keys", Input).value).expanduser(),
            known_hosts=Path(self.query_one("#storage-known-hosts", Input).value).expanduser(),
        )

    @on(Button.Pressed, "#validate-storage")
    def validate_storage(self) -> None:
        try:
            choices = self._storage_from_widgets()
            choices.validate()
            targets = (
                choices.state_db_path,
                choices.audit_path,
                choices.keys_dir,
                choices.known_hosts,
            )
            if not all(parent_is_writable(target) for target in targets):
                raise SetupHostError("One or more selected storage parents are not writable")
        except (SetupHostError, ValueError) as exc:
            self.set_step_validation(SetupStep.STORAGE, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(self.state)
        candidate.choices.state_db_path = str(choices.state_db_path)
        candidate.choices.audit_path = str(choices.audit_path)
        candidate.choices.keys_dir = str(choices.keys_dir)
        candidate.choices.known_hosts = str(choices.known_hosts)
        candidate.reviewed_fields |= frozenset({"state_db_path", "audit"})
        self._save_validated_state(
            candidate,
            step=SetupStep.STORAGE,
            message="Storage paths are production-valid and their parents are writable.",
        )

    @on(Button.Pressed, "#create-storage")
    def create_storage(self) -> None:
        try:
            choices = self._storage_from_widgets()
            prepare_storage_directories(choices, approved=True)
        except SetupHostError as exc:
            self.set_step_validation(SetupStep.STORAGE, valid=False, message=str(exc))
            return
        self.query_one("#validation-message", Static).update(
            "Reviewed missing storage directories were created with restrictive permissions."
        )

    def _selected(self, selector: str, *, label: str) -> str:
        return self._select_value(self.query_one(selector, Select).value, label=label)

    def _docker_from_widgets(self) -> DockerSetupChoices:
        return DockerSetupChoices(
            default_image=self.query_one("#docker-image", Input).value.strip(),
            workspace_path=self.query_one("#docker-workspace-path", Input).value.strip(),
            container_user=self._selected("#docker-user", label="container user"),
            git_dir_mode=self._selected("#docker-git-dir-mode", label="Git directory mode"),
            idle_stop_after=self.query_one("#docker-idle-stop", Input).value.strip(),
        )

    @on(Button.Pressed, "#validate-docker")
    def validate_docker(self) -> None:
        try:
            choices = self._docker_from_widgets()
            choices.config_fragments()
            defer_image = self.query_one("#docker-defer-image", Checkbox).value
            client = docker.from_env()
            try:
                present = image_exists(client, choices.default_image)
            finally:
                client.close()
            if not present and not defer_image:
                raise SetupDockerError(
                    "Selected image is missing; build it or explicitly defer to final diagnostics"
                )
        except (DockerException, ValueError):
            present = False
            message = (
                "Docker is unavailable or a selection is invalid; workspace configuration remains "
                "action-required until corrected or Docker-dependent checks can run."
            )
            if isinstance(sys.exc_info()[1], ValueError):
                self.set_step_validation(SetupStep.DOCKER, valid=False, message=message)
                return
        except SetupDockerError as exc:
            self.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        else:
            message = (
                "Docker workspace values are valid and the selected image is present."
                if present
                else "Docker workspace values are valid; missing image explicitly deferred."
            )
        command = " ".join(default_image_build_command(choices.default_image))
        self.query_one("#docker-build-command", Static).update(f"Reviewed build action: {command}")
        candidate = clone_setup_state(self.state)
        candidate.choices.docker_image = choices.default_image
        candidate.choices.workspace_path = choices.workspace_path
        candidate.choices.container_user = choices.container_user
        candidate.choices.git_dir_mode = choices.git_dir_mode
        candidate.choices.idle_stop_after = choices.idle_stop_after
        candidate.reviewed_fields |= frozenset({"docker", "workspace", "container"})
        self._save_validated_state(candidate, step=SetupStep.DOCKER, message=message)

    @on(Button.Pressed, "#build-docker-image")
    def build_docker_image(self) -> None:
        try:
            choices = self._docker_from_widgets()
        except ValueError as exc:
            self.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        self.query_one("#docker-build-command", Static).update(
            "Reviewed build action: " + " ".join(default_image_build_command(choices.default_image))
        )
        try:
            client = docker.from_env()
            try:
                build_default_image(
                    client,
                    image=choices.default_image,
                    project_root=Path.cwd(),
                    approved=True,
                )
            finally:
                client.close()
        except (DockerException, SetupDockerError) as exc:
            self.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        self.validate_docker()

    def _repository_from_widgets(self) -> SetupRepositoryState:
        return SetupRepositoryState.from_dict(
            {
                "id": self.query_one("#repo-id", Input).value.strip(),
                "path": self.query_one("#repo-path", Input).value.strip(),
                "github": self.query_one("#repo-github", Input).value.strip(),
                "ssh_key": _blank_to_none(self.query_one("#repo-ssh-key", Input).value),
            }
        )

    @on(Button.Pressed, "#repo-add")
    def add_or_replace_repository(self) -> None:
        try:
            repository = self._repository_from_widgets()
            existing = any(item.id == repository.id for item in self.state.repositories)
            repositories = (
                edit_repository(
                    self.state.repositories,
                    repository_id=repository.id,
                    replacement=repository,
                )
                if existing
                else add_repository(self.state.repositories, repository)
            )
        except (ValueError, SetupRepositoryError) as exc:
            self.query_one("#repository-message", Static).update(str(exc))
            return
        candidate = invalidate_from_step(self.state, SetupStep.REPOSITORIES)
        candidate.repositories = repositories
        candidate.reviewed_fields |= frozenset({"repositories"})
        if self._save(candidate):
            self.state = candidate
            self._refresh_repository_table()
            self.query_one("#repository-message", Static).update(
                "Repository record saved; validate repository configuration before continuing."
            )
            self._refresh()

    @on(Button.Pressed, "#repo-verify")
    def verify_entered_repository(self) -> None:
        try:
            report = verify_repository(self._repository_from_widgets())
        except (ValueError, SetupRepositoryError) as exc:
            self.query_one("#repository-message", Static).update(str(exc))
            return
        self.query_one("#repository-message", Static).update(report.message)

    @on(Button.Pressed, "#repo-remove")
    def remove_entered_repository(self) -> None:
        repository_id = self.query_one("#repo-remove-id", Input).value.strip()
        confirmed = self.query_one("#repo-confirm-remove", Checkbox).value
        try:
            repositories = remove_repository(
                self.state.repositories,
                repository_id=repository_id,
                confirmed=confirmed,
            )
        except SetupRepositoryError as exc:
            self.query_one("#repository-message", Static).update(str(exc))
            return
        candidate = invalidate_from_step(self.state, SetupStep.REPOSITORIES)
        candidate.repositories = repositories
        candidate.reviewed_fields |= frozenset({"repositories"})
        if self._save(candidate):
            self.state = candidate
            self._refresh_repository_table()
            self.query_one("#repo-confirm-remove", Checkbox).value = False
            self.query_one("#repository-message", Static).update("Repository record removed.")
            self._refresh()

    def _repository_roots_from_widget(self) -> tuple[Path, ...]:
        value = self.query_one("#repository-roots", Input).value
        roots = [Path(item.strip()).expanduser() for item in value.split(",") if item.strip()]
        return validate_repository_roots(roots)

    @on(Button.Pressed, "#validate-repositories")
    def validate_repositories(self) -> None:
        try:
            roots = self._repository_roots_from_widget()
            reports = tuple(verify_repository(repository) for repository in self.state.repositories)
            invalid = [report.repository.id for report in reports if not report.valid]
            if invalid:
                raise SetupRepositoryError(
                    "Repository verification is unresolved for: " + ", ".join(invalid)
                )
        except (ValueError, SetupRepositoryError) as exc:
            self.set_step_validation(SetupStep.REPOSITORIES, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(self.state)
        candidate.choices.repository_roots = tuple(str(root) for root in roots)
        candidate.reviewed_fields |= frozenset({"repositories", "repository_roots"})
        self._save_validated_state(
            candidate,
            step=SetupStep.REPOSITORIES,
            message="Repository records and discovery roots are read-only verified.",
        )

    def _credential_choices_from_widgets(self) -> CredentialChoices:
        choices = self.state.choices
        if choices.keys_dir is None or choices.known_hosts is None:
            raise SetupCredentialError("Storage step must select keys_dir and known_hosts first")
        auth_file = _blank_to_none(self.query_one("#github-auth-file", Input).value)
        try:
            mode = self._selected("#ssh-mode", label="SSH mode")
            scope = self._selected("#ssh-scope", label="SSH scope")
            api_auth = self._selected("#github-api-auth", label="GitHub API authentication")
        except ValueError as exc:
            raise SetupCredentialError(str(exc)) from exc
        return CredentialChoices(
            mode=mode,  # type: ignore[arg-type]
            scope=scope,  # type: ignore[arg-type]
            keys_dir=Path(choices.keys_dir),
            known_hosts=Path(choices.known_hosts),
            shared_key=_blank_to_none(self.query_one("#ssh-shared-key", Input).value),
            github_api_auth=api_auth,  # type: ignore[arg-type]
            github_api_token_path=Path(auth_file).expanduser() if auth_file is not None else None,
            github_api_base_url=self.query_one("#github-api-base-url", Input).value.strip(),
        )

    @on(Button.Pressed, "#validate-credentials")
    def validate_credentials(self) -> None:
        try:
            choices = self._credential_choices_from_widgets()
            choices.config_fragments()
            checks = verify_credential_files(choices, self.state.repositories)
        except SetupCredentialError as exc:
            self.set_step_validation(SetupStep.CREDENTIALS, valid=False, message=str(exc))
            return
        blocking = [check.message for check in checks if check.status == "action_required"]
        self.query_one("#credential-message", Static).update(
            "\n".join(f"{check.status.upper()}: {check.message}" for check in checks)
        )
        if blocking:
            self.set_step_validation(
                SetupStep.CREDENTIALS,
                valid=False,
                message="Credential references still require action.",
            )
            return
        candidate = clone_setup_state(self.state)
        candidate.choices.ssh_mode = choices.mode
        candidate.choices.ssh_scope = choices.scope
        candidate.choices.shared_key = choices.shared_key
        candidate.choices.github_api_auth = choices.github_api_auth
        candidate.choices.github_auth_file = (
            str(choices.github_api_token_path)
            if choices.github_api_token_path is not None
            else None
        )
        candidate.choices.github_api_base_url = choices.github_api_base_url
        candidate.reviewed_fields |= frozenset({"ssh", "github"})
        self._save_validated_state(
            candidate,
            step=SetupStep.CREDENTIALS,
            message="Credential references are valid; optional API uncertainty remains explicit.",
        )

    @on(Button.Pressed, "#validate-git-policy")
    def validate_git_policy(self) -> None:
        try:
            raw_preset = self._selected("#git-policy-preset", label="Git safety preset")
            preset = GitPolicyPreset(raw_preset)
            custom: dict[str, object] | None = None
            if preset is GitPolicyPreset.CUSTOM:
                raw = json.loads(self.query_one("#git-policy-custom", Input).value)
                if not isinstance(raw, dict):
                    raise ValueError("Custom Git policy JSON must be an object")
                custom = raw
            policy = materialize_policy(preset, custom=custom)
        except (ValueError, json.JSONDecodeError) as exc:
            self.set_step_validation(SetupStep.GIT_POLICY, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(self.state)
        candidate.choices.git_policy_preset = preset.value
        candidate.choices.git_policy_custom = (
            policy.model_dump(mode="json") if preset is GitPolicyPreset.CUSTOM else None
        )
        candidate.reviewed_fields |= frozenset({"git_policy"})
        self.query_one("#git-policy-message", Static).update(
            "Validated production Git policy: " + json.dumps(policy.model_dump(mode="json"))
        )
        self._save_validated_state(
            candidate,
            step=SetupStep.GIT_POLICY,
            message="Git safety policy is production-valid.",
        )

    def _required_int(self, selector: str, *, label: str) -> int:
        raw = self.query_one(selector, Input).value.strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise SetupRuntimeError(f"{label} must be an integer") from exc

    def _optional_int(self, selector: str, *, label: str) -> int | None:
        raw = self.query_one(selector, Input).value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise SetupRuntimeError(f"{label} must be an integer or blank") from exc

    def _optional_float(self, selector: str, *, label: str) -> float | None:
        raw = self.query_one(selector, Input).value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError as exc:
            raise SetupRuntimeError(f"{label} must be a number or blank") from exc

    def _runtime_choices_from_widgets(self) -> RuntimeChoices:
        level = self._selected("#runtime-logging-level", label="logging level")
        memory = _blank_to_none(self.query_one("#runtime-resources-memory", Input).value)
        return RuntimeChoices(
            filesystem={
                "max_read_bytes": self._required_int(
                    "#runtime-fs-max-read-bytes", label="Maximum read bytes"
                ),
                "max_write_bytes": self._required_int(
                    "#runtime-fs-max-write-bytes", label="Maximum write bytes"
                ),
                "max_patch_bytes": self._required_int(
                    "#runtime-fs-max-patch-bytes", label="Maximum patch bytes"
                ),
                "max_listing_entries": self._required_int(
                    "#runtime-fs-max-listing-entries", label="Maximum listing entries"
                ),
                "max_search_results": self._required_int(
                    "#runtime-fs-max-search-results", label="Maximum search results"
                ),
            },
            execution={
                "sync_timeout_seconds": self._required_int(
                    "#runtime-exec-sync-timeout-seconds", label="Synchronous timeout"
                ),
                "default_job_timeout_seconds": self._required_int(
                    "#runtime-exec-default-job-timeout-seconds",
                    label="Default job timeout",
                ),
                "max_output_bytes": self._required_int(
                    "#runtime-exec-max-output-bytes", label="Maximum captured output bytes"
                ),
                "tail_keep_bytes": self._required_int(
                    "#runtime-exec-tail-keep-bytes", label="Tail keep bytes"
                ),
                "response_tail_lines": self._required_int(
                    "#runtime-exec-response-tail-lines", label="Response tail lines"
                ),
                "job_retention_days": self._required_int(
                    "#runtime-exec-job-retention-days", label="Job retention days"
                ),
                "job_log_max_total_bytes": self._required_int(
                    "#runtime-exec-job-log-max-total-bytes", label="Maximum total job-log bytes"
                ),
                "default_output_response_bytes": self._required_int(
                    "#runtime-exec-default-output-response-bytes",
                    label="Default output response bytes",
                ),
            },
            resources={
                "cpus": self._optional_float("#runtime-resources-cpus", label="CPUs"),
                "memory": memory,
                "pids_limit": self._optional_int(
                    "#runtime-resources-pids-limit", label="PID limit"
                ),
            },
            logging={"level": level},
        )

    @on(Button.Pressed, "#validate-runtime")
    def validate_runtime(self) -> None:
        edit = self.query_one("#runtime-edit", Checkbox).value
        candidate = clone_setup_state(self.state)
        runtime_fields = frozenset({"filesystem", "execution", "resources", "logging"})
        if not edit:
            candidate.reviewed_fields -= runtime_fields
            candidate.choices.runtime_overrides = None
            self._save_validated_state(
                candidate,
                step=SetupStep.RUNTIME_LIMITS,
                message=(
                    "Advanced runtime values remain untouched and use production "
                    "defaults/existing values."
                ),
            )
            return
        try:
            fragments = self._runtime_choices_from_widgets().reviewed_fragments()
        except (ValueError, SetupRuntimeError) as exc:
            self.set_step_validation(SetupStep.RUNTIME_LIMITS, valid=False, message=str(exc))
            return
        candidate.choices.runtime_overrides = fragments
        candidate.reviewed_fields = (candidate.reviewed_fields - runtime_fields) | frozenset(
            fragments
        )
        self.query_one("#runtime-message", Static).update(
            "Reviewed advanced sections: " + ", ".join(sorted(fragments))
        )
        self._save_validated_state(
            candidate,
            step=SetupStep.RUNTIME_LIMITS,
            message="Advanced runtime values are production-valid.",
        )

    def _tunnel_plan_from_widgets(self) -> TunnelProfilePlan:
        tunnel_id = self.query_one("#tunnel-id", Input).value
        profile = self.query_one("#tunnel-profile", Input).value
        return build_tunnel_profile_plan(
            profile=profile,
            tunnel_id=tunnel_id,
            config_path=self.config_path,
            project_root=Path.cwd(),
        )

    @on(Button.Pressed, "#tunnel-open-docs")
    def open_tunnel_docs(self) -> None:
        try:
            opened = open_setup_documentation(
                OPENAI_TUNNEL_DOC_URL,
                no_browser=self.no_browser,
                approved=True,
            )
        except SetupTunnelError as exc:
            self.query_one("#tunnel-message", Static).update(str(exc))
            return
        self.query_one("#tunnel-message", Static).update(
            "Browser opened." if opened else f"Open this URL manually: {OPENAI_TUNNEL_DOC_URL}"
        )

    @on(Button.Pressed, "#tunnel-apply")
    def apply_tunnel(self) -> None:
        try:
            plan = self._tunnel_plan_from_widgets()
            self.query_one("#tunnel-command", Static).update(
                "Reviewed profile command: " + " ".join(plan.argv)
            )
            apply_tunnel_profile(plan, approved=True)
        except SetupTunnelError as exc:
            self.query_one("#tunnel-message", Static).update(str(exc))
            return
        self.query_one("#tunnel-message", Static).update(
            "Reviewed tunnel profile mutation completed; run validation next."
        )

    @on(Button.Pressed, "#validate-chatgpt")
    def validate_chatgpt(self) -> None:
        enabled = self.query_one("#chatgpt-enabled", Checkbox).value
        candidate = clone_setup_state(self.state)
        if not enabled:
            candidate.chatgpt_requested = False
            candidate.choices.tunnel_id = None
            candidate.choices.chatgpt_user_attested = False
            self._save_validated_state(
                candidate,
                step=SetupStep.CHATGPT,
                message="Optional ChatGPT connection deliberately skipped.",
                skipped=True,
            )
            return
        try:
            plan = self._tunnel_plan_from_widgets()
        except SetupTunnelError as exc:
            self.set_step_validation(SetupStep.CHATGPT, valid=False, message=str(exc))
            return
        self.query_one("#tunnel-command", Static).update("Server command: " + plan.server_command)
        health = check_tunnel_profile(plan.profile)
        attested = self.query_one("#chatgpt-attested", Checkbox).value
        self.query_one("#tunnel-message", Static).update(
            f"{'PASS' if health.healthy else 'ACTION REQUIRED'}: {health.message}\n"
            f"{'USER-ATTESTED' if attested else 'WAITING FOR USER'}: ChatGPT custom app/refresh"
        )
        if not health.healthy or not attested:
            self.set_step_validation(
                SetupStep.CHATGPT,
                valid=False,
                message=(
                    "Tunnel health and user attestation are required when ChatGPT setup is selected."
                ),
            )
            return
        candidate.chatgpt_requested = True
        candidate.choices.tunnel_id = plan.tunnel_id
        candidate.choices.tunnel_profile = plan.profile
        candidate.choices.chatgpt_user_attested = True
        self._save_validated_state(
            candidate,
            step=SetupStep.CHATGPT,
            message=(
                "Tunnel is machine-healthy; ChatGPT UI completion is recorded as USER-ATTESTED."
            ),
        )

    @on(Button.Pressed, "#prepare-review")
    def prepare_review(self) -> None:
        try:
            inspection = inspect_config(self.config_path)
            repair_approved = self.query_one("#repair-invalid", Checkbox).value
            if inspection.exists and not inspection.valid and not repair_approved:
                raise SetupConfigError(
                    "Existing configuration is invalid; explicitly approve repair before review."
                )
            materialized = materialize_setup(
                self.state,
                new_config=not inspection.valid,
            )
            plan = prepare_config_update(
                self.config_path,
                wizard_values=materialized.values,
                managed_fields=materialized.managed_fields,
                repair_invalid=repair_approved,
            )
        except (SetupConfigError, ValueError) as exc:
            self._review_plan = None
            self.query_one("#write-reviewed-config", Button).disabled = True
            self.set_step_validation(SetupStep.REVIEW, valid=False, message=str(exc))
            return
        self._review_plan = plan
        target = (
            f"Target: {self.config_path}\n"
            f"Status: {'existing' if plan.source_exists else 'new'}\n"
            f"Backup: {'timestamped backup before replacement' if plan.source_exists else 'not needed'}"
        )
        self.query_one("#review-target", Static).update(target)
        self.query_one("#review-summary", Static).update("\n".join(plan.summary))
        self.query_one("#write-reviewed-config", Button).disabled = False
        self.set_step_validation(
            SetupStep.REVIEW,
            valid=False,
            message="Review prepared. Explicitly write the reviewed configuration before continuing.",
        )

    @on(Button.Pressed, "#write-reviewed-config")
    def write_reviewed_config(self) -> None:
        if self._review_plan is None:
            self.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message="Prepare a fresh configuration review first.",
            )
            return
        try:
            result = commit_config_update(self.config_path, self._review_plan)
        except SetupConfigError as exc:
            self._review_plan = None
            self.query_one("#write-reviewed-config", Button).disabled = True
            self.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message=f"{exc}. Review again before any retry.",
            )
            return
        backup = str(result.backup_path) if result.backup_path is not None else "none"
        self.query_one("#write-result", Static).update(
            f"Configuration written and production-reloaded successfully. Backup: {backup}"
        )
        self.set_step_validation(
            SetupStep.REVIEW,
            valid=True,
            message="Reviewed configuration was written, byte-verified, and production-reloaded.",
        )

    @on(Button.Pressed, "#run-final-diagnostics")
    def run_final_diagnostics(self) -> None:
        report = run_diagnostics(
            self.config_path,
            tunnel_profile=(
                self.state.choices.tunnel_profile if self.state.chatgpt_requested else None
            ),
            tunnel_requested=self.state.chatgpt_requested,
        )
        self.query_one("#diagnostic-matrix", Static).update(format_diagnostics(report))
        attestation = (
            "USER-ATTESTED ChatGPT custom app/refresh: complete"
            if self.state.chatgpt_requested and self.state.choices.chatgpt_user_attested
            else "SKIPPED optional ChatGPT UI attestation"
            if not self.state.chatgpt_requested
            else "WAITING FOR USER ChatGPT custom app/refresh"
        )
        self.query_one("#attestation-summary", Static).update(attestation)
        self.query_one("#smoke-guidance", Static).update(
            "Copyable smoke checks:\n"
            f"dev-workspace-mcp setup --check --config {self.config_path}\n"
            f"dev-workspace-mcp serve --config {self.config_path}"
        )
        self.set_step_validation(
            SetupStep.FINAL_VALIDATION,
            valid=report.ready_to_finish,
            message=(
                "All mandatory machine-verifiable diagnostics pass."
                if report.ready_to_finish
                else "Mandatory diagnostics remain unresolved; remediate and rerun."
            ),
        )

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
