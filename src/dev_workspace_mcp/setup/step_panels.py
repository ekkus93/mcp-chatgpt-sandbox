"""Concrete Textual controls for each logical setup-wizard step."""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Static

from dev_workspace_mcp.setup.git_policy_setup import recommended_policy
from dev_workspace_mcp.setup.runtime_setup import production_runtime_defaults


class WizardStepPanels(Vertical):
    """All logical step panels; the app shows exactly one at a time."""

    def __init__(self, *, advanced: bool) -> None:
        super().__init__(id="step-content")
        self.advanced = advanced

    def compose(self) -> ComposeResult:
        with Vertical(id="panel-local_prerequisites", classes="step-panel"):
            yield Static(
                "Checks Python and Docker. Docker may be unavailable while configuration continues; "
                "final diagnostics will keep Finish disabled until Docker-dependent checks pass.",
                markup=False,
            )
            yield Static(id="prerequisite-report", markup=False)
            yield Button("Run prerequisite checks", id="validate-prerequisites", variant="primary")

        with Vertical(id="panel-server_storage", classes="step-panel"):
            yield Label("State database path")
            yield Input(id="storage-state-db")
            yield Label("Audit log path")
            yield Input(id="storage-audit")
            yield Label("SSH keys directory")
            yield Input(id="storage-keys")
            yield Label("Pinned known_hosts path")
            yield Input(id="storage-known-hosts")
            yield Static(
                "Validation is read-only. Directory creation is a separate explicit action below.",
                markup=False,
            )
            yield Button("Validate storage paths", id="validate-storage", variant="primary")
            yield Button("Create reviewed missing directories", id="create-storage")

        with Vertical(id="panel-docker_workspace", classes="step-panel"):
            yield Label("Default workspace image")
            yield Input(id="docker-image")
            yield Label("Workspace path inside container")
            yield Input(id="docker-workspace-path")
            yield Label("Container user: host or root")
            yield Input(id="docker-user")
            yield Label("Git directory mode: read-write, read-only, or none")
            yield Input(id="docker-git-dir-mode")
            yield Label("Idle stop duration, e.g. 4h or never")
            yield Input(id="docker-idle-stop")
            yield Checkbox(
                "Explicitly defer a missing image until final diagnostics",
                id="docker-defer-image",
            )
            yield Static(id="docker-build-command", markup=False)
            yield Button("Validate Docker workspace", id="validate-docker", variant="primary")
            yield Button("Build reviewed default image", id="build-docker-image")

        with Vertical(id="panel-repository_access", classes="step-panel"):
            yield Static(
                "Explicit repositories are registered one by one. Repository roots are optional "
                "discovery locations and do not replace remote-identity verification.",
                markup=False,
            )
            yield DataTable(id="repository-table")
            yield Label("Repository id")
            yield Input(id="repo-id")
            yield Label("Absolute local repository path")
            yield Input(id="repo-path")
            yield Label("Canonical GitHub owner/repo")
            yield Input(id="repo-github")
            yield Label("Optional repository SSH key filename")
            yield Input(id="repo-ssh-key")
            yield Button("Add / replace repository", id="repo-add")
            yield Button("Verify entered repository read-only", id="repo-verify")
            yield Label("Repository id to remove")
            yield Input(id="repo-remove-id")
            yield Checkbox("Confirm repository removal", id="repo-confirm-remove")
            yield Button("Remove repository", id="repo-remove", variant="warning")
            yield Label("Repository discovery roots (comma-separated absolute paths)")
            yield Input(id="repository-roots")
            yield Button(
                "Validate repository configuration",
                id="validate-repositories",
                variant="primary",
            )
            yield Static(id="repository-message", markup=False)

        with Vertical(id="panel-ssh_and_github", classes="step-panel"):
            yield Static(
                "Recommended: repository-scoped SSH + agent mode. Shared scope weakens isolation; "
                "file mode exposes the mounted private key to container processes.",
                markup=False,
            )
            yield Label("SSH mode: agent or file")
            yield Input(id="ssh-mode")
            yield Label("SSH scope: repository or shared")
            yield Input(id="ssh-scope")
            yield Label("Shared key filename (only for shared scope; blank means none)")
            yield Input(id="ssh-shared-key")
            yield Label("GitHub API auth: none, token, or app")
            yield Input(id="github-api-auth")
            yield Label("GitHub API credential file path (token mode only; never token contents)")
            yield Input(id="github-auth-file")
            yield Label("GitHub API base URL")
            yield Input(id="github-api-base-url")
            yield Static(
                "GitHub App is accepted by the production config but runtime API verification is "
                "currently unimplemented; the wizard will report it as unverified, never PASS.",
                markup=False,
            )
            yield Button(
                "Validate SSH / GitHub choices", id="validate-credentials", variant="primary"
            )
            yield Static(id="credential-message", markup=False)

        with Vertical(id="panel-git_safety_policy", classes="step-panel"):
            yield Static(
                "Recommended uses the production safe defaults. Conservative denies ordinary push, "
                "default-branch commit/push, branch deletion, and requires verified remote "
                "protection. Custom must specify every current GitPolicyConfig field as JSON.",
                markup=False,
            )
            yield Label("Preset: recommended, conservative, or custom")
            yield Input(id="git-policy-preset", value="recommended")
            yield Label("Custom policy JSON (used only when preset=custom)")
            yield Input(
                id="git-policy-custom",
                value=json.dumps(recommended_policy().model_dump(mode="json")),
            )
            yield Button("Validate Git safety policy", id="validate-git-policy", variant="primary")
            yield Static(id="git-policy-message", markup=False)

        with Vertical(id="panel-runtime_and_resources", classes="step-panel"):
            yield Static(
                "Advanced runtime fields are untouched unless this editor is explicitly enabled. "
                "Units are production units: byte counts are integers, timeouts are seconds, memory "
                "uses Docker size strings such as 4g.",
                markup=False,
            )
            yield Checkbox(
                "Edit advanced runtime/filesystem/resources/logging settings",
                id="runtime-edit",
                value=self.advanced,
            )
            yield Label(
                "Advanced overrides JSON with filesystem/execution/resources/logging objects"
            )
            yield Input(
                id="runtime-json",
                value=json.dumps(production_runtime_defaults()),
            )
            yield Button("Validate runtime settings", id="validate-runtime", variant="primary")
            yield Static(id="runtime-message", markup=False)

        with Vertical(id="panel-chatgpt_connection", classes="step-panel"):
            yield Checkbox("Configure optional ChatGPT Secure MCP Tunnel", id="chatgpt-enabled")
            yield Static(
                "OpenAI documentation is the source of truth for tunnel and ChatGPT UI availability. "
                "The wizard stores only the non-secret tunnel id/profile; runtime credentials must "
                "already be supplied by your external secure environment.",
                markup=False,
            )
            yield Label("Tunnel id")
            yield Input(id="tunnel-id")
            yield Label("Tunnel profile name")
            yield Input(id="tunnel-profile", value="chatgpt-sandbox")
            yield Static(id="tunnel-command", markup=False)
            yield Button("Open current Secure MCP Tunnel documentation", id="tunnel-open-docs")
            yield Button("Create / repair reviewed tunnel profile", id="tunnel-apply")
            yield Checkbox(
                "I completed the ChatGPT custom MCP app/refresh step (USER-ATTESTED)",
                id="chatgpt-attested",
            )
            yield Button(
                "Validate optional ChatGPT connection", id="validate-chatgpt", variant="primary"
            )
            yield Static(id="tunnel-message", markup=False)

        with Vertical(id="panel-configuration_review", classes="step-panel"):
            yield Static(id="review-target", markup=False)
            yield Checkbox(
                "I explicitly approve repair/replacement if the existing config is invalid",
                id="repair-invalid",
            )
            yield Button("Prepare exact configuration review", id="prepare-review", variant="primary")
            yield Static(id="review-summary", markup=False)
            yield Button(
                "Write reviewed configuration",
                id="write-reviewed-config",
                variant="warning",
                disabled=True,
            )
            yield Static(id="write-result", markup=False)

        with Vertical(id="panel-final_validation", classes="step-panel"):
            yield Static(
                "Finish is enabled only when all mandatory machine-verifiable diagnostics pass. "
                "Optional/user-attested ChatGPT status is shown separately from machine PASS.",
                markup=False,
            )
            yield Button(
                "Run / rerun final diagnostics", id="run-final-diagnostics", variant="primary"
            )
            yield Static(id="diagnostic-matrix", markup=False)
            yield Static(id="attestation-summary", markup=False)
