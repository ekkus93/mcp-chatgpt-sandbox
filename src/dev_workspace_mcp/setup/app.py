"""Textual setup wizard shell over presentation-independent setup services."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Label, Static

from dev_workspace_mcp.config.models import ExecutionConfig, FilesystemConfig
from dev_workspace_mcp.setup.browser import SetupBrowserError, open_setup_url
from dev_workspace_mcp.setup.controller import (
    SetupController,
    SetupControllerError,
    SetupEntryChoice,
)
from dev_workspace_mcp.setup.docker_setup import DockerBuildPlan
from dev_workspace_mcp.setup.model import (
    GitPolicyPreset,
    SetupRepository,
    SetupStep,
    SetupStepStatus,
)
from dev_workspace_mcp.setup.steps.views import (
    GIT_POLICY_UI_FIELDS,
    STEP_TITLES,
    StepViewBuilder,
)
from dev_workspace_mcp.setup.tunnel import (
    CHATGPT_APP_URL,
    DEFAULT_TUNNEL_PROFILE,
    TUNNEL_DOC_URL,
    TunnelProfilePlan,
    TunnelSetupError,
    build_tunnel_profile_plan,
    create_or_repair_tunnel_profile,
    doctor_tunnel_profile,
)


class ExistingSetupScreen(ModalScreen[SetupEntryChoice | None]):
    """Explicit Resume/Repair/Edit/Start-fresh choice for existing material."""

    DEFAULT_CSS = """
    ExistingSetupScreen { align: center middle; }
    #existing-dialog { width: 72; height: auto; border: heavy $accent; padding: 1 2; }
    #existing-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, controller: SetupController) -> None:
        super().__init__()
        self.controller = controller

    def compose(self) -> ComposeResult:
        details = ["Existing setup material was detected."]
        if self.controller.state_load_error:
            details.append(f"Setup state is unreadable: {self.controller.state_load_error}")
        if self.controller.inspection.exists:
            status = (
                "valid"
                if self.controller.inspection.valid
                else "invalid; explicit repair approval required"
            )
            details.append(f"Production config: {status}")
        with Vertical(id="existing-dialog"):
            yield Static("\n".join(details))
            with Horizontal(id="existing-actions"):
                yield Button(
                    "Resume",
                    id="entry_resume",
                    disabled=not self.controller.resume_available,
                )
                yield Button("Repair", id="entry_repair")
                yield Button(
                    "Edit configuration",
                    id="entry_edit",
                    disabled=not self.controller.inspection.valid,
                )
                yield Button("Start fresh", id="entry_start_fresh")
                yield Button("Cancel", id="entry_cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping: dict[str, SetupEntryChoice] = {
            "entry_resume": "resume",
            "entry_repair": "repair",
            "entry_edit": "edit",
            "entry_start_fresh": "start_fresh",
        }
        if event.button.id == "entry_cancel":
            self.dismiss(None)
        elif event.button.id in mapping:
            self.dismiss(mapping[event.button.id])


