"""Translate persisted non-secret setup choices into production config fragments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_workspace_mcp.config import ServerConfig, load_config
from dev_workspace_mcp.setup.choices import SetupChoices
from dev_workspace_mcp.setup.docker_setup import DockerSetupChoices
from dev_workspace_mcp.setup.git_policy_setup import (
    GitPolicyPreset,
    conservative_policy,
    materialize_policy,
    policy_to_yaml_mapping,
    recommended_policy,
)
from dev_workspace_mcp.setup.model import (
    SetupRepositoryState,
    SetupState,
    SetupStep,
    SetupStepStatus,
)
from dev_workspace_mcp.setup.runtime_setup import RuntimeChoices
from dev_workspace_mcp.setup.ssh_github_setup import CredentialChoices


@dataclass(frozen=True, slots=True)
class MaterializedSetup:
    values: dict[str, object]
    managed_fields: frozenset[str]


def _duration_string(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    for unit, divisor in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % divisor == 0:
            return f"{seconds // divisor}{unit}"
    return f"{seconds}s"


def choices_from_config(config: ServerConfig) -> SetupChoices:
    """Prepopulate Edit/Repair from authoritative production values."""

    if config.git_policy == recommended_policy():
        preset = GitPolicyPreset.RECOMMENDED.value
        custom: dict[str, object] | None = None
    elif config.git_policy == conservative_policy():
        preset = GitPolicyPreset.CONSERVATIVE.value
        custom = None
    else:
        preset = GitPolicyPreset.CUSTOM.value
        custom = config.git_policy.model_dump(mode="json")
    return SetupChoices(
        state_db_path=str(config.state_db_path),
        audit_path=str(config.audit.path),
        keys_dir=str(config.ssh.keys_dir),
        known_hosts=str(config.ssh.known_hosts),
        docker_image=config.docker.default_image,
        workspace_path=str(config.docker.workspace_path),
        container_user=config.container.user,
        git_dir_mode=config.container.git_dir_mode,
        idle_stop_after=_duration_string(config.workspace.idle_stop_after_seconds),
        repository_roots=tuple(str(root) for root in config.repository_roots),
        ssh_mode=config.ssh.mode,
        ssh_scope=config.ssh.scope,
        shared_key=config.ssh.shared_key,
        github_api_auth=config.github.api_auth,
        github_auth_file=(
            str(config.github.api_token_path) if config.github.api_token_path is not None else None
        ),
        github_api_base_url=config.github.api_base_url,
        git_policy_preset=preset,
        git_policy_custom=custom,
        runtime_overrides={
            "filesystem": config.filesystem.model_dump(mode="json"),
            "execution": config.execution.model_dump(mode="json"),
            "resources": config.resources.model_dump(mode="json"),
            "logging": config.logging.model_dump(mode="json"),
        },
    )


def state_from_existing_config(config_path: Path, *, advanced: bool) -> SetupState:
    config = load_config(config_path)
    return SetupState(
        current_step=SetupStep.STORAGE,
        statuses={SetupStep.PREREQUISITES: SetupStepStatus.COMPLETE},
        repositories=tuple(
            SetupRepositoryState.from_dict(
                {
                    "id": repository.id,
                    "path": str(repository.path),
                    "github": repository.github,
                    "ssh_key": repository.ssh_key,
                }
            )
            for repository in config.repositories
        ),
        advanced=advanced,
        config_path=str(config_path.expanduser()),
        choices=choices_from_config(config),
    )


def fresh_choices(config_path: Path) -> SetupChoices:
    """Offer explicit operator defaults without adding production-model defaults."""

    home = Path.home()
    state_dir = home / ".local" / "state" / "dev-workspace-mcp"
    config_dir = config_path.expanduser().parent
    return SetupChoices(
        state_db_path=str(state_dir / "state.db"),
        audit_path=str(state_dir / "audit.jsonl"),
        keys_dir=str(config_dir / "keys"),
        known_hosts=str(config_dir / "known_hosts"),
    )


def materialize_setup(state: SetupState, *, new_config: bool) -> MaterializedSetup:
    """Materialize only fields explicitly reviewed by the operator."""

    reviewed = set(state.reviewed_fields)
    values: dict[str, object] = {}
    choices = state.choices

    if new_config:
        reviewed.add("version")
        values["version"] = 1

    if "state_db_path" in reviewed:
        if choices.state_db_path is None:
            raise ValueError("state_db_path has not been selected")
        values["state_db_path"] = choices.state_db_path
    if "audit" in reviewed:
        if choices.audit_path is None:
            raise ValueError("audit path has not been selected")
        values["audit"] = {"path": choices.audit_path}

    docker_fields = {"docker", "workspace", "container"} & reviewed
    if docker_fields:
        fragments = DockerSetupChoices(
            default_image=choices.docker_image,
            workspace_path=choices.workspace_path,
            container_user=choices.container_user,
            git_dir_mode=choices.git_dir_mode,
            idle_stop_after=choices.idle_stop_after,
        ).config_fragments()
        for field_name in docker_fields:
            values[field_name] = fragments[field_name]

    if "repository_roots" in reviewed:
        values["repository_roots"] = list(choices.repository_roots)
    if "repositories" in reviewed:
        values["repositories"] = [repository.to_dict() for repository in state.repositories]

    credential_fields = {"ssh", "github"} & reviewed
    if credential_fields:
        if choices.keys_dir is None or choices.known_hosts is None:
            raise ValueError("SSH host paths have not been selected")
        fragments = CredentialChoices(
            mode=choices.ssh_mode,  # type: ignore[arg-type]
            scope=choices.ssh_scope,  # type: ignore[arg-type]
            keys_dir=Path(choices.keys_dir),
            known_hosts=Path(choices.known_hosts),
            shared_key=choices.shared_key,
            github_api_auth=choices.github_api_auth,  # type: ignore[arg-type]
            github_api_token_path=(
                Path(choices.github_auth_file) if choices.github_auth_file is not None else None
            ),
            github_api_base_url=choices.github_api_base_url,
        ).config_fragments()
        for field_name in credential_fields:
            values[field_name] = fragments[field_name]

    if "git_policy" in reviewed:
        policy = materialize_policy(
            GitPolicyPreset(choices.git_policy_preset),
            custom=choices.git_policy_custom,
        )
        values["git_policy"] = policy_to_yaml_mapping(policy)

    runtime_fields = {"filesystem", "execution", "resources", "logging"} & reviewed
    if runtime_fields:
        overrides = choices.runtime_overrides or {}
        fragments = RuntimeChoices(
            filesystem=(
                overrides.get("filesystem")
                if isinstance(overrides.get("filesystem"), dict)
                else None
            ),
            execution=(
                overrides.get("execution") if isinstance(overrides.get("execution"), dict) else None
            ),
            resources=(
                overrides.get("resources") if isinstance(overrides.get("resources"), dict) else None
            ),
            logging=(
                overrides.get("logging") if isinstance(overrides.get("logging"), dict) else None
            ),
        ).reviewed_fragments()
        for field_name in runtime_fields:
            if field_name not in fragments:
                raise ValueError(f"reviewed runtime field {field_name!r} has no value")
            values[field_name] = fragments[field_name]

    missing = reviewed - values.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"reviewed setup fields have no materialized values: {names}")
    return MaterializedSetup(values, frozenset(reviewed))
