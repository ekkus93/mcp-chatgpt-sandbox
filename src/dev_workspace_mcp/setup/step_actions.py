"""Textual step-event orchestration over presentation-independent setup services.

The application shell owns lifecycle/navigation/rendering.  This mixin owns only
step-specific widget extraction and calls into the ordinary setup domain modules;
it does not implement YAML parsing, atomic configuration persistence, or runtime
policy semantics itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Protocol, TypeVar, cast

import docker
from docker.errors import DockerException
from textual import on
from textual.widgets import Button, Checkbox, Input, Select, Static

from dev_workspace_mcp.setup.config_writer import (
    ConfigUpdatePlan,
    SetupConfigError,
    commit_config_update,
    inspect_config,
    prepare_config_update,
)
from dev_workspace_mcp.setup.diagnostics import format_diagnostics, run_diagnostics
from dev_workspace_mcp.setup.docker_setup import (
    DockerBuildPlan,
    DockerSetupChoices,
    SetupDockerError,
    build_default_image,
    default_image_build_command,
    image_exists,
    prepare_default_image_build_plan,
)
from dev_workspace_mcp.setup.git_policy_setup import GitPolicyPreset, materialize_policy
from dev_workspace_mcp.setup.host import (
    SetupHostError,
    StorageChoices,
    check_prerequisites,
    parent_is_writable,
    prepare_storage_directories,
)
from dev_workspace_mcp.setup.materialize import materialize_setup
from dev_workspace_mcp.setup.model import (
    SetupRepositoryState,
    SetupState,
    SetupStep,
    clone_setup_state,
    invalidate_from_step,
)
from dev_workspace_mcp.setup.repository_setup import (
    SetupRepositoryError,
    add_repository,
    edit_repository,
    remove_repository,
    validate_repository_roots,
    verify_repository,
)
from dev_workspace_mcp.setup.runtime_setup import RuntimeChoices, SetupRuntimeError
from dev_workspace_mcp.setup.ssh_github_setup import (
    CredentialChoices,
    SetupCredentialError,
    verify_credential_files,
)
from dev_workspace_mcp.setup.tunnel import (
    OPENAI_TUNNEL_DOC_URL,
    SetupTunnelError,
    TunnelProfilePlan,
    apply_tunnel_profile,
    build_tunnel_profile_plan,
    check_tunnel_profile,
    open_setup_documentation,
)

_T = TypeVar("_T")


class _ActionHost(Protocol):
    state: SetupState
    config_path: Path
    no_browser: bool
    _review_plan: ConfigUpdatePlan | None
    _pending_docker_build_plan: DockerBuildPlan | None
    _pending_tunnel_plan: TunnelProfilePlan | None

    def query_one(self, selector: str, expect_type: type[_T]) -> _T: ...

    def set_step_validation(
        self, step: SetupStep, *, valid: bool, message: str | None = None
    ) -> None: ...

    def _save_validated_state(
        self,
        candidate: SetupState,
        *,
        step: SetupStep,
        message: str,
        skipped: bool = False,
    ) -> None: ...

    def _save(self, candidate: SetupState) -> bool: ...

    def _refresh_repository_table(self) -> None: ...

    def _refresh(self) -> None: ...


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _selected(host: _ActionHost, selector: str, *, label: str) -> str:
    value = host.query_one(selector, Select).value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be selected")
    return value


def _required_int(host: _ActionHost, selector: str, *, label: str) -> int:
    raw = host.query_one(selector, Input).value.strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be an integer") from exc


def _optional_int(host: _ActionHost, selector: str, *, label: str) -> int | None:
    raw = host.query_one(selector, Input).value.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be an integer or blank") from exc


def _optional_float(host: _ActionHost, selector: str, *, label: str) -> float | None:
    raw = host.query_one(selector, Input).value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise SetupRuntimeError(f"{label} must be a number or blank") from exc


def _command_text(argv: tuple[str, ...]) -> str:
    return " ".join(argv)


class SetupStepActionsMixin:
    """Step handlers inherited by :class:`SetupWizardApp`."""

    def _action_host(self) -> _ActionHost:
        return cast(_ActionHost, self)

    @on(Button.Pressed, "#validate-prerequisites")
    def validate_prerequisites(self) -> None:
        host = self._action_host()
        report = check_prerequisites()
        text = (
            f"Python supported: {'PASS' if report.python_supported else 'FAIL'}\n"
            f"uv: {report.uv_path or 'not found (only required for source workflows)'}\n"
            f"Docker daemon: "
            f"{'PASS' if report.docker_reachable else 'ACTION REQUIRED at final validation'}"
        )
        host.query_one("#prerequisite-report", Static).update(text)
        host.set_step_validation(
            SetupStep.PREREQUISITES,
            valid=not report.fatal,
            message=(
                "Host prerequisite checks completed."
                if not report.fatal
                else "Supported Python runtime is required."
            ),
        )

    def _storage_from_widgets(self) -> StorageChoices:
        host = self._action_host()
        return StorageChoices(
            state_db_path=Path(host.query_one("#storage-state-db", Input).value).expanduser(),
            audit_path=Path(host.query_one("#storage-audit", Input).value).expanduser(),
            keys_dir=Path(host.query_one("#storage-keys", Input).value).expanduser(),
            known_hosts=Path(host.query_one("#storage-known-hosts", Input).value).expanduser(),
        )

    @on(Button.Pressed, "#validate-storage")
    def validate_storage(self) -> None:
        host = self._action_host()
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
            host.set_step_validation(SetupStep.STORAGE, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(host.state)
        candidate.choices.state_db_path = str(choices.state_db_path)
        candidate.choices.audit_path = str(choices.audit_path)
        candidate.choices.keys_dir = str(choices.keys_dir)
        candidate.choices.known_hosts = str(choices.known_hosts)
        candidate.reviewed_fields |= frozenset({"state_db_path", "audit"})
        host._save_validated_state(
            candidate,
            step=SetupStep.STORAGE,
            message="Storage paths are production-valid and their parents are writable.",
        )

    @on(Button.Pressed, "#create-storage")
    def create_storage(self) -> None:
        host = self._action_host()
        try:
            choices = self._storage_from_widgets()
            prepare_storage_directories(choices, approved=True)
        except SetupHostError as exc:
            host.set_step_validation(SetupStep.STORAGE, valid=False, message=str(exc))
            return
        host.query_one("#validation-message", Static).update(
            "Reviewed missing storage directories were created with restrictive permissions."
        )

    def _docker_from_widgets(self) -> DockerSetupChoices:
        host = self._action_host()
        return DockerSetupChoices(
            default_image=host.query_one("#docker-image", Input).value.strip(),
            workspace_path=host.query_one("#docker-workspace-path", Input).value.strip(),
            container_user=_selected(host, "#docker-user", label="container user"),
            git_dir_mode=_selected(host, "#docker-git-dir-mode", label="Git directory mode"),
            idle_stop_after=host.query_one("#docker-idle-stop", Input).value.strip(),
        )

    @on(Button.Pressed, "#validate-docker")
    def validate_docker(self) -> None:
        host = self._action_host()
        try:
            choices = self._docker_from_widgets()
            choices.config_fragments()
            defer_image = host.query_one("#docker-defer-image", Checkbox).value
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
                host.set_step_validation(SetupStep.DOCKER, valid=False, message=message)
                return
        except SetupDockerError as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        else:
            message = (
                "Docker workspace values are valid and the selected image is present."
                if present
                else "Docker workspace values are valid; missing image explicitly deferred."
            )
        command = _command_text(default_image_build_command(choices.default_image))
        host.query_one("#docker-build-command", Static).update(f"Build command: {command}")
        candidate = clone_setup_state(host.state)
        candidate.choices.docker_image = choices.default_image
        candidate.choices.workspace_path = choices.workspace_path
        candidate.choices.container_user = choices.container_user
        candidate.choices.git_dir_mode = choices.git_dir_mode
        candidate.choices.idle_stop_after = choices.idle_stop_after
        candidate.reviewed_fields |= frozenset({"docker", "workspace", "container"})
        host._save_validated_state(candidate, step=SetupStep.DOCKER, message=message)

    @on(Button.Pressed, "#prepare-docker-build")
    def prepare_docker_build(self) -> None:
        host = self._action_host()
        host._pending_docker_build_plan = None
        host.query_one("#build-docker-image", Button).disabled = True
        try:
            choices = self._docker_from_widgets()
            plan = prepare_default_image_build_plan(image=choices.default_image)
        except (ValueError, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        host._pending_docker_build_plan = plan
        host.query_one("#docker-build-command", Static).update(
            "Reviewed build plan: " + _command_text(plan.argv)
        )
        host.query_one("#build-docker-image", Button).disabled = False
        host.set_step_validation(
            SetupStep.DOCKER,
            valid=False,
            message="Review the Docker build command, then apply the stored plan separately.",
        )

    @on(Button.Pressed, "#build-docker-image")
    def build_docker_image(self) -> None:
        host = self._action_host()
        plan = host._pending_docker_build_plan
        if plan is None:
            host.set_step_validation(
                SetupStep.DOCKER,
                valid=False,
                message="Prepare and review a Docker build plan before applying it.",
            )
            return
        try:
            client = docker.from_env()
            try:
                build_default_image(
                    client,
                    image=plan.image,
                    build_context=plan.build_context,
                    approved=True,
                )
            finally:
                client.close()
        except (DockerException, SetupDockerError) as exc:
            host.set_step_validation(SetupStep.DOCKER, valid=False, message=str(exc))
            return
        host._pending_docker_build_plan = None
        host.query_one("#build-docker-image", Button).disabled = True
        host.set_step_validation(
            SetupStep.DOCKER,
            valid=False,
            message="Docker build completed from the reviewed plan; validate Docker again.",
        )

    def _repository_from_widgets(self) -> SetupRepositoryState:
        host = self._action_host()
        return SetupRepositoryState.from_dict(
            {
                "id": host.query_one("#repo-id", Input).value.strip(),
                "path": host.query_one("#repo-path", Input).value.strip(),
                "github": host.query_one("#repo-github", Input).value.strip(),
                "ssh_key": _blank_to_none(host.query_one("#repo-ssh-key", Input).value),
            }
        )

    @on(Button.Pressed, "#repo-add")
    def add_or_replace_repository(self) -> None:
        host = self._action_host()
        try:
            repository = self._repository_from_widgets()
            existing = any(item.id == repository.id for item in host.state.repositories)
            repositories = (
                edit_repository(
                    host.state.repositories,
                    repository_id=repository.id,
                    replacement=repository,
                )
                if existing
                else add_repository(host.state.repositories, repository)
            )
        except (ValueError, SetupRepositoryError) as exc:
            host.query_one("#repository-message", Static).update(str(exc))
            return
        candidate = invalidate_from_step(host.state, SetupStep.REPOSITORIES)
        candidate.repositories = repositories
        candidate.reviewed_fields |= frozenset({"repositories"})
        if host._save(candidate):
            host.state = candidate
            host._refresh_repository_table()
            host.query_one("#repository-message", Static).update(
                "Repository record saved; validate repository configuration before continuing."
            )
            host._refresh()

    @on(Button.Pressed, "#repo-verify")
    def verify_entered_repository(self) -> None:
        host = self._action_host()
        try:
            report = verify_repository(self._repository_from_widgets())
        except (ValueError, SetupRepositoryError) as exc:
            host.query_one("#repository-message", Static).update(str(exc))
            return
        host.query_one("#repository-message", Static).update(report.message)

    @on(Button.Pressed, "#repo-remove")
    def remove_entered_repository(self) -> None:
        host = self._action_host()
        repository_id = host.query_one("#repo-remove-id", Input).value.strip()
        confirmed = host.query_one("#repo-confirm-remove", Checkbox).value
        try:
            repositories = remove_repository(
                host.state.repositories,
                repository_id=repository_id,
                confirmed=confirmed,
            )
        except SetupRepositoryError as exc:
            host.query_one("#repository-message", Static).update(str(exc))
            return
        candidate = invalidate_from_step(host.state, SetupStep.REPOSITORIES)
        candidate.repositories = repositories
        candidate.reviewed_fields |= frozenset({"repositories"})
        if host._save(candidate):
            host.state = candidate
            host._refresh_repository_table()
            host.query_one("#repo-confirm-remove", Checkbox).value = False
            host.query_one("#repository-message", Static).update("Repository record removed.")
            host._refresh()

    def _repository_roots_from_widget(self) -> tuple[Path, ...]:
        host = self._action_host()
        value = host.query_one("#repository-roots", Input).value
        roots = [Path(item.strip()).expanduser() for item in value.split(",") if item.strip()]
        return validate_repository_roots(roots)

    @on(Button.Pressed, "#validate-repositories")
    def validate_repositories(self) -> None:
        host = self._action_host()
        try:
            roots = self._repository_roots_from_widget()
            reports = tuple(verify_repository(repository) for repository in host.state.repositories)
            invalid = [report.repository.id for report in reports if not report.valid]
            if invalid:
                raise SetupRepositoryError(
                    "Repository verification is unresolved for: " + ", ".join(invalid)
                )
        except (ValueError, SetupRepositoryError) as exc:
            host.set_step_validation(SetupStep.REPOSITORIES, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(host.state)
        candidate.choices.repository_roots = tuple(str(root) for root in roots)
        candidate.reviewed_fields |= frozenset({"repositories", "repository_roots"})
        host._save_validated_state(
            candidate,
            step=SetupStep.REPOSITORIES,
            message="Repository records and discovery roots are read-only verified.",
        )

    def _credential_choices_from_widgets(self) -> CredentialChoices:
        host = self._action_host()
        choices = host.state.choices
        if choices.keys_dir is None or choices.known_hosts is None:
            raise SetupCredentialError("Storage step must select keys_dir and known_hosts first")
        auth_file = _blank_to_none(host.query_one("#github-auth-file", Input).value)
        try:
            mode = _selected(host, "#ssh-mode", label="SSH mode")
            scope = _selected(host, "#ssh-scope", label="SSH scope")
            api_auth = _selected(host, "#github-api-auth", label="GitHub API authentication")
        except ValueError as exc:
            raise SetupCredentialError(str(exc)) from exc
        return CredentialChoices(
            mode=mode,  # type: ignore[arg-type]
            scope=scope,  # type: ignore[arg-type]
            keys_dir=Path(choices.keys_dir),
            known_hosts=Path(choices.known_hosts),
            shared_key=_blank_to_none(host.query_one("#ssh-shared-key", Input).value),
            github_api_auth=api_auth,  # type: ignore[arg-type]
            github_api_token_path=Path(auth_file).expanduser() if auth_file is not None else None,
            github_api_base_url=host.query_one("#github-api-base-url", Input).value.strip(),
        )

    @on(Button.Pressed, "#validate-credentials")
    def validate_credentials(self) -> None:
        host = self._action_host()
        try:
            choices = self._credential_choices_from_widgets()
            choices.config_fragments()
            checks = verify_credential_files(choices, host.state.repositories)
        except SetupCredentialError as exc:
            host.set_step_validation(SetupStep.CREDENTIALS, valid=False, message=str(exc))
            return
        blocking = [check.message for check in checks if check.status == "action_required"]
        host.query_one("#credential-message", Static).update(
            "\n".join(f"{check.status.upper()}: {check.message}" for check in checks)
        )
        if blocking:
            host.set_step_validation(
                SetupStep.CREDENTIALS,
                valid=False,
                message="Credential references still require action.",
            )
            return
        candidate = clone_setup_state(host.state)
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
        host._save_validated_state(
            candidate,
            step=SetupStep.CREDENTIALS,
            message="Credential references are valid; optional API uncertainty remains explicit.",
        )

    @on(Button.Pressed, "#validate-git-policy")
    def validate_git_policy(self) -> None:
        host = self._action_host()
        try:
            preset = GitPolicyPreset(
                _selected(host, "#git-policy-preset", label="Git safety preset")
            )
            custom: dict[str, object] | None = None
            if preset is GitPolicyPreset.CUSTOM:
                raw = json.loads(host.query_one("#git-policy-custom", Input).value)
                if not isinstance(raw, dict):
                    raise ValueError("Custom Git policy JSON must be an object")
                custom = raw
            policy = materialize_policy(preset, custom=custom)
        except (ValueError, json.JSONDecodeError) as exc:
            host.set_step_validation(SetupStep.GIT_POLICY, valid=False, message=str(exc))
            return
        candidate = clone_setup_state(host.state)
        candidate.choices.git_policy_preset = preset.value
        candidate.choices.git_policy_custom = (
            policy.model_dump(mode="json") if preset is GitPolicyPreset.CUSTOM else None
        )
        candidate.reviewed_fields |= frozenset({"git_policy"})
        host.query_one("#git-policy-message", Static).update(
            "Validated production Git policy: " + json.dumps(policy.model_dump(mode="json"))
        )
        host._save_validated_state(
            candidate,
            step=SetupStep.GIT_POLICY,
            message="Git safety policy is production-valid.",
        )

    def _runtime_choices_from_widgets(self) -> RuntimeChoices:
        host = self._action_host()
        level = _selected(host, "#runtime-logging-level", label="logging level")
        memory = _blank_to_none(host.query_one("#runtime-resources-memory", Input).value)
        return RuntimeChoices(
            filesystem={
                "max_read_bytes": _required_int(
                    host, "#runtime-fs-max-read-bytes", label="Maximum read bytes"
                ),
                "max_write_bytes": _required_int(
                    host, "#runtime-fs-max-write-bytes", label="Maximum write bytes"
                ),
                "max_patch_bytes": _required_int(
                    host, "#runtime-fs-max-patch-bytes", label="Maximum patch bytes"
                ),
                "max_listing_entries": _required_int(
                    host, "#runtime-fs-max-listing-entries", label="Maximum listing entries"
                ),
                "max_search_results": _required_int(
                    host, "#runtime-fs-max-search-results", label="Maximum search results"
                ),
            },
            execution={
                "sync_timeout_seconds": _required_int(
                    host, "#runtime-exec-sync-timeout-seconds", label="Synchronous timeout"
                ),
                "default_job_timeout_seconds": _required_int(
                    host,
                    "#runtime-exec-default-job-timeout-seconds",
                    label="Default job timeout",
                ),
                "max_output_bytes": _required_int(
                    host,
                    "#runtime-exec-max-output-bytes",
                    label="Maximum captured output bytes",
                ),
                "tail_keep_bytes": _required_int(
                    host, "#runtime-exec-tail-keep-bytes", label="Tail keep bytes"
                ),
                "response_tail_lines": _required_int(
                    host, "#runtime-exec-response-tail-lines", label="Response tail lines"
                ),
                "job_retention_days": _required_int(
                    host, "#runtime-exec-job-retention-days", label="Job retention days"
                ),
                "job_log_max_total_bytes": _required_int(
                    host,
                    "#runtime-exec-job-log-max-total-bytes",
                    label="Maximum total job-log bytes",
                ),
                "default_output_response_bytes": _required_int(
                    host,
                    "#runtime-exec-default-output-response-bytes",
                    label="Default output response bytes",
                ),
            },
            resources={
                "cpus": _optional_float(host, "#runtime-resources-cpus", label="CPUs"),
                "memory": memory,
                "pids_limit": _optional_int(
                    host, "#runtime-resources-pids-limit", label="PID limit"
                ),
            },
            logging={"level": level},
        )

    @on(Button.Pressed, "#validate-runtime")
    def validate_runtime(self) -> None:
        host = self._action_host()
        edit = host.query_one("#runtime-edit", Checkbox).value
        candidate = clone_setup_state(host.state)
        runtime_fields = frozenset({"filesystem", "execution", "resources", "logging"})
        if not edit:
            candidate.reviewed_fields -= runtime_fields
            candidate.choices.runtime_overrides = None
            host._save_validated_state(
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
            host.set_step_validation(SetupStep.RUNTIME_LIMITS, valid=False, message=str(exc))
            return
        candidate.choices.runtime_overrides = fragments
        candidate.reviewed_fields = (candidate.reviewed_fields - runtime_fields) | frozenset(
            fragments
        )
        host.query_one("#runtime-message", Static).update(
            "Reviewed advanced sections: " + ", ".join(sorted(fragments))
        )
        host._save_validated_state(
            candidate,
            step=SetupStep.RUNTIME_LIMITS,
            message="Advanced runtime values are production-valid.",
        )

    def _tunnel_plan_from_widgets(self) -> TunnelProfilePlan:
        host = self._action_host()
        return build_tunnel_profile_plan(
            profile=host.query_one("#tunnel-profile", Input).value,
            tunnel_id=host.query_one("#tunnel-id", Input).value,
            config_path=host.config_path,
            project_root=Path.cwd(),
        )

    @on(Button.Pressed, "#tunnel-open-docs")
    def open_tunnel_docs(self) -> None:
        host = self._action_host()
        try:
            opened = open_setup_documentation(
                OPENAI_TUNNEL_DOC_URL,
                no_browser=host.no_browser,
                approved=True,
            )
        except SetupTunnelError as exc:
            host.query_one("#tunnel-message", Static).update(str(exc))
            return
        host.query_one("#tunnel-message", Static).update(
            "Browser opened." if opened else f"Open this URL manually: {OPENAI_TUNNEL_DOC_URL}"
        )

    @on(Button.Pressed, "#tunnel-prepare")
    def prepare_tunnel(self) -> None:
        host = self._action_host()
        host._pending_tunnel_plan = None
        host.query_one("#tunnel-apply", Button).disabled = True
        try:
            plan = self._tunnel_plan_from_widgets()
        except SetupTunnelError as exc:
            host.set_step_validation(SetupStep.CHATGPT, valid=False, message=str(exc))
            return
        host._pending_tunnel_plan = plan
        host.query_one("#tunnel-command", Static).update(
            "Server command: "
            f"{plan.server_command}\nReviewed profile command: {_command_text(plan.argv)}"
        )
        host.query_one("#tunnel-apply", Button).disabled = False
        host.set_step_validation(
            SetupStep.CHATGPT,
            valid=False,
            message="Review the tunnel profile command, then apply the stored plan separately.",
        )

    @on(Button.Pressed, "#tunnel-apply")
    def apply_tunnel(self) -> None:
        host = self._action_host()
        plan = host._pending_tunnel_plan
        if plan is None:
            host.set_step_validation(
                SetupStep.CHATGPT,
                valid=False,
                message="Prepare and review a tunnel profile plan before applying it.",
            )
            return
        try:
            apply_tunnel_profile(plan, approved=True)
        except SetupTunnelError as exc:
            host.query_one("#tunnel-message", Static).update(str(exc))
            return
        host._pending_tunnel_plan = None
        host.query_one("#tunnel-apply", Button).disabled = True
        host.query_one("#tunnel-message", Static).update(
            "Reviewed tunnel profile mutation completed; run validation next."
        )

    @on(Button.Pressed, "#validate-chatgpt")
    def validate_chatgpt(self) -> None:
        host = self._action_host()
        enabled = host.query_one("#chatgpt-enabled", Checkbox).value
        candidate = clone_setup_state(host.state)
        if not enabled:
            candidate.chatgpt_requested = False
            candidate.choices.tunnel_id = None
            candidate.choices.chatgpt_user_attested = False
            host._save_validated_state(
                candidate,
                step=SetupStep.CHATGPT,
                message="Optional ChatGPT connection deliberately skipped.",
                skipped=True,
            )
            return
        try:
            plan = self._tunnel_plan_from_widgets()
        except SetupTunnelError as exc:
            host.set_step_validation(SetupStep.CHATGPT, valid=False, message=str(exc))
            return
        host.query_one("#tunnel-command", Static).update("Server command: " + plan.server_command)
        health = check_tunnel_profile(plan.profile)
        attested = host.query_one("#chatgpt-attested", Checkbox).value
        host.query_one("#tunnel-message", Static).update(
            f"{'PASS' if health.healthy else 'ACTION REQUIRED'}: {health.message}\n"
            f"{'USER-ATTESTED' if attested else 'WAITING FOR USER'}: ChatGPT custom app/refresh"
        )
        if not health.healthy or not attested:
            host.set_step_validation(
                SetupStep.CHATGPT,
                valid=False,
                message=(
                    "Tunnel health and user attestation are required when ChatGPT "
                    "setup is selected."
                ),
            )
            return
        candidate.chatgpt_requested = True
        candidate.choices.tunnel_id = plan.tunnel_id
        candidate.choices.tunnel_profile = plan.profile
        candidate.choices.chatgpt_user_attested = True
        host._save_validated_state(
            candidate,
            step=SetupStep.CHATGPT,
            message=(
                "Tunnel is machine-healthy; ChatGPT UI completion is recorded as USER-ATTESTED."
            ),
        )

    @on(Button.Pressed, "#prepare-review")
    def prepare_review(self) -> None:
        host = self._action_host()
        try:
            inspection = inspect_config(host.config_path)
            repair_approved = host.query_one("#repair-invalid", Checkbox).value
            if inspection.exists and not inspection.valid and not repair_approved:
                raise SetupConfigError(
                    "Existing configuration is invalid; explicitly approve repair before review."
                )
            materialized = materialize_setup(host.state, new_config=not inspection.valid)
            plan = prepare_config_update(
                host.config_path,
                wizard_values=materialized.values,
                managed_fields=materialized.managed_fields,
                repair_invalid=repair_approved,
            )
        except (SetupConfigError, ValueError) as exc:
            host._review_plan = None
            host.query_one("#write-reviewed-config", Button).disabled = True
            host.set_step_validation(SetupStep.REVIEW, valid=False, message=str(exc))
            return
        host._review_plan = plan
        target = (
            f"Target: {host.config_path}\n"
            f"Status: {'existing' if plan.source_exists else 'new'}\n"
            f"Backup: "
            f"{'timestamped backup before replacement' if plan.source_exists else 'not needed'}"
        )
        host.query_one("#review-target", Static).update(target)
        host.query_one("#review-summary", Static).update("\n".join(plan.summary))
        host.query_one("#write-reviewed-config", Button).disabled = False
        host.set_step_validation(
            SetupStep.REVIEW,
            valid=False,
            message="Review prepared. Write reviewed configuration before continuing.",
        )

    @on(Button.Pressed, "#write-reviewed-config")
    def write_reviewed_config(self) -> None:
        host = self._action_host()
        if host._review_plan is None:
            host.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message="Prepare a fresh configuration review first.",
            )
            return
        try:
            result = commit_config_update(host.config_path, host._review_plan)
        except SetupConfigError as exc:
            host._review_plan = None
            host.query_one("#write-reviewed-config", Button).disabled = True
            host.set_step_validation(
                SetupStep.REVIEW,
                valid=False,
                message=f"{exc}. Review again before any retry.",
            )
            return
        backup = str(result.backup_path) if result.backup_path is not None else "none"
        host.query_one("#write-result", Static).update(
            f"Configuration written and production-reloaded successfully. Backup: {backup}"
        )
        host.set_step_validation(
            SetupStep.REVIEW,
            valid=True,
            message="Reviewed configuration was written, byte-verified, and production-reloaded.",
        )

    @on(Button.Pressed, "#run-final-diagnostics")
    def run_final_diagnostics(self) -> None:
        host = self._action_host()
        report = run_diagnostics(
            host.config_path,
            tunnel_profile=(
                host.state.choices.tunnel_profile if host.state.chatgpt_requested else None
            ),
            tunnel_requested=host.state.chatgpt_requested,
        )
        host.query_one("#diagnostic-matrix", Static).update(format_diagnostics(report))
        attestation = (
            "USER-ATTESTED ChatGPT custom app/refresh: complete"
            if host.state.chatgpt_requested and host.state.choices.chatgpt_user_attested
            else "SKIPPED optional ChatGPT UI attestation"
            if not host.state.chatgpt_requested
            else "WAITING FOR USER ChatGPT custom app/refresh"
        )
        host.query_one("#attestation-summary", Static).update(attestation)
        host.query_one("#smoke-guidance", Static).update(
            "Copyable smoke checks:\n"
            f"dev-workspace-mcp setup --check --config {host.config_path}\n"
            f"dev-workspace-mcp serve --config {host.config_path}"
        )
        host.set_step_validation(
            SetupStep.FINAL_VALIDATION,
            valid=report.ready_to_finish,
            message=(
                "All mandatory machine-verifiable diagnostics pass."
                if report.ready_to_finish
                else "Mandatory diagnostics remain unresolved; remediate and rerun."
            ),
        )
