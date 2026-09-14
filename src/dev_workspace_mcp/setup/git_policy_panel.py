"""Textual controls for the guided Git-policy setup panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Label, Select, Static

from dev_workspace_mcp.setup.git_policy_form import (
    ALLOWED_URL_SCHEME_VALUES,
    DEFAULT_BRANCH_PUSH_VALUES,
    PULL_STRATEGY_VALUES,
    REMOTE_PROTECTION_CHECK_VALUES,
    REMOTE_PROTECTION_VALUES,
    REMOTE_WRITE_POLICY_VALUES,
)


def _options(values: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((value, value) for value in values)


def git_policy_panel() -> ComposeResult:
    """Yield the complete guided Git-policy panel."""

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
