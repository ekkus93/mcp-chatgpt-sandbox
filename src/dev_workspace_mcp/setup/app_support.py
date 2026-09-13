"""Small Textual support types for the setup wizard app."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dev_workspace_mcp.setup.model import SetupState, SetupStateError, SetupStep, SetupStepStatus


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


SETUP_WIZARD_CSS = """
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


def path_present(path: Path) -> bool:
    try:
        path.expanduser().lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SetupStateError(f"Could not inspect setup path {path.expanduser()}") from exc
    return True


def blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None
