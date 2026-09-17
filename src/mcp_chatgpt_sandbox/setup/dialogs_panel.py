"""Lifecycle choice and confirmation dialogs for the setup wizard."""

from __future__ import annotations

from enum import StrEnum

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


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
            yield Label("Existing mcp-chatgpt-sandbox setup detected")
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
