"""Concrete Textual controls for each logical setup-wizard step."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from dev_workspace_mcp.setup.git_policy_form import (
    ALLOW_DENY_VALUES,
    ALLOWED_URL_SCHEME_VALUES,
    DEFAULT_BRANCH_PUSH_VALUES,
    PULL_STRATEGY_VALUES,
    REMOTE_PROTECTION_CHECK_VALUES,
    REMOTE_PROTECTION_VALUES,
    REMOTE_WRITE_POLICY_VALUES,
)
from dev_workspace_mcp.setup.runtime_setup import production_runtime_defaults


def _display(value: object) -> str:
    return "" if value is None else str(value)


def _options(values: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((value, value) for value in values)


class WizardStepPanels(Vertical):
    """All logical step panels; the app shows exactly one at a time."""

    def __init__(self, *, advanced: bool) -> None:
        super().__init__(id="step-content")
        self.advanced = advanced

    def compose(self) -> ComposeResult:
        with Vertical(id="panel-local_prerequisites", classes="step-panel"):
            yield Static(
                "Checks Python and Docker. Docker may be unavailable while "
                "configuration continues; final diagnostics will keep Finish disabled until "
                "Docker-dependent checks pass.",
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
                "Validation is read-only. Creation/population is a separate explicit action below; "
                "when known_hosts is absent or empty, that action writes the production-pinned "
                "GitHub host-key set rather than using trust-on-first-use.",
                markup=False,
            )
            yield Button("Validate storage paths", id="validate-storage", variant="primary")
            yield Button("Create reviewed storage paths / pinned known_hosts", id="create-storage")

        with Vertical(id="panel-docker_workspace", classes="step-panel"):
            yield Label("Default workspace image")
            yield Input(id="docker-image")
            yield Label("Workspace path inside container")
            yield Input(id="docker-workspace-path")
            yield Label("Container user")
            yield Select(
                (("Host user (recommended)", "host"), ("Root (advanced)", "root")),
                id="docker-user",
                value="host",
                allow_blank=False,
            )
            yield Static(
                "Root mode is nonstandard and weakens host-user parity; select it only when a "
                "project genuinely requires root inside the workspace.",
                markup=False,
            )
            yield Label("Git directory visibility")
            yield Select(
                (
                    ("Read/write .git", "read-write"),
                    ("Read-only .git", "read-only"),
                    ("Hide .git", "none"),
                ),
                id="docker-git-dir-mode",
                value="read-write",
                allow_blank=False,
            )
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
            yield Label("Container credential exposure")
            yield Select(
                (
                    ("SSH agent (recommended; key material stays host-side)", "agent"),
                    ("Mounted key file (container can read private key)", "file"),
                ),
                id="ssh-mode",
                value="agent",
                allow_blank=False,
            )
            yield Label("Credential isolation scope")
            yield Select(
                (
                    ("Dedicated key per repository (recommended)", "repository"),
                    ("Shared key across repositories (weaker isolation)", "shared"),
                ),
                id="ssh-scope",
                value="repository",
                allow_blank=False,
            )
            yield Label("Shared key filename (only for shared scope; blank means none)")
            yield Input(id="ssh-shared-key")
            yield Label("GitHub API authentication")
            yield Select(
                (
                    ("None", "none"),
                    ("Token from credential file", "token"),
                    ("GitHub App (production config accepts; verification unimplemented)", "app"),
                ),
                id="github-api-auth",
                value="none",
                allow_blank=False,
            )
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
                "protection. Custom exposes every current GitPolicyConfig field as guided controls.",
                markup=False,
            )
            yield Static(
                "Consequences: force push rewrites remote history; force-with-lease is safer but "
                "still rewrites published history; branch/tag deletion removes remote refs; amend "
                "rewrites a published commit; default-branch controls protect the main line; "
                "ff_only pull refuses implicit merge/rebase; remote protection off/warn/deny "
                "controls how uncertainty affects mutating Git operations. Required checks choose "
                "which remote protections must be verified.",
                markup=False,
            )
            yield Label("Safety preset")
            yield Select(
                (
                    ("Recommended", "recommended"),
                    ("Conservative", "conservative"),
                    ("Custom", "custom"),
                ),
                id="git-policy-preset",
                value="recommended",
                allow_blank=False,
            )
            yield Static("Custom Git policy controls", markup=False)
            yield Checkbox("Allow ordinary push", id="git-policy-push-allow", value=True)
            yield Label("Default branch push")
            yield Select(
                _options(DEFAULT_BRANCH_PUSH_VALUES),
                id="git-policy-default-branch-push",
                value="allow_fast_forward",
                allow_blank=False,
            )
            yield Label("Force push")
            yield Select(
                _options(REMOTE_WRITE_POLICY_VALUES),
                id="git-policy-force-push",
                value="deny",
                allow_blank=False,
            )
            yield Label("Force with lease")
            yield Select(
                _options(REMOTE_WRITE_POLICY_VALUES),
                id="git-policy-force-with-lease",
                value="deny",
                allow_blank=False,
            )
            yield Label("Delete remote branch")
            yield Select(
                _options(REMOTE_WRITE_POLICY_VALUES),
                id="git-policy-delete-remote-branch",
                value="require_approval",
                allow_blank=False,
            )
            yield Label("Delete remote tag")
            yield Select(
                _options(REMOTE_WRITE_POLICY_VALUES),
                id="git-policy-delete-remote-tag",
                value="deny",
                allow_blank=False,
            )
            yield Checkbox(
                "Allow direct default-branch commits",
                id="git-policy-default-branch-commit-allow",
                value=True,
            )
            yield Label("Amend published commit")
            yield Select(
                _options(REMOTE_WRITE_POLICY_VALUES),
                id="git-policy-amend-published",
                value="deny",
                allow_blank=False,
            )
            yield Label("Pull strategy")
            yield Select(
                _options(PULL_STRATEGY_VALUES),
                id="git-policy-pull-strategy",
                value="ff_only",
                allow_blank=False,
            )
            yield Label("Require remote protection")
            yield Select(
                _options(REMOTE_PROTECTION_VALUES),
                id="git-policy-remote-protection",
                value="warn",
                allow_blank=False,
            )
            yield Static("Required remote-protection checks", markup=False)
            yield Checkbox(
                "Force-push protection",
                id="git-policy-check-force-push",
                value="force_push" in REMOTE_PROTECTION_CHECK_VALUES,
            )
            yield Checkbox(
                "Branch-deletion protection",
                id="git-policy-check-deletion",
                value="deletion" in REMOTE_PROTECTION_CHECK_VALUES,
            )
            yield Static("Allowed remote URL schemes", markup=False)
            yield Checkbox(
                "SSH remote URLs",
                id="git-policy-url-ssh",
                value="ssh" in ALLOWED_URL_SCHEME_VALUES,
            )
            yield Checkbox(
                "HTTPS remote URLs",
                id="git-policy-url-https",
                value="https" in ALLOWED_URL_SCHEME_VALUES,
            )
            yield Static(
                "No raw secret or credential value belongs in Git policy. Values are validated "
                "through the production GitPolicyConfig model before being persisted.",
                markup=False,
            )
            yield Button("Validate Git safety policy", id="validate-git-policy", variant="primary")
            yield Static(id="git-policy-message", markup=False)

        runtime_defaults = production_runtime_defaults()
        filesystem = runtime_defaults["filesystem"]
        execution = runtime_defaults["execution"]
        resources = runtime_defaults["resources"]
        logging = runtime_defaults["logging"]
        with Vertical(id="panel-runtime_and_resources", classes="step-panel"):
            yield Static(
                "Advanced runtime fields are untouched unless this editor is explicitly enabled. "
                "Units are production units: byte counts are integers, timeouts are seconds, "
                "memory uses Docker size strings such as 4g.",
                markup=False,
            )
            yield Checkbox(
                "Edit advanced runtime/filesystem/resources/logging settings",
                id="runtime-edit",
                value=self.advanced,
            )
            with Vertical(id="runtime-advanced-fields"):
                yield Static("Filesystem limits", markup=False)
                yield Label("Maximum read bytes")
                yield Input(
                    id="runtime-fs-max-read-bytes",
                    value=_display(filesystem["max_read_bytes"]),
                )
                yield Label("Maximum write bytes")
                yield Input(
                    id="runtime-fs-max-write-bytes",
                    value=_display(filesystem["max_write_bytes"]),
                )
                yield Label("Maximum patch bytes")
                yield Input(
                    id="runtime-fs-max-patch-bytes",
                    value=_display(filesystem["max_patch_bytes"]),
                )
                yield Label("Maximum listing entries")
                yield Input(
                    id="runtime-fs-max-listing-entries",
                    value=_display(filesystem["max_listing_entries"]),
                )
                yield Label("Maximum search results")
                yield Input(
                    id="runtime-fs-max-search-results",
                    value=_display(filesystem["max_search_results"]),
                )

                yield Static("Execution and retention", markup=False)
                yield Label("Synchronous timeout seconds")
                yield Input(
                    id="runtime-exec-sync-timeout-seconds",
                    value=_display(execution["sync_timeout_seconds"]),
                )
                yield Label("Default background job timeout seconds")
                yield Input(
                    id="runtime-exec-default-job-timeout-seconds",
                    value=_display(execution["default_job_timeout_seconds"]),
                )
                yield Label("Maximum captured output bytes")
                yield Input(
                    id="runtime-exec-max-output-bytes",
                    value=_display(execution["max_output_bytes"]),
                )
                yield Label("Tail bytes retained after truncation")
                yield Input(
                    id="runtime-exec-tail-keep-bytes",
                    value=_display(execution["tail_keep_bytes"]),
                )
                yield Label("Response tail lines")
                yield Input(
                    id="runtime-exec-response-tail-lines",
                    value=_display(execution["response_tail_lines"]),
                )
                yield Label("Job retention days")
                yield Input(
                    id="runtime-exec-job-retention-days",
                    value=_display(execution["job_retention_days"]),
                )
                yield Label("Maximum total job-log bytes")
                yield Input(
                    id="runtime-exec-job-log-max-total-bytes",
                    value=_display(execution["job_log_max_total_bytes"]),
                )
                yield Label("Default exec_output response bytes")
                yield Input(
                    id="runtime-exec-default-output-response-bytes",
                    value=_display(execution["default_output_response_bytes"]),
                )

                yield Static("Container resource limits", markup=False)
                yield Label("CPUs (blank = unbounded)")
                yield Input(id="runtime-resources-cpus", value=_display(resources["cpus"]))
                yield Label("Memory Docker size string, e.g. 4g (blank = unbounded)")
                yield Input(id="runtime-resources-memory", value=_display(resources["memory"]))
                yield Label("PID limit (blank = unbounded)")
                yield Input(
                    id="runtime-resources-pids-limit",
                    value=_display(resources["pids_limit"]),
                )

                yield Static("Operational logging", markup=False)
                yield Label("Logging level")
                yield Select(
                    tuple(
                        (level, level)
                        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
                    ),
                    id="runtime-logging-level",
                    value=_display(logging["level"]),
                    allow_blank=False,
                )
            yield Button("Validate runtime settings", id="validate-runtime", variant="primary")
            yield Static(id="runtime-message", markup=False)

        with Vertical(id="panel-chatgpt_connection", classes="step-panel"):
            yield Checkbox("Configure optional ChatGPT Secure MCP Tunnel", id="chatgpt-enabled")
            yield Static(
                "OpenAI documentation is the source of truth for tunnel and ChatGPT UI "
                "availability. The wizard stores only the non-secret tunnel id/profile; runtime "
                "credentials must already be supplied by your external secure environment.",
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
            yield Button(
                "Prepare exact configuration review", id="prepare-review", variant="primary"
            )
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
            yield Static(id="smoke-guidance", markup=False)
