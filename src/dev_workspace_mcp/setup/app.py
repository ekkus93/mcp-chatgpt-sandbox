"""Textual setup wizard presentation over the production setup controller."""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Label, Static

from dev_workspace_mcp.config.models import (
    ExecutionConfig,
    FilesystemConfig,
    GitPolicyConfig,
)
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
from dev_workspace_mcp.setup.tunnel import (
    CHATGPT_APP_URL,
    DEFAULT_TUNNEL_PROFILE,
    TUNNEL_DOC_URL,
    TunnelSetupError,
    build_tunnel_profile_plan,
    create_or_repair_tunnel_profile,
    doctor_tunnel_profile,
)

GIT_POLICY_UI_FIELDS = tuple(GitPolicyConfig.model_fields)

_STEP_TITLES: dict[SetupStep, str] = {
    SetupStep.LOCAL_PREREQUISITES: "Local prerequisites",
    SetupStep.SERVER_STORAGE: "Server storage",
    SetupStep.DOCKER_WORKSPACE: "Docker workspace",
    SetupStep.REPOSITORY_ACCESS: "Repository access",
    SetupStep.SSH_AND_GITHUB: "SSH and GitHub",
    SetupStep.GIT_SAFETY_POLICY: "Git safety policy",
    SetupStep.RUNTIME_AND_RESOURCES: "Runtime and resources",
    SetupStep.CHATGPT_CONNECTION: "ChatGPT connection (optional)",
    SetupStep.CONFIGURATION_REVIEW: "Configuration review",
    SetupStep.FINAL_VALIDATION: "Final validation",
}


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
    """Reusable explicit confirmation for destructive/external actions."""

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


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


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
    """Full-screen keyboard-operable setup wizard."""

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

    def _show_message(self, message: str) -> None:
        self.query_one("#validation", Static).update(f"STATUS: {message}")

    def _show_error(self, message: str) -> None:
        self.query_one("#validation", Static).update(f"ERROR: {message}")

    def _progress_text(self) -> str:
        active = self.controller.state.current_step
        lines: list[str] = []
        for index, step in enumerate(SetupStep, start=1):
            marker = ">" if step is active else " "
            lines.append(f"{marker} [{index}/{len(SetupStep)}] {_STEP_TITLES[step]}")
            lines.append(f"    {_status_text(self.controller.state.statuses.get(step))}")
            lines.append("")
        return "\n".join(lines)

    def _render_step(self) -> None:
        self.query_one("#progress", Static).update(self._progress_text())
        step = self.controller.state.current_step
        self.query_one("#title", Static).update(_STEP_TITLES[step])
        content = self.query_one("#content", VerticalScroll)
        content.remove_children()
        widgets = list(self._widgets_for_step(step))
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

    def _widgets_for_step(self, step: SetupStep) -> Iterable[Widget]:
        if step is SetupStep.LOCAL_PREREQUISITES:
            yield Static(
                "Verify Python and local tooling. Docker unavailability is reported as degraded, "
                "not hidden or treated as a fatal configuration error."
            )
            yield Button("Run prerequisite checks", id="validate_prerequisites", variant="primary")
            return

        if step is SetupStep.SERVER_STORAGE:
            state_db, audit, keys_dir, known_hosts = self.controller.effective_storage()
            yield Static(
                "Choose absolute host paths. Creating directories is a separate explicit action."
            )
            yield from self._field("State database path", "storage_state_db", state_db)
            yield from self._field("Audit log path", "storage_audit", audit)
            yield from self._field("SSH keys directory", "storage_keys", keys_dir)
            yield from self._field("Pinned known-hosts file", "storage_known_hosts", known_hosts)
            yield Button("Create reviewed parent directories", id="create_storage_dirs")
            yield Button("Validate storage choices", id="validate_storage", variant="primary")
            return

        if step is SetupStep.DOCKER_WORKSPACE:
            image, workspace_path, container, idle = self.controller.effective_docker()
            yield Static(
                "Recommended: host user, sudo enabled, read-write Git metadata, /workspace mount."
            )
            yield from self._field("Default image", "docker_image", image)
            yield from self._field("Workspace path", "docker_workspace_path", workspace_path)
            yield from self._field("Container user [host|root]", "docker_user", container.user)
            yield from self._field("Sudo [true|false]", "docker_sudo", _bool_text(container.sudo))
            yield from self._field(
                "Git directory mode [read-write|read-only|none]",
                "docker_git_dir_mode",
                container.git_dir_mode,
            )
            yield from self._field("Idle stop duration [4h|30m|never]", "docker_idle", idle)
            yield Static(
                "Root mode and reduced Git visibility are advanced choices and may limit normal "
                "development.",
                classes="warning",
            )
            yield Button("Validate Docker workspace", id="validate_docker", variant="primary")
            yield Button("Review default-image build action", id="plan_docker_build")
            return

        if step is SetupStep.REPOSITORY_ACCESS:
            yield Static(
                "Register repositories explicitly. Verification reads Git metadata only and never "
                "modifies repository contents."
            )
            yield Static(self._repository_list_text(), id="repository_list", classes="boxed")
            yield from self._field("Repository ID", "repo_id", "")
            yield from self._field("Absolute local path", "repo_path", "")
            yield from self._field("GitHub owner/repo", "repo_github", "")
            yield from self._field("SSH key filename (optional)", "repo_ssh_key", "")
            yield Button("Add / replace by ID", id="repo_add")
            yield Button("Remove by ID", id="repo_remove", variant="warning")
            yield from self._field(
                "Repository discovery roots (comma-separated absolute paths)",
                "repo_roots",
                ", ".join(self._repository_roots),
            )
            yield Button("Validate repository set", id="validate_repositories", variant="primary")
            return

        if step is SetupStep.SSH_AND_GITHUB:
            ssh, github = self.controller.effective_ssh_github()
            yield Static(
                "Recommended: repository-scoped keys through ssh-agent. Shared scope reduces "
                "credential isolation; file mode exposes private-key material to container code.",
                classes="warning",
            )
            yield from self._field("SSH mode [agent|file]", "ssh_mode", ssh.mode)
            yield from self._field("SSH scope [repository|shared]", "ssh_scope", ssh.scope)
            yield from self._field(
                "Shared key filename (shared scope only)",
                "ssh_shared_key",
                ssh.shared_key,
            )
            yield from self._field(
                "GitHub API auth [none|token|app]",
                "github_auth",
                github.api_auth,
            )
            yield from self._field(
                "GitHub token credential file path (token mode only)",
                "github_token_path",
                github.api_token_path,
            )
            yield Static(
                "GitHub App is a production configuration value, but the current runtime does not "
                "implement App token exchange. The wizard never claims otherwise."
            )
            yield Button("Validate SSH / GitHub choices", id="validate_ssh", variant="primary")
            return

        if step is SetupStep.GIT_SAFETY_POLICY:
            preset, policy = self.controller.effective_git_policy()
            yield Static(
                "Recommended denies force pushes and published amend; Conservative also denies "
                "ordinary pushes/default-branch commits. Custom exposes every production field."
            )
            yield from self._field(
                "Preset [recommended|conservative|custom]",
                "git_preset",
                preset.value,
            )
            values = policy.model_dump(mode="json")
            for field_name in GIT_POLICY_UI_FIELDS:
                value = values[field_name]
                if isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                yield from self._field(field_name, f"git_{field_name}", value)
            yield Static(
                "Force push/lease, deletion, and amending published commits can rewrite shared "
                "history. Remote-protection 'deny' fails closed when verification is unavailable.",
                classes="warning",
            )
            yield Button("Validate Git safety policy", id="validate_git", variant="primary")
            return

        if step is SetupStep.RUNTIME_AND_RESOURCES:
            if not self._advanced_visible:
                yield Static(
                    "Production filesystem, execution, resource, and logging defaults remain "
                    "authoritative unless you explicitly open Advanced."
                )
                yield Button("Use production defaults", id="runtime_defaults", variant="primary")
                yield Button("Show advanced controls", id="show_advanced")
                return
            filesystem, execution, resources, logging = self.controller.effective_advanced()
            yield Static("Filesystem limits (bytes/results)", classes="section")
            for field_name, value in filesystem.model_dump(mode="json").items():
                yield from self._field(field_name, f"fs_{field_name}", value)
            yield Static("Execution limits (seconds/bytes/lines/days)", classes="section")
            for field_name, value in execution.model_dump(mode="json").items():
                yield from self._field(field_name, f"exec_{field_name}", value)
            yield Static("Docker resource limits; blank means unbounded", classes="section")
            yield from self._field("cpus", "resource_cpus", resources.cpus)
            yield from self._field("memory (for example 16g)", "resource_memory", resources.memory)
            yield from self._field("pids_limit", "resource_pids", resources.pids_limit)
            yield from self._field("Logging level", "logging_level", logging.level)
            yield Button(
                "Validate advanced runtime settings",
                id="validate_runtime",
                variant="primary",
            )
            return

        if step is SetupStep.CHATGPT_CONNECTION:
            yield Static(
                "Optional. Secure MCP Tunnel keeps the server private. Runtime API-key input is "
                "masked and process-local only; it is never written to setup state or YAML."
            )
            yield Static(f"Docs: {TUNNEL_DOC_URL}\nChatGPT app page: {CHATGPT_APP_URL}")
            yield from self._field(
                "Enable ChatGPT tunnel setup [true|false]",
                "chatgpt_enabled",
                _bool_text(self.controller.state.chatgpt_enabled),
            )
            yield from self._field(
                "Tunnel ID",
                "chatgpt_tunnel_id",
                self.controller.state.tunnel_id,
            )
            yield from self._field(
                "Runtime API key (never persisted)",
                "chatgpt_runtime_key",
                "",
                password=True,
            )
            yield from self._field(
                "ChatGPT UI completed by operator [true|false]",
                "chatgpt_attested",
                _bool_text(self.controller.state.chatgpt_ui_attested),
            )
            if self.controller.state.tunnel_id:
                tunnel_plan = build_tunnel_profile_plan(
                    self.controller.config_path,
                    self.controller.state.tunnel_id,
                )
                yield Static(
                    f"Reviewed profile: {tunnel_plan.profile}\n"
                    f"Server command: {tunnel_plan.mcp_command}\n"
                    f"Init command: {tunnel_plan.display_command}"
                )
            yield Button("Open current tunnel documentation", id="open_tunnel_docs")
            yield Button("Open ChatGPT app settings", id="open_chatgpt_app")
            yield Button("Create / repair reviewed tunnel profile", id="create_tunnel_profile")
            yield Button("Run tunnel-client doctor", id="doctor_tunnel")
            yield Button("Validate ChatGPT choices", id="validate_chatgpt", variant="primary")
            yield Button("Skip ChatGPT connection", id="skip_chatgpt")
            return

        if step is SetupStep.CONFIGURATION_REVIEW:
            yield Static(f"Target configuration: {self.controller.config_path}")
            try:
                config_plan = self.controller.prepare_review()
            except SetupControllerError as exc:
                yield Static(f"Cannot prepare review: {exc}")
                return
            status = (
                "yes; timestamped backup will be created"
                if config_plan.source_exists
                else "no; new file"
            )
            yield Static(f"Existing file: {status}")
            yield Static("\n".join(config_plan.summary), classes="boxed")
            yield Button(
                "Write reviewed configuration",
                id="write_configuration",
                variant="primary",
            )
            return

        if step is SetupStep.FINAL_VALIDATION:
            yield Static(
                "Finish is enabled only after mandatory machine-verifiable diagnostics pass. "
                "ChatGPT UI completion remains USER-ATTESTED, never machine PASS."
            )
            if self.controller.diagnostics is None:
                yield Static("Diagnostics have not been run in this session.")
            else:
                lines = [
                    f"{item.status.value.upper():15} {item.code:28} {item.message}"
                    for item in self.controller.diagnostics.diagnostics
                ]
                yield Static("\n".join(lines), classes="boxed")
            yield Button("Run / rerun final diagnostics", id="run_diagnostics", variant="primary")
            yield Static(
                "Smoke test after completion:\n"
                f"  dev-workspace-mcp serve --config {self.controller.config_path}"
            )

    def _repository_list_text(self) -> str:
        if not self._repositories:
            return "No explicit repositories configured."
        return "\n".join(
            f"{repo.id}: {repo.path} -> {repo.github}"
            + (f" [ssh_key={repo.ssh_key}]" if repo.ssh_key else "")
            for repo in self._repositories
        )

    def _mark_dirty(self) -> None:
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
            elif button_id == "repo_add":
                repository = SetupRepository(
                    id=self._input("repo_id"),
                    path=self._input("repo_path"),
                    github=self._input("repo_github"),
                    ssh_key=self._input("repo_ssh_key").strip() or None,
                )
                self._repositories = [
                    repo for repo in self._repositories if repo.id != repository.id
                ]
                self._repositories.append(repository)
                self._repositories.sort(key=lambda repo: repo.id)
                self.controller.state.statuses[SetupStep.REPOSITORY_ACCESS] = (
                    SetupStepStatus.ACTION_REQUIRED
                )
                self._render_step()
            elif button_id == "repo_remove":
                repository_id = self._input("repo_id").strip()
                if not repository_id:
                    raise SetupControllerError("Enter the repository ID to remove")
                if not any(repo.id == repository_id for repo in self._repositories):
                    raise SetupControllerError(
                        f"Repository is not in the setup list: {repository_id}"
                    )
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
                tunnel_plan = build_tunnel_profile_plan(
                    self.controller.config_path,
                    self._input("chatgpt_tunnel_id"),
                )
                runtime_key = (
                    self._input("chatgpt_runtime_key") or self.controller.runtime_tunnel_key
                )
                create_or_repair_tunnel_profile(tunnel_plan, runtime_key=runtime_key)
                self.controller.runtime_tunnel_key = runtime_key
                self._show_message(
                    f"Tunnel profile {tunnel_plan.profile!r} created/repaired after explicit approval"
                )
            elif button_id == "doctor_tunnel":
                runtime_key = (
                    self._input("chatgpt_runtime_key") or self.controller.runtime_tunnel_key
                )
                ok = doctor_tunnel_profile(
                    DEFAULT_TUNNEL_PROFILE,
                    runtime_key=runtime_key,
                )
                if not ok:
                    raise SetupControllerError(
                        "tunnel-client doctor did not report a healthy profile"
                    )
                self.controller.runtime_tunnel_key = runtime_key
                self._show_message("tunnel-client doctor passed")
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
                backup = (
                    f"; backup: {commit_result.backup_path}" if commit_result.backup_path else ""
                )
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
        except (SetupControllerError, TunnelSetupError, SetupBrowserError, ValueError) as exc:
            self._show_error(str(exc))
            self._update_controls()

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
