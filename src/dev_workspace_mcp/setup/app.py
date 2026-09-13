"""Textual navigation shell and existing-installation lifecycle for setup."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static

from dev_workspace_mcp.config import ConfigError, load_config
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    backup_regular_file,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.model import (
    DEFAULT_SETUP_STATE_PATH,
    SetupState,
    SetupStateError,
    SetupStep,
    SetupStepStatus,
    clone_setup_state,
    load_setup_state,
    save_setup_state,
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
        self.return_code: int | None = None
        self.fatal_error: str | None = None
        self._current_index = 0
        self._validation: dict[SetupStep, bool] = {}
        self._validation_messages: dict[SetupStep, str] = {}
        self._loaded_state: SetupState | None = None
        self._state_load_error: str | None = None

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
                    yield Static(id="step-content", markup=False)
                yield Static(id="validation-message", markup=False)
                with Horizontal(id="wizard-controls"):
                    yield Button("Back", id="back")
                    yield Button("Cancel", id="cancel")
                    yield Button("Next", id="next", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        try:
            self._loaded_state = load_setup_state(self.state_path)
        except SetupStateError as exc:
            self._state_load_error = str(exc)

        if self.resume_requested:
            if self._loaded_state is None or not setup_state_is_incomplete(self._loaded_state):
                self._fatal(self._state_load_error or "No incomplete setup state is available.")
                return
            self._activate(self._loaded_state)
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
        fresh = SetupState(advanced=self.advanced)
        if self._save(fresh):
            self._activate(fresh)

    def _fatal(self, message: str) -> None:
        self.fatal_error = message
        self.return_code = 2
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
        self.query_one("#step-content", Static).update(
            "Step-specific controls are loaded by the corresponding setup component."
        )
        valid = self._validation.get(definition.step, False)
        message = self._validation_messages.get(
            definition.step,
            "Complete and validate this step before continuing." if not valid else "",
        )
        self.query_one("#validation-message", Static).update(message)
        self.query_one("#back", Button).disabled = self._current_index == 0
        next_button = self.query_one("#next", Button)
        next_button.disabled = not valid
        next_button.label = "Finish" if self._current_index == len(STEPS) - 1 else "Next"

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
        if not self._validation.get(step, False):
            self._refresh()
            return
        candidate = clone_setup_state(self.state)
        candidate.statuses[step] = SetupStepStatus.COMPLETE
        if self._current_index == len(STEPS) - 1:
            if self._save(candidate):
                self.state = candidate
                self.return_code = 0
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
        self.return_code = 130
        self.exit(130, return_code=130)

    def _handle_start_choice(self, choice: SetupStartChoice | None) -> None:
        if choice in {None, SetupStartChoice.CANCEL}:
            self.return_code = 130
            self.exit(130, return_code=130)
            return
        try:
            if choice is SetupStartChoice.RESUME:
                if self._loaded_state is None or not setup_state_is_incomplete(self._loaded_state):
                    self._fatal("No incomplete setup state is available to resume.")
                    return
                self._activate(self._loaded_state)
                return
            if choice is SetupStartChoice.EDIT:
                load_config(self.config_path)
                state = SetupState(
                    current_step=SetupStep.STORAGE,
                    statuses={SetupStep.PREREQUISITES: SetupStepStatus.COMPLETE},
                    advanced=self.advanced,
                )
                if self._save(state):
                    self._activate(state)
                return
            if choice is SetupStartChoice.REPAIR:
                state = self._loaded_state or SetupState(advanced=self.advanced)
                if self._save(state):
                    self._activate(state)
                return
            if choice is SetupStartChoice.START_FRESH:
                reject_unsafe_sensitive_target(self.state_path)
                if _path_present(self.state_path):
                    backup_regular_file(self.state_path)
                state = SetupState(advanced=self.advanced)
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
    if app.return_code is not None:
        return app.return_code
    return 0 if result is None else result
