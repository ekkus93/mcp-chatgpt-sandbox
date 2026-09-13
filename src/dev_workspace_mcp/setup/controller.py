"""Presentation-independent setup lifecycle and step orchestration."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dev_workspace_mcp.config.models import (
    ContainerConfig,
    DockerConfig,
    Duration,
    ExecutionConfig,
    FilesystemConfig,
    GithubConfig,
    GitPolicyConfig,
    LoggingConfig,
    ResourcesConfig,
    SSHConfig,
    ServerConfig,
    WorkspaceConfig,
)
from dev_workspace_mcp.setup.config_plan import (
    ConfigInspection,
    ConfigPlanError,
    ConfigUpdatePlan,
    inspect_config,
    prepare_config_update,
    read_valid_config,
)
from dev_workspace_mcp.setup.config_writer import (
    ConfigCommitError,
    ConfigCommitResult,
    commit_config_update,
)
from dev_workspace_mcp.setup.diagnostics import SetupDiagnosticsReport, run_setup_diagnostics
from dev_workspace_mcp.setup.filesystem import (
    SetupFilesystemError,
    ensure_secure_directory,
    reject_unsafe_sensitive_target,
)
from dev_workspace_mcp.setup.git_policy import conservative_policy, recommended_policy
from dev_workspace_mcp.setup.model import (
    GitPolicyPreset,
    SetupRepository,
    SetupState,
    SetupStep,
    SetupStepStatus,
)
from dev_workspace_mcp.setup.repository_verification import verify_repository
from dev_workspace_mcp.setup.state import (
    DEFAULT_SETUP_STATE_PATH,
    SetupStateError,
    load_setup_state,
    save_setup_state,
)

DEFAULT_CONFIG_PATH = Path("~/.config/dev-workspace-mcp/config.yml")
DEFAULT_STATE_DB_PATH = Path("~/.local/state/dev-workspace-mcp/state.db")
DEFAULT_AUDIT_PATH = Path("~/.local/state/dev-workspace-mcp/audit.jsonl")
DEFAULT_SSH_KEYS_DIR = Path("~/.config/dev-workspace-mcp/keys")
DEFAULT_KNOWN_HOSTS_PATH = Path("~/.config/dev-workspace-mcp/github_known_hosts")
DEFAULT_DOCKER_IMAGE = "mcp-dev-ubuntu:24.04"
DEFAULT_WORKSPACE_PATH = "/workspace"
DEFAULT_IDLE_STOP = "4h"

SetupEntryChoice = Literal["resume", "repair", "edit", "start_fresh"]


class SetupControllerError(RuntimeError):
    """Safe operator-facing setup lifecycle failure."""


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def _duration_for_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    for suffix, divisor in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % divisor == 0:
            return f"{seconds // divisor}{suffix}"
    return f"{seconds}s"


def _dict_or_none(model: object, existing: object | None) -> dict[str, object] | None:
    if model == existing:
        return None
    if not hasattr(model, "model_dump"):
        raise TypeError("advanced setup value is not a Pydantic model")
    dumped = model.model_dump(mode="json")
    return {str(key): value for key, value in dumped.items()}


@dataclass(slots=True)
class SetupController:
    """One setup session; raw runtime credentials are deliberately process-local."""

    config_path: Path
    state_path: Path
    state: SetupState
    inspection: ConfigInspection
    existing_config: ServerConfig | None
    resumable_state: SetupState | None = None
    state_load_error: str | None = None
    no_browser: bool = False
    repair_invalid_config: bool = False
    runtime_tunnel_key: str | None = field(default=None, repr=False)
    reviewed_plan: ConfigUpdatePlan | None = field(default=None, repr=False)
    last_commit: ConfigCommitResult | None = field(default=None, repr=False)
    diagnostics: SetupDiagnosticsReport | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        config_path: Path | None = None,
        state_path: Path = DEFAULT_SETUP_STATE_PATH,
        resume: bool = False,
        advanced: bool = False,
        no_browser: bool = False,
    ) -> SetupController:
        target = _absolute(config_path or DEFAULT_CONFIG_PATH)
        persisted: SetupState | None = None
        state_error: str | None = None
        try:
            persisted = load_setup_state(state_path)
        except SetupStateError as exc:
            state_error = str(exc)

        try:
            inspection = inspect_config(target)
            existing = read_valid_config(target) if inspection.valid else None
        except ConfigPlanError as exc:
            raise SetupControllerError(str(exc)) from exc

        if resume:
            if state_error is not None:
                raise SetupControllerError(state_error)
            if persisted is None:
                raise SetupControllerError("No resumable setup state exists")
            if persisted.statuses.get(SetupStep.FINAL_VALIDATION) is SetupStepStatus.COMPLETE:
                raise SetupControllerError("Setup state is already complete and cannot be resumed")
            if persisted.config_path is not None and _absolute(Path(persisted.config_path)) != target:
                raise SetupControllerError(
                    "Resumable setup state belongs to a different configuration path"
                )
            state = persisted.clone()
            if advanced:
                state.advanced = True
            state.rewind_for_missing_process_local_secret()
            return cls(
                config_path=target,
                state_path=state_path,
                state=state,
                inspection=inspection,
                existing_config=existing,
                resumable_state=persisted.clone(),
                no_browser=no_browser,
            )

        state = cls._fresh_state(target, existing_config=existing, advanced=advanced)
        resumable = None
        if persisted is not None and (
            persisted.statuses.get(SetupStep.FINAL_VALIDATION) is not SetupStepStatus.COMPLETE
        ):
            resumable = persisted.clone()
        return cls(
            config_path=target,
            state_path=state_path,
            state=state,
            inspection=inspection,
            existing_config=existing,
            resumable_state=resumable,
            state_load_error=state_error,
            no_browser=no_browser,
        )

    @staticmethod
    def _fresh_state(
        config_path: Path,
        *,
        existing_config: ServerConfig | None,
        advanced: bool,
    ) -> SetupState:
        state = SetupState(config_path=str(config_path), advanced=advanced)
        if existing_config is None and not config_path.exists():
            state.state_db_path = str(_absolute(DEFAULT_STATE_DB_PATH))
            state.audit_path = str(_absolute(DEFAULT_AUDIT_PATH))
            state.ssh_keys_dir = str(_absolute(DEFAULT_SSH_KEYS_DIR))
            state.known_hosts_path = str(_absolute(DEFAULT_KNOWN_HOSTS_PATH))
            state.docker_image = DEFAULT_DOCKER_IMAGE
            state.docker_workspace_path = DEFAULT_WORKSPACE_PATH
            state.workspace_idle_stop_after = DEFAULT_IDLE_STOP
            state.ssh_mode = "agent"
            state.ssh_scope = "repository"
            state.github_api_auth = "none"
        return state

    @property
    def has_existing_material(self) -> bool:
        return self.inspection.exists or self.resumable_state is not None or self.state_load_error is not None

    @property
    def resume_available(self) -> bool:
        return self.resumable_state is not None and self.state_load_error is None

    def choose_entry(self, choice: SetupEntryChoice) -> None:
        if choice == "resume":
            if not self.resume_available or self.resumable_state is None:
                raise SetupControllerError("No valid incomplete setup state is available to resume")
            self.state = self.resumable_state.clone()
            self.state.rewind_for_missing_process_local_secret()
            self.repair_invalid_config = False
        elif choice == "repair":
            self.state = self._fresh_state(
                self.config_path, existing_config=self.existing_config, advanced=self.state.advanced
            )
            self.repair_invalid_config = self.inspection.exists and not self.inspection.valid
        elif choice == "edit":
            if not self.inspection.valid or self.existing_config is None:
                raise SetupControllerError("Edit requires an existing production-valid configuration")
            self.state = self._fresh_state(
                self.config_path, existing_config=self.existing_config, advanced=self.state.advanced
            )
            self.repair_invalid_config = False
        elif choice == "start_fresh":
            self.state = self._fresh_state(
                self.config_path, existing_config=self.existing_config, advanced=self.state.advanced
            )
            self.repair_invalid_config = False
        else:
            raise SetupControllerError(f"Unsupported setup entry choice: {choice}")
        self.reviewed_plan = None
        self.diagnostics = None
        self.persist(create_backup=self.state_path.expanduser().exists())

    def persist(self, *, create_backup: bool = False) -> Path:
        try:
            return save_setup_state(self.state, self.state_path, create_backup=create_backup)
        except SetupStateError as exc:
            raise SetupControllerError(str(exc)) from exc

    def _invalidate_review(self) -> None:
        self.reviewed_plan = None
        self.diagnostics = None
        self.state.invalidate_from(self.state.current_step)

    def _complete(self, step: SetupStep) -> None:
        self.state.statuses[step] = SetupStepStatus.COMPLETE
        self.persist()

    def effective_storage(self) -> tuple[str, str, str, str]:
        existing = self.existing_config
        return (
            self.state.state_db_path
            or (str(existing.state_db_path) if existing is not None else str(_absolute(DEFAULT_STATE_DB_PATH))),
            self.state.audit_path
            or (str(existing.audit.path) if existing is not None else str(_absolute(DEFAULT_AUDIT_PATH))),
            self.state.ssh_keys_dir
            or (str(existing.ssh.keys_dir) if existing is not None else str(_absolute(DEFAULT_SSH_KEYS_DIR))),
            self.state.known_hosts_path
            or (
                str(existing.ssh.known_hosts)
                if existing is not None
                else str(_absolute(DEFAULT_KNOWN_HOSTS_PATH))
            ),
        )

    def apply_storage(
        self,
        *,
        state_db_path: str,
        audit_path: str,
        ssh_keys_dir: str,
        known_hosts_path: str,
    ) -> None:
        values = [state_db_path, audit_path, ssh_keys_dir, known_hosts_path]
        paths = [Path(value).expanduser() for value in values]
        if any(not path.is_absolute() for path in paths):
            raise SetupControllerError("All server-storage paths must be absolute")
        try:
            reject_unsafe_sensitive_target(paths[0])
            reject_unsafe_sensitive_target(paths[1])
            reject_unsafe_sensitive_target(paths[3])
        except SetupFilesystemError as exc:
            raise SetupControllerError(str(exc)) from exc
        existing = self.existing_config
        self.state.state_db_path = (
            None if existing is not None and paths[0] == existing.state_db_path else str(paths[0])
        )
        self.state.audit_path = (
            None if existing is not None and paths[1] == existing.audit.path else str(paths[1])
        )
        self.state.ssh_keys_dir = (
            None if existing is not None and paths[2] == existing.ssh.keys_dir else str(paths[2])
        )
        self.state.known_hosts_path = (
            None if existing is not None and paths[3] == existing.ssh.known_hosts else str(paths[3])
        )
        self.state.mark_changed(SetupStep.SERVER_STORAGE)
        self._complete(SetupStep.SERVER_STORAGE)

    def create_storage_directories(self) -> tuple[Path, ...]:
        state_db, audit, keys_dir, known_hosts = self.effective_storage()
        targets = (
            Path(state_db).parent,
            Path(audit).parent,
            Path(keys_dir),
            Path(known_hosts).parent,
            self.config_path.parent,
            self.state_path.expanduser().parent,
        )
        try:
            return tuple(ensure_secure_directory(path) for path in targets)
        except SetupFilesystemError as exc:
            raise SetupControllerError(str(exc)) from exc

    def effective_docker(self) -> tuple[str, str, ContainerConfig, str]:
        existing = self.existing_config
        image = self.state.docker_image or (
            existing.docker.default_image if existing is not None else DEFAULT_DOCKER_IMAGE
        )
        workspace_path = self.state.docker_workspace_path or (
            str(existing.docker.workspace_path) if existing is not None else DEFAULT_WORKSPACE_PATH
        )
        container = (
            ContainerConfig.model_validate(self.state.container_overrides)
            if self.state.container_overrides is not None
            else existing.container
            if existing is not None
            else ContainerConfig()
        )
        idle = self.state.workspace_idle_stop_after or (
            _duration_for_seconds(existing.workspace.idle_stop_after_seconds)
            if existing is not None
            else DEFAULT_IDLE_STOP
        )
        return image, workspace_path, container, idle

    def apply_docker(
        self,
        *,
        image: str,
        workspace_path: str,
        container_user: str,
        sudo: bool,
        git_dir_mode: str,
        idle_stop_after: str,
    ) -> None:
        try:
            docker = DockerConfig(default_image=image, workspace_path=Path(workspace_path))
            container = ContainerConfig(user=container_user, sudo=sudo, git_dir_mode=git_dir_mode)
            workspace = WorkspaceConfig.from_yaml_value({"idle_stop_after": idle_stop_after})
        except (ValueError, TypeError) as exc:
            raise SetupControllerError(f"Invalid Docker workspace settings: {exc}") from exc
        existing = self.existing_config
        self.state.docker_image = (
            None if existing is not None and docker.default_image == existing.docker.default_image else image
        )
        self.state.docker_workspace_path = (
            None
            if existing is not None and docker.workspace_path == existing.docker.workspace_path
            else str(docker.workspace_path)
        )
        self.state.container_overrides = _dict_or_none(
            container, existing.container if existing is not None else None
        )
        self.state.workspace_idle_stop_after = (
            None
            if existing is not None
            and workspace.idle_stop_after_seconds == existing.workspace.idle_stop_after_seconds
            else idle_stop_after
        )
        self.state.mark_changed(SetupStep.DOCKER_WORKSPACE)
        self._complete(SetupStep.DOCKER_WORKSPACE)

    def effective_repositories(self) -> tuple[tuple[SetupRepository, ...], tuple[str, ...]]:
        if self.state.statuses.get(SetupStep.REPOSITORY_ACCESS) is SetupStepStatus.COMPLETE:
            return self.state.repositories, self.state.repository_roots
        if self.existing_config is None:
            return self.state.repositories, self.state.repository_roots
        return (
            tuple(
                SetupRepository(
                    id=repo.id,
                    path=str(repo.path),
                    github=repo.github,
                    ssh_key=repo.ssh_key,
                )
                for repo in self.existing_config.repositories
            ),
            tuple(str(root) for root in self.existing_config.repository_roots),
        )

    def apply_repositories(
        self,
        repositories: tuple[SetupRepository, ...],
        repository_roots: tuple[str, ...],
    ) -> None:
        ids = [repo.id for repo in repositories]
        paths = [Path(repo.path) for repo in repositories]
        if len(ids) != len(set(ids)):
            raise SetupControllerError("Repository IDs must be unique")
        if len(paths) != len(set(paths)):
            raise SetupControllerError("Repository paths must be unique")
        for root in repository_roots:
            root_path = Path(root).expanduser()
            if not root_path.is_absolute():
                raise SetupControllerError("Repository discovery roots must be absolute")
            if not root_path.is_dir():
                raise SetupControllerError(f"Repository discovery root does not exist: {root_path}")
        failures = [verify_repository(repository) for repository in repositories]
        invalid = [result.message for result in failures if not result.valid]
        if invalid:
            raise SetupControllerError("; ".join(invalid))
        self.state.repositories = repositories
        self.state.repository_roots = repository_roots
        self.state.mark_changed(SetupStep.REPOSITORY_ACCESS)
        self._complete(SetupStep.REPOSITORY_ACCESS)

    def effective_ssh_github(self) -> tuple[SSHConfig, GithubConfig]:
        existing = self.existing_config
        state_db, audit, keys_dir, known_hosts = self.effective_storage()
        del state_db, audit
        ssh = SSHConfig(
            mode=self.state.ssh_mode or (existing.ssh.mode if existing is not None else "agent"),
            scope=self.state.ssh_scope or (existing.ssh.scope if existing is not None else "repository"),
            keys_dir=Path(keys_dir),
            known_hosts=Path(known_hosts),
            shared_key=(
                self.state.shared_key
                if self.state.shared_key is not None
                else existing.ssh.shared_key
                if existing is not None
                else None
            ),
        )
        github = GithubConfig(
            api_auth=(
                self.state.github_api_auth
                or (existing.github.api_auth if existing is not None else "none")
            ),
            api_token_path=(
                Path(self.state.github_api_token_path)
                if self.state.github_api_token_path is not None
                else existing.github.api_token_path
                if existing is not None
                else None
            ),
            api_base_url=(existing.github.api_base_url if existing is not None else "https://api.github.com"),
            repositories=(list(existing.github.repositories) if existing is not None else []),
        )
        return ssh, github

    def apply_ssh_github(
        self,
        *,
        ssh_mode: str,
        ssh_scope: str,
        shared_key: str | None,
        github_api_auth: str,
        github_api_token_path: str | None,
    ) -> None:
        _, _, keys_dir, known_hosts = self.effective_storage()
        try:
            ssh = SSHConfig(
                mode=ssh_mode,
                scope=ssh_scope,
                keys_dir=Path(keys_dir),
                known_hosts=Path(known_hosts),
                shared_key=shared_key or None,
            )
            github = GithubConfig(
                api_auth=github_api_auth,
                api_token_path=Path(github_api_token_path) if github_api_token_path else None,
            )
        except (ValueError, TypeError) as exc:
            raise SetupControllerError(f"Invalid SSH/GitHub settings: {exc}") from exc
        repositories, _ = self.effective_repositories()
        missing: list[str] = []
        if ssh.scope == "shared" and ssh.shared_key:
            if not (ssh.keys_dir / ssh.shared_key).is_file():
                missing.append(str(ssh.keys_dir / ssh.shared_key))
        if ssh.scope == "repository":
            for repository in repositories:
                if repository.ssh_key is not None and not (ssh.keys_dir / repository.ssh_key).is_file():
                    missing.append(str(ssh.keys_dir / repository.ssh_key))
        if missing:
            raise SetupControllerError("Referenced SSH key file is missing: " + ", ".join(missing))
        if github.api_auth == "token" and (
            github.api_token_path is None or not github.api_token_path.is_file()
        ):
            raise SetupControllerError(
                "GitHub token mode requires an existing credential file path; token contents are never stored"
            )
        existing = self.existing_config
        self.state.ssh_mode = None if existing is not None and ssh.mode == existing.ssh.mode else ssh.mode
        self.state.ssh_scope = (
            None if existing is not None and ssh.scope == existing.ssh.scope else ssh.scope
        )
        self.state.shared_key = (
            None if existing is not None and ssh.shared_key == existing.ssh.shared_key else ssh.shared_key
        )
        self.state.github_api_auth = (
            None
            if existing is not None and github.api_auth == existing.github.api_auth
            else github.api_auth
        )
        self.state.github_api_token_path = (
            None
            if existing is not None and github.api_token_path == existing.github.api_token_path
            else str(github.api_token_path)
            if github.api_token_path is not None
            else None
        )
        self.state.mark_changed(SetupStep.SSH_AND_GITHUB)
        self._complete(SetupStep.SSH_AND_GITHUB)

    def effective_git_policy(self) -> tuple[GitPolicyPreset, GitPolicyConfig]:
        existing = self.existing_config.git_policy if self.existing_config is not None else GitPolicyConfig()
        if self.state.custom_git_policy is not None:
            return self.state.git_policy_preset, GitPolicyConfig.model_validate(self.state.custom_git_policy)
        if existing == recommended_policy():
            return GitPolicyPreset.RECOMMENDED, existing
        if existing == conservative_policy():
            return GitPolicyPreset.CONSERVATIVE, existing
        return GitPolicyPreset.CUSTOM, existing

    def apply_git_policy(
        self,
        preset: GitPolicyPreset,
        custom: dict[str, object] | None = None,
    ) -> None:
        try:
            if preset is GitPolicyPreset.RECOMMENDED:
                policy = recommended_policy()
            elif preset is GitPolicyPreset.CONSERVATIVE:
                policy = conservative_policy()
            else:
                if custom is None:
                    raise SetupControllerError("Custom Git policy requires every policy field")
                policy = GitPolicyConfig.model_validate(custom)
        except ValueError as exc:
            raise SetupControllerError(f"Invalid Git safety policy: {exc}") from exc
        existing = self.existing_config.git_policy if self.existing_config is not None else GitPolicyConfig()
        self.state.git_policy_preset = preset
        self.state.custom_git_policy = _dict_or_none(policy, existing)
        self.state.mark_changed(SetupStep.GIT_SAFETY_POLICY)
        self._complete(SetupStep.GIT_SAFETY_POLICY)

    def effective_advanced(
        self,
    ) -> tuple[FilesystemConfig, ExecutionConfig, ResourcesConfig, LoggingConfig]:
        existing = self.existing_config
        filesystem = (
            FilesystemConfig.model_validate(self.state.filesystem_overrides)
            if self.state.filesystem_overrides is not None
            else existing.filesystem
            if existing is not None
            else FilesystemConfig()
        )
        execution = (
            ExecutionConfig.model_validate(self.state.execution_overrides)
            if self.state.execution_overrides is not None
            else existing.execution
            if existing is not None
            else ExecutionConfig()
        )
        resources = (
            ResourcesConfig.model_validate(self.state.resources_overrides)
            if self.state.resources_overrides is not None
            else existing.resources
            if existing is not None
            else ResourcesConfig()
        )
        logging = (
            LoggingConfig.model_validate(self.state.logging_overrides)
            if self.state.logging_overrides is not None
            else existing.logging
            if existing is not None
            else LoggingConfig()
        )
        return filesystem, execution, resources, logging

    def apply_advanced(
        self,
        *,
        filesystem: dict[str, object] | None,
        execution: dict[str, object] | None,
        resources: dict[str, object] | None,
        logging: dict[str, object] | None,
    ) -> None:
        try:
            filesystem_model = FilesystemConfig.model_validate(filesystem or {})
            execution_model = ExecutionConfig.model_validate(execution or {})
            resources_model = ResourcesConfig.model_validate(resources or {})
            logging_model = LoggingConfig.model_validate(logging or {})
        except ValueError as exc:
            raise SetupControllerError(f"Invalid advanced runtime settings: {exc}") from exc
        existing = self.existing_config
        self.state.filesystem_overrides = _dict_or_none(
            filesystem_model, existing.filesystem if existing is not None else FilesystemConfig()
        )
        self.state.execution_overrides = _dict_or_none(
            execution_model, existing.execution if existing is not None else ExecutionConfig()
        )
        self.state.resources_overrides = _dict_or_none(
            resources_model, existing.resources if existing is not None else ResourcesConfig()
        )
        self.state.logging_overrides = _dict_or_none(
            logging_model, existing.logging if existing is not None else LoggingConfig()
        )
        self.state.mark_changed(SetupStep.RUNTIME_AND_RESOURCES)
        self._complete(SetupStep.RUNTIME_AND_RESOURCES)

    def skip_advanced(self) -> None:
        self._complete(SetupStep.RUNTIME_AND_RESOURCES)

    def apply_chatgpt(
        self,
        *,
        enabled: bool,
        tunnel_id: str | None = None,
        runtime_key: str | None = None,
        user_attested: bool = False,
    ) -> None:
        if not enabled:
            self.state.chatgpt_enabled = False
            self.state.tunnel_id = None
            self.state.chatgpt_ui_attested = False
            self.state.statuses[SetupStep.CHATGPT_CONNECTION] = SetupStepStatus.SKIPPED_OPTIONAL
            self.runtime_tunnel_key = None
            self.persist()
            return
        if shutil.which("tunnel-client") is None:
            raise SetupControllerError("tunnel-client is not installed or not available on PATH")
        if tunnel_id is None or not tunnel_id.strip():
            raise SetupControllerError("ChatGPT tunnel setup requires a non-secret tunnel ID")
        self.state.chatgpt_enabled = True
        self.state.tunnel_id = tunnel_id.strip()
        self.state.chatgpt_ui_attested = user_attested
        self.runtime_tunnel_key = runtime_key or None
        self.state.mark_changed(SetupStep.CHATGPT_CONNECTION)
        self._complete(SetupStep.CHATGPT_CONNECTION)

    def prepare_review(self) -> ConfigUpdatePlan:
        try:
            plan = prepare_config_update(
                self.config_path,
                self.state,
                repair_invalid=self.repair_invalid_config,
            )
        except ConfigPlanError as exc:
            raise SetupControllerError(str(exc)) from exc
        self.reviewed_plan = plan
        return plan

    def write_reviewed_configuration(self) -> ConfigCommitResult:
        if self.reviewed_plan is None:
            raise SetupControllerError("Configuration has not been reviewed")
        try:
            result = commit_config_update(self.config_path, self.reviewed_plan, self.state)
        except ConfigCommitError as exc:
            self.reviewed_plan = None
            raise SetupControllerError(str(exc)) from exc
        self.last_commit = result
        self.existing_config = result.config
        self.inspection = ConfigInspection(exists=True, valid=True)
        self.state.statuses[SetupStep.CONFIGURATION_REVIEW] = SetupStepStatus.COMPLETE
        self.persist()
        return result

    def run_final_diagnostics(self) -> SetupDiagnosticsReport:
        try:
            report = run_setup_diagnostics(self.config_path)
        except Exception as exc:
            self.state.statuses[SetupStep.FINAL_VALIDATION] = SetupStepStatus.FAILED
            self.persist()
            raise SetupControllerError("Unexpected setup diagnostic failure") from exc
        self.diagnostics = report
        self.state.statuses[SetupStep.FINAL_VALIDATION] = (
            SetupStepStatus.COMPLETE if report.valid else SetupStepStatus.ACTION_REQUIRED
        )
        self.persist()
        return report

    def mark_local_prerequisites(self) -> tuple[str, ...]:
        messages: list[str] = []
        if sys.version_info < (3, 12):
            raise SetupControllerError("Python 3.12 or newer is required")
        messages.append(f"Python {sys.version_info.major}.{sys.version_info.minor}: supported")
        if shutil.which("uv") is None:
            messages.append("uv: not detected on PATH (installed-package use may still be valid)")
        else:
            messages.append("uv: available")
        self._complete(SetupStep.LOCAL_PREREQUISITES)
        return tuple(messages)

    def can_advance(self) -> bool:
        return self.state.statuses.get(self.state.current_step) in {
            SetupStepStatus.COMPLETE,
            SetupStepStatus.SKIPPED_OPTIONAL,
        }

    def advance(self) -> SetupStep:
        if not self.can_advance():
            raise SetupControllerError("Current setup step is not complete")
        steps = list(SetupStep)
        index = steps.index(self.state.current_step)
        if index >= len(steps) - 1:
            return self.state.current_step
        self.state.current_step = steps[index + 1]
        self.persist()
        return self.state.current_step

    def back(self) -> SetupStep:
        steps = list(SetupStep)
        index = steps.index(self.state.current_step)
        if index == 0:
            return self.state.current_step
        self.state.current_step = steps[index - 1]
        self.persist()
        return self.state.current_step


__all__ = [
    "DEFAULT_AUDIT_PATH",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DOCKER_IMAGE",
    "DEFAULT_IDLE_STOP",
    "DEFAULT_KNOWN_HOSTS_PATH",
    "DEFAULT_SSH_KEYS_DIR",
    "DEFAULT_STATE_DB_PATH",
    "DEFAULT_WORKSPACE_PATH",
    "SetupController",
    "SetupControllerError",
    "SetupEntryChoice",
]
