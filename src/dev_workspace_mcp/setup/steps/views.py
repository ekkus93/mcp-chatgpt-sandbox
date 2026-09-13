"""Domain-specific Textual view builders for setup steps."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from textual.widget import Widget
from textual.widgets import Button, Static

from dev_workspace_mcp.config.models import GitPolicyConfig
from dev_workspace_mcp.setup.controller import SetupController, SetupControllerError
from dev_workspace_mcp.setup.model import SetupRepository, SetupStep
from dev_workspace_mcp.setup.tunnel import (
    CHATGPT_APP_URL,
    TUNNEL_DOC_URL,
    build_tunnel_profile_plan,
)

GIT_POLICY_UI_FIELDS = tuple(GitPolicyConfig.model_fields)

STEP_TITLES: dict[SetupStep, str] = {
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


class FieldFactory(Protocol):
    def __call__(
        self,
        label: str,
        widget_id: str,
        value: object,
        *,
        password: bool = False,
    ) -> list[Widget]: ...


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


@dataclass(slots=True)
class StepViewBuilder:
    """Build presentation-only widgets from already-typed setup state."""

    controller: SetupController
    field: FieldFactory
    repositories: list[SetupRepository]
    repository_roots: list[str]
    advanced_visible: bool

    def widgets(self, step: SetupStep) -> Iterable[Widget]:
        renderer = {
            SetupStep.LOCAL_PREREQUISITES: self._local_prerequisites,
            SetupStep.SERVER_STORAGE: self._server_storage,
            SetupStep.DOCKER_WORKSPACE: self._docker_workspace,
            SetupStep.REPOSITORY_ACCESS: self._repository_access,
            SetupStep.SSH_AND_GITHUB: self._ssh_and_github,
            SetupStep.GIT_SAFETY_POLICY: self._git_safety_policy,
            SetupStep.RUNTIME_AND_RESOURCES: self._runtime_and_resources,
            SetupStep.CHATGPT_CONNECTION: self._chatgpt_connection,
            SetupStep.CONFIGURATION_REVIEW: self._configuration_review,
            SetupStep.FINAL_VALIDATION: self._final_validation,
        }[step]
        return renderer()

    def _local_prerequisites(self) -> Iterable[Widget]:
        yield Static(
            "Verify Python and local tooling. Docker unavailability is reported as degraded, "
            "not hidden or treated as a fatal configuration error."
        )
        yield Button("Run prerequisite checks", id="validate_prerequisites", variant="primary")

    def _server_storage(self) -> Iterable[Widget]:
        state_db, audit, keys_dir, known_hosts = self.controller.effective_storage()
        yield Static(
            "Choose absolute host paths. Creating directories is a separate explicit action."
        )
        yield from self.field("State database path", "storage_state_db", state_db)
        yield from self.field("Audit log path", "storage_audit", audit)
        yield from self.field("SSH keys directory", "storage_keys", keys_dir)
        yield from self.field("Pinned known-hosts file", "storage_known_hosts", known_hosts)
        directories = {
            str(self.controller.config_path.parent),
            str(self.controller.state_path.expanduser().parent),
            str(Path(state_db).parent),
            str(Path(audit).parent),
            str(Path(keys_dir)),
            str(Path(known_hosts).parent),
        }
        yield Static(
            "Reviewed directory targets:\n"
            + "\n".join(f"  {path}" for path in sorted(directories)),
            classes="boxed",
        )
        yield Button("Create reviewed parent directories", id="create_storage_dirs")
        yield Button("Validate storage choices", id="validate_storage", variant="primary")

    def _docker_workspace(self) -> Iterable[Widget]:
        image, workspace_path, container, idle = self.controller.effective_docker()
        yield Static(
            "Recommended: host user, sudo enabled, read-write Git metadata, /workspace mount."
        )
        yield from self.field("Default image", "docker_image", image)
        yield from self.field("Workspace path", "docker_workspace_path", workspace_path)
        yield from self.field("Container user [host|root]", "docker_user", container.user)
        yield from self.field("Sudo [true|false]", "docker_sudo", _bool_text(container.sudo))
        yield from self.field(
            "Git directory mode [read-write|read-only|none]",
            "docker_git_dir_mode",
            container.git_dir_mode,
        )
        yield from self.field("Idle stop duration [4h|30m|never]", "docker_idle", idle)
        yield Static(
            "Root mode and reduced Git visibility are advanced choices and may limit normal "
            "development.",
            classes="warning",
        )
        yield Button("Validate Docker workspace", id="validate_docker", variant="primary")
        yield Button("Review default-image build action", id="plan_docker_build")

    def _repository_access(self) -> Iterable[Widget]:
        yield Static(
            "Register repositories explicitly. Verification reads Git metadata only and never "
            "modifies repository contents."
        )
        yield Static(self.repository_list_text(), id="repository_list", classes="boxed")
        yield from self.field("Repository ID", "repo_id", "")
        yield from self.field("Absolute local path", "repo_path", "")
        yield from self.field("GitHub owner/repo", "repo_github", "")
        yield from self.field("SSH key filename (optional)", "repo_ssh_key", "")
        yield Button("Load ID for editing", id="repo_edit")
        yield Button("Add / update repository", id="repo_add")
        yield Button("Remove repository", id="repo_remove", variant="warning")
        yield from self.field(
            "Repository discovery roots (comma-separated absolute paths)",
            "repo_roots",
            ", ".join(self.repository_roots),
        )
        yield Button("Validate repository set", id="validate_repositories", variant="primary")

    def _ssh_and_github(self) -> Iterable[Widget]:
        ssh, github = self.controller.effective_ssh_github()
        yield Static(
            "Recommended: repository-scoped keys through ssh-agent. Shared scope reduces "
            "credential isolation; file mode exposes private-key material to container code.",
            classes="warning",
        )
        yield from self.field("SSH mode [agent|file]", "ssh_mode", ssh.mode)
        yield from self.field("SSH scope [repository|shared]", "ssh_scope", ssh.scope)
        yield from self.field(
            "Shared key filename (shared scope only)",
            "ssh_shared_key",
            ssh.shared_key,
        )
        yield from self.field(
            "GitHub API auth [none|token|app]",
            "github_auth",
            github.api_auth,
        )
        yield from self.field(
            "GitHub token credential file path (token mode only)",
            "github_token_path",
            github.api_token_path,
        )
        yield Static(
            "GitHub App is a production configuration value, but the current runtime does not "
            "implement App token exchange. The wizard never claims otherwise."
        )
        yield Button("Validate SSH / GitHub choices", id="validate_ssh", variant="primary")

    def _git_safety_policy(self) -> Iterable[Widget]:
        preset, policy = self.controller.effective_git_policy()
        yield Static(
            "Recommended denies force pushes and published amend; Conservative also denies "
            "ordinary pushes/default-branch commits. Custom exposes every production field."
        )
        yield from self.field(
            "Preset [recommended|conservative|custom]",
            "git_preset",
            preset.value,
        )
        values = policy.model_dump(mode="json")
        for field_name in GIT_POLICY_UI_FIELDS:
            value = values[field_name]
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            yield from self.field(field_name, f"git_{field_name}", value)
        yield Static(
            "Force push/lease, deletion, and amending published commits can rewrite shared "
            "history. Remote-protection 'deny' fails closed when verification is unavailable.",
            classes="warning",
        )
        yield Button("Validate Git safety policy", id="validate_git", variant="primary")

    def _runtime_and_resources(self) -> Iterable[Widget]:
        if not self.advanced_visible:
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
            yield from self.field(field_name, f"fs_{field_name}", value)
        yield Static("Execution limits (seconds/bytes/lines/days)", classes="section")
        for field_name, value in execution.model_dump(mode="json").items():
            yield from self.field(field_name, f"exec_{field_name}", value)
        yield Static("Docker resource limits; blank means unbounded", classes="section")
        yield from self.field("cpus", "resource_cpus", resources.cpus)
        yield from self.field("memory (for example 16g)", "resource_memory", resources.memory)
        yield from self.field("pids_limit", "resource_pids", resources.pids_limit)
        yield from self.field("Logging level", "logging_level", logging.level)
        yield Button(
            "Validate advanced runtime settings",
            id="validate_runtime",
            variant="primary",
        )

    def _chatgpt_connection(self) -> Iterable[Widget]:
        yield Static(
            "Optional. Secure MCP Tunnel keeps the server private. Runtime API-key input is "
            "masked and process-local only; it is never written to setup state or YAML."
        )
        yield Static(f"Docs: {TUNNEL_DOC_URL}\nChatGPT app page: {CHATGPT_APP_URL}")
        yield from self.field(
            "Enable ChatGPT tunnel setup [true|false]",
            "chatgpt_enabled",
            _bool_text(self.controller.state.chatgpt_enabled),
        )
        yield from self.field(
            "Tunnel ID",
            "chatgpt_tunnel_id",
            self.controller.state.tunnel_id,
        )
        yield from self.field(
            "Runtime API key (never persisted)",
            "chatgpt_runtime_key",
            "",
            password=True,
        )
        yield from self.field(
            "ChatGPT UI completed by operator [true|false]",
            "chatgpt_attested",
            _bool_text(self.controller.state.chatgpt_ui_attested),
        )
        tunnel_id = self.controller.state.tunnel_id
        if tunnel_id:
            tunnel_plan = build_tunnel_profile_plan(self.controller.config_path, tunnel_id)
            yield Static(
                f"Reviewed profile: {tunnel_plan.profile}\n"
                f"Server command: {tunnel_plan.mcp_command}\n"
                f"Init command: {tunnel_plan.display_command}"
            )
        yield Button("Open current tunnel documentation", id="open_tunnel_docs")
        yield Button("Open ChatGPT app settings", id="open_chatgpt_app")
        yield Button("Review / create tunnel profile", id="create_tunnel_profile")
        yield Button("Run tunnel-client doctor", id="doctor_tunnel")
        yield Button("Validate ChatGPT choices", id="validate_chatgpt", variant="primary")
        yield Button("Skip ChatGPT connection", id="skip_chatgpt")

    def _configuration_review(self) -> Iterable[Widget]:
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

    def _final_validation(self) -> Iterable[Widget]:
        yield Static(
            "Finish is enabled only after mandatory machine-verifiable diagnostics pass. "
            "ChatGPT UI completion is never converted into a machine PASS."
        )
        if self.controller.diagnostics is None:
            yield Static("Diagnostics have not been run in this session.")
        else:
            lines = [
                f"{item.status.value.upper():15} {item.code:28} {item.message}"
                for item in self.controller.diagnostics.diagnostics
            ]
            yield Static("\n".join(lines), classes="boxed")
        if self.controller.state.chatgpt_enabled:
            attestation = (
                "USER-ATTESTED" if self.controller.state.chatgpt_ui_attested else "ACTION REQUIRED"
            )
            yield Static(f"{attestation:15} chatgpt.ui                  External ChatGPT UI")
        yield Button("Run / rerun final diagnostics", id="run_diagnostics", variant="primary")
        yield Static(
            "Smoke test after completion:\n"
            f"  dev-workspace-mcp serve --config {self.controller.config_path}"
        )

    def repository_list_text(self) -> str:
        if not self.repositories:
            return "No explicit repositories configured."
        return "\n".join(
            f"{repo.id}: {repo.path} -> {repo.github}"
            + (f" [ssh_key={repo.ssh_key}]" if repo.ssh_key else "")
            for repo in self.repositories
        )


__all__ = ["GIT_POLICY_UI_FIELDS", "STEP_TITLES", "StepViewBuilder"]