class ConfirmScreen(ModalScreen[bool]):
    """Reusable explicit confirmation for consequential actions."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-dialog { width: 68; height: auto; border: heavy $warning; padding: 1 2; }
    """

    def __init__(self, message: str, *, confirm_label: str) -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.message)
            with Horizontal():
                yield Button("Back", id="confirm_no")
                yield Button(
                    self.confirm_label,
                    id="confirm_yes",
                    variant="warning",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm_yes":
            self.dismiss(True)
        elif event.button.id == "confirm_no":
            self.dismiss(False)


def _status_text(status: SetupStepStatus | None) -> str:
    return (status or SetupStepStatus.NOT_STARTED).value.replace("_", " ").upper()


def _parse_bool(value: str, *, label: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise SetupControllerError(f"{label} must be true or false")


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _optional_int(value: str) -> int | None:
    return None if not value.strip() else int(value.strip())


def _optional_float(value: str) -> float | None:
    return None if not value.strip() else float(value.strip())


class SetupApp(App[int]):
    """Navigation/event shell; domain validation and persistence live outside Textual."""

    CSS = """
    Screen { min-width: 40; }
    #setup-shell { height: 1fr; }
    #progress {
        width: 30;
        min-width: 22;
        border-right: solid $accent;
        padding: 1;
        overflow-y: auto;
    }
    #main { width: 1fr; padding: 0 1; }
    #title { height: auto; text-style: bold; padding: 1 0; }
    #content { height: 1fr; padding: 0 1; }
    #validation { height: auto; min-height: 3; border-top: solid $panel; padding: 1; }
    #controls { height: 3; align-horizontal: right; }
    Input { margin-bottom: 1; }
    .warning { text-style: bold; }
    .section { margin-top: 1; text-style: bold; }
    .boxed { min-height: 3; border: round $panel; padding: 1; margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_setup", "Cancel", priority=True),
        Binding("escape", "cancel_setup", "Cancel"),
        Binding("alt+left", "back_step", "Back"),
        Binding("alt+right", "next_step", "Next"),
    ]

    def __init__(self, controller: SetupController) -> None:
        super().__init__()
        self.controller = controller
        repositories, roots = controller.effective_repositories()
        self._repositories = list(repositories)
        self._repository_roots = list(roots)
        self._advanced_visible = controller.state.advanced
        self._pending_remove_id: str | None = None
        self._pending_build: DockerBuildPlan | None = None
        self._pending_tunnel_plan: TunnelProfilePlan | None = None
        self._suspend_dirty = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="setup-shell"):
            yield Static(id="progress")
            with Vertical(id="main"):
                yield Static(id="title")
                yield VerticalScroll(id="content")
                yield Static("", id="validation")
                with Horizontal(id="controls"):
                    yield Button("Back", id="back")
                    yield Button("Cancel", id="cancel", variant="warning")
                    yield Button("Next", id="next", variant="primary")
                    yield Button("Finish", id="finish", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._render_step()
        if self.controller.has_existing_material:
            self.push_screen(ExistingSetupScreen(self.controller), self._existing_choice)

    def _existing_choice(self, choice: SetupEntryChoice | None) -> None:
        if choice is None:
            self.exit(130)
            return
        try:
            self.controller.choose_entry(choice)
        except SetupControllerError as exc:
            self._show_error(str(exc))
            return
        repositories, roots = self.controller.effective_repositories()
        self._repositories = list(repositories)
        self._repository_roots = list(roots)
        self._advanced_visible = self.controller.state.advanced
        self._show_message(f"Selected action: {choice.replace('_', ' ')}")
        self._render_step()

    def _field(
        self,
        label: str,
        widget_id: str,
        value: object,
        *,
        password: bool = False,
    ) -> list[Widget]:
        return [
            Label(label),
            Input(
                value="" if value is None else str(value),
                id=widget_id,
                password=password,
            ),
        ]

    def _input(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value

    def _set_input(self, widget_id: str, value: object) -> None:
        self.query_one(f"#{widget_id}", Input).value = "" if value is None else str(value)

    def _show_message(self, message: str) -> None:
        self.query_one("#validation", Static).update(f"STATUS: {message}")

    def _show_error(self, message: str) -> None:
        self.query_one("#validation", Static).update(f"ERROR: {message}")

    def _progress_text(self) -> str:
        active = self.controller.state.current_step
        lines: list[str] = []
        for index, step in enumerate(SetupStep, start=1):
            marker = ">" if step is active else " "
            lines.append(f"{marker} [{index}/{len(SetupStep)}] {STEP_TITLES[step]}")
            lines.append(f"    {_status_text(self.controller.state.statuses.get(step))}")
            lines.append("")
        return "\n".join(lines)

    def _render_step(self) -> None:
        self.query_one("#progress", Static).update(self._progress_text())
        step = self.controller.state.current_step
        self.query_one("#title", Static).update(STEP_TITLES[step])
        content = self.query_one("#content", VerticalScroll)
        content.remove_children()
        view = StepViewBuilder(
            controller=self.controller,
            field=self._field,
            repositories=self._repositories,
            repository_roots=self._repository_roots,
            advanced_visible=self._advanced_visible,
        )
        widgets = list(view.widgets(step))
        if widgets:
            content.mount(*widgets)
        self._update_controls()

    def _update_controls(self) -> None:
        step = self.controller.state.current_step
        back = self.query_one("#back", Button)
        next_button = self.query_one("#next", Button)
        finish = self.query_one("#finish", Button)
        back.disabled = step is SetupStep.LOCAL_PREREQUISITES
        is_final = step is SetupStep.FINAL_VALIDATION
        next_button.display = not is_final
        next_button.disabled = not self.controller.can_advance()
        finish.display = is_final
        finish.disabled = (
            self.controller.state.statuses.get(SetupStep.FINAL_VALIDATION)
            is not SetupStepStatus.COMPLETE
        )

    def _mark_dirty(self) -> None:
        if self._suspend_dirty:
            return
        step = self.controller.state.current_step
        if self.controller.state.statuses.get(step) is SetupStepStatus.COMPLETE:
            self.controller.state.mark_changed(step)
            self.controller.reviewed_plan = None
            self.controller.diagnostics = None
            try:
                self.controller.persist()
            except SetupControllerError as exc:
                self._show_error(str(exc))
            self._render_status_only()

    def on_input_changed(self, event: Input.Changed) -> None:
        del event
        self._mark_dirty()

    def _custom_git_policy(self) -> dict[str, object]:
        values: dict[str, object] = {}
        list_fields = {"remote_protection_checks", "allowed_url_schemes"}
        for field_name in GIT_POLICY_UI_FIELDS:
            raw = self._input(f"git_{field_name}")
            values[field_name] = _comma_list(raw) if field_name in list_fields else raw.strip()
        return values

    def _advanced_values(
        self,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
        filesystem_values: dict[str, object] = {
            name: int(self._input(f"fs_{name}")) for name in FilesystemConfig.model_fields
        }
        execution_values: dict[str, object] = {
            name: int(self._input(f"exec_{name}")) for name in ExecutionConfig.model_fields
        }
        resources_values: dict[str, object] = {
            "cpus": _optional_float(self._input("resource_cpus")),
            "memory": self._input("resource_memory").strip() or None,
            "pids_limit": _optional_int(self._input("resource_pids")),
        }
        logging_values: dict[str, object] = {"level": self._input("logging_level").strip()}
        return filesystem_values, execution_values, resources_values, logging_values

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        try:
            self._dispatch_button(button_id)
        except (SetupControllerError, TunnelSetupError, SetupBrowserError, ValueError) as exc:
            self._show_error(str(exc))
            self._update_controls()

    def _dispatch_button(self, button_id: str | None) -> None:
        if button_id == "back":
            self.controller.back()
            self._render_step()
        elif button_id == "next":
            self.controller.advance()
            self._render_step()
        elif button_id == "cancel":
            self.action_cancel_setup()
        elif button_id == "finish":
            self.exit(0)
        elif button_id == "validate_prerequisites":
            messages = self.controller.mark_local_prerequisites()
            self._show_message("; ".join(messages))
            self._render_status_only()
        elif button_id == "create_storage_dirs":
            created = self.controller.create_storage_directories()
            self._show_message(
                "Reviewed directories ready: " + ", ".join(str(path) for path in created)
            )
        elif button_id == "validate_storage":
            self.controller.apply_storage(
                state_db_path=self._input("storage_state_db"),
                audit_path=self._input("storage_audit"),
                ssh_keys_dir=self._input("storage_keys"),
                known_hosts_path=self._input("storage_known_hosts"),
            )
            self._show_message("Storage choices validated")
            self._render_status_only()
        elif button_id == "validate_docker":
            docker_result = self.controller.apply_docker(
                image=self._input("docker_image"),
                workspace_path=self._input("docker_workspace_path"),
                container_user=self._input("docker_user"),
                sudo=_parse_bool(self._input("docker_sudo"), label="sudo"),
                git_dir_mode=self._input("docker_git_dir_mode"),
                idle_stop_after=self._input("docker_idle"),
            )
            self._show_message(docker_result.message)
            self._render_status_only()
        elif button_id == "plan_docker_build":
            self._review_docker_build()
        elif button_id == "repo_edit":
            self._load_repository_for_edit()
        elif button_id == "repo_add":
            self._add_or_update_repository()
        elif button_id == "repo_remove":
            self._review_repository_removal()
        elif button_id == "validate_repositories":
            self._repository_roots = _comma_list(self._input("repo_roots"))
            self.controller.apply_repositories(
                tuple(self._repositories),
                tuple(self._repository_roots),
            )
            self._show_message("Repository set verified read-only")
            self._render_status_only()
        elif button_id == "validate_ssh":
            self.controller.apply_ssh_github(
                ssh_mode=self._input("ssh_mode"),
                ssh_scope=self._input("ssh_scope"),
                shared_key=self._input("ssh_shared_key").strip() or None,
                github_api_auth=self._input("github_auth"),
                github_api_token_path=self._input("github_token_path").strip() or None,
            )
            self._show_message(
                "SSH/GitHub choices validated without reading or displaying secret contents"
            )
            self._render_status_only()
        elif button_id == "validate_git":
            preset = GitPolicyPreset(self._input("git_preset").strip())
            custom = self._custom_git_policy() if preset is GitPolicyPreset.CUSTOM else None
            self.controller.apply_git_policy(preset, custom)
            self._show_message("Git safety policy validated against production model")
            self._render_status_only()
        elif button_id == "runtime_defaults":
            self.controller.skip_advanced()
            self._show_message("Production runtime/resource defaults preserved")
            self._render_status_only()
        elif button_id == "show_advanced":
            self._advanced_visible = True
            self._render_step()
        elif button_id == "validate_runtime":
            filesystem, execution, resources, logging = self._advanced_values()
            self.controller.apply_advanced(
                filesystem=filesystem,
                execution=execution,
                resources=resources,
                logging=logging,
            )
            self._show_message("Advanced runtime settings validated")
            self._render_status_only()
        elif button_id == "open_tunnel_docs":
            self._open_url(TUNNEL_DOC_URL, "OpenAI tunnel documentation")
        elif button_id == "open_chatgpt_app":
            self._open_url(CHATGPT_APP_URL, "ChatGPT app settings")
        elif button_id == "create_tunnel_profile":
            self._review_tunnel_profile()
        elif button_id == "doctor_tunnel":
            self._doctor_tunnel()
        elif button_id == "validate_chatgpt":
            self.controller.apply_chatgpt(
                enabled=_parse_bool(
                    self._input("chatgpt_enabled"),
                    label="ChatGPT enabled",
                ),
                tunnel_id=self._input("chatgpt_tunnel_id").strip() or None,
                runtime_key=self._input("chatgpt_runtime_key")
                or self.controller.runtime_tunnel_key,
                user_attested=_parse_bool(
                    self._input("chatgpt_attested"),
                    label="ChatGPT UI attestation",
                ),
            )
            self._show_message(
                "ChatGPT choices saved; UI completion is USER-ATTESTED, not machine-verified"
            )
            self._render_status_only()
        elif button_id == "skip_chatgpt":
            self.controller.apply_chatgpt(enabled=False)
            self._show_message("Optional ChatGPT connection skipped")
            self._render_status_only()
        elif button_id == "write_configuration":
            if self.controller.reviewed_plan is None:
                self.controller.prepare_review()
            commit_result = self.controller.write_reviewed_configuration()
            backup = f"; backup: {commit_result.backup_path}" if commit_result.backup_path else ""
            self._show_message(
                f"Configuration written and production-reloaded: {commit_result.path}{backup}"
            )
            self._render_status_only()
        elif button_id == "run_diagnostics":
            report = self.controller.run_final_diagnostics()
            self._show_message(
                "All mandatory diagnostics passed"
                if report.valid
                else f"{len(report.unresolved)} mandatory diagnostic(s) unresolved"
            )
            self._render_step()

    def _review_docker_build(self) -> None:
        docker_plan = self.controller.docker_build_plan(self._input("docker_image"))
        self._pending_build = docker_plan
        message = (
            "Run this exact Docker build action?\n\n"
            f"{docker_plan.display_command}\n\nNo shell interpolation is used."
        )
        self.push_screen(
            ConfirmScreen(message, confirm_label="Build image"),
            self._docker_build_choice,
        )

    def _load_repository_for_edit(self) -> None:
        repository_id = self._input("repo_id").strip()
        repository = next((repo for repo in self._repositories if repo.id == repository_id), None)
        if repository is None:
            raise SetupControllerError(f"Repository is not in the setup list: {repository_id}")
        self._suspend_dirty = True
        try:
            self._set_input("repo_path", repository.path)
            self._set_input("repo_github", repository.github)
            self._set_input("repo_ssh_key", repository.ssh_key)
        finally:
            self._suspend_dirty = False
        self._show_message(f"Loaded repository {repository_id!r} for editing; no state changed")

    def _add_or_update_repository(self) -> None:
        repository = SetupRepository(
            id=self._input("repo_id"),
            path=self._input("repo_path"),
            github=self._input("repo_github"),
            ssh_key=self._input("repo_ssh_key").strip() or None,
        )
        self._repositories = [repo for repo in self._repositories if repo.id != repository.id]
        self._repositories.append(repository)
        self._repositories.sort(key=lambda repo: repo.id)
        self.controller.state.statuses[SetupStep.REPOSITORY_ACCESS] = (
            SetupStepStatus.ACTION_REQUIRED
        )
        self._render_step()

    def _review_repository_removal(self) -> None:
        repository_id = self._input("repo_id").strip()
        if not repository_id:
            raise SetupControllerError("Enter the repository ID to remove")
        if not any(repo.id == repository_id for repo in self._repositories):
            raise SetupControllerError(f"Repository is not in the setup list: {repository_id}")
        self._pending_remove_id = repository_id
        self.push_screen(
            ConfirmScreen(
                "Remove this repository from the generated configuration only? "
                "The local Git repository and its files will not be deleted.\n\n"
                f"Repository ID: {repository_id}",
                confirm_label="Remove from config",
            ),
            self._repository_remove_choice,
        )

    def _review_tunnel_profile(self) -> None:
        tunnel_plan = build_tunnel_profile_plan(
            self.controller.config_path,
            self._input("chatgpt_tunnel_id"),
        )
        self._pending_tunnel_plan = tunnel_plan
        self.push_screen(
            ConfirmScreen(
                "Create or repair this exact tunnel-client profile?\n\n"
                f"Profile: {tunnel_plan.profile}\n"
                f"Server command: {tunnel_plan.mcp_command}\n"
                f"Init command: {tunnel_plan.display_command}\n\n"
                "The runtime API key remains process-local and is not part of this plan.",
                confirm_label="Write tunnel profile",
            ),
            self._tunnel_profile_choice,
        )

    def _doctor_tunnel(self) -> None:
        runtime_key = self._input("chatgpt_runtime_key") or self.controller.runtime_tunnel_key
        ok = doctor_tunnel_profile(DEFAULT_TUNNEL_PROFILE, runtime_key=runtime_key)
        if not ok:
            raise SetupControllerError("tunnel-client doctor did not report a healthy profile")
        self.controller.runtime_tunnel_key = runtime_key
        self._show_message("tunnel-client doctor passed")

    def _open_url(self, url: str, label: str) -> None:
        opened = open_setup_url(url, no_browser=self.controller.no_browser)
        self._show_message(
            f"Opened {label}" if opened else f"Browser disabled; open manually: {url}"
        )

    def _docker_build_choice(self, confirmed: bool | None) -> None:
        docker_plan = self._pending_build
        self._pending_build = None
        if not confirmed or docker_plan is None:
            self._show_message("Docker build cancelled; no image mutation performed")
            return
        try:
            docker_result = self.controller.execute_docker_build(docker_plan)
        except SetupControllerError as exc:
            self._show_error(str(exc))
            return
        self._show_message(docker_result.message)
        self._render_status_only()

    def _repository_remove_choice(self, confirmed: bool | None) -> None:
        repository_id = self._pending_remove_id
        self._pending_remove_id = None
        if not confirmed or repository_id is None:
            self._show_message("Repository removal cancelled")
            return
        self._repositories = [
            repository for repository in self._repositories if repository.id != repository_id
        ]
        self.controller.state.statuses[SetupStep.REPOSITORY_ACCESS] = (
            SetupStepStatus.ACTION_REQUIRED
        )
        self._show_message(
            f"Repository {repository_id!r} removed from pending config; local files untouched"
        )
        self._render_step()

    def _tunnel_profile_choice(self, confirmed: bool | None) -> None:
        tunnel_plan = self._pending_tunnel_plan
        self._pending_tunnel_plan = None
        if not confirmed or tunnel_plan is None:
            self._show_message("Tunnel profile write cancelled")
            return
        runtime_key = self._input("chatgpt_runtime_key") or self.controller.runtime_tunnel_key
        try:
            create_or_repair_tunnel_profile(tunnel_plan, runtime_key=runtime_key)
        except TunnelSetupError as exc:
            self._show_error(str(exc))
            return
        self.controller.runtime_tunnel_key = runtime_key
        self._show_message(
            f"Tunnel profile {tunnel_plan.profile!r} created/repaired after reviewed approval"
        )

    def _render_status_only(self) -> None:
        self.query_one("#progress", Static).update(self._progress_text())
        self._update_controls()

    def action_cancel_setup(self) -> None:
        self.push_screen(
            ConfirmScreen(
                "Cancel setup? Existing production configuration, repositories, Docker resources, "
                "keys, and operational state will not be deleted.",
                confirm_label="Cancel setup",
            ),
            self._cancel_choice,
        )

    def _cancel_choice(self, confirmed: bool | None) -> None:
        if confirmed:
            try:
                self.controller.persist()
            except SetupControllerError as exc:
                self._show_error(str(exc))
                return
            self.exit(130)
        else:
            self._show_message("Cancellation declined; setup session preserved")

    def action_back_step(self) -> None:
        if self.controller.state.current_step is not SetupStep.LOCAL_PREREQUISITES:
            self.controller.back()
            self._render_step()

    def action_next_step(self) -> None:
        if (
            self.controller.can_advance()
            and self.controller.state.current_step is not SetupStep.FINAL_VALIDATION
        ):
            self.controller.advance()
            self._render_step()


def run_setup_app(controller: SetupController) -> int:
    result = SetupApp(controller).run()
    return 0 if result is None else result


__all__ = [
    "ConfirmScreen",
    "ExistingSetupScreen",
    "GIT_POLICY_UI_FIELDS",
    "SetupApp",
    "run_setup_app",
]
