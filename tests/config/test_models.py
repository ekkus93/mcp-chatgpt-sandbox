"""S2: config models cover every §52 block, with §18.9 as the authoritative
field set for `git_policy`."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mcp_chatgpt_sandbox.config.models import (
    RepositoryConfig,
    ServerConfig,
    WorkspaceConfig,
)


def test_minimal_config_parses_with_defaults(minimal_config_dict: dict[str, Any]) -> None:
    config = ServerConfig.model_validate(minimal_config_dict)
    assert config.version == 1
    assert config.repositories == []
    assert config.repository_roots == []
    assert config.workspace.idle_stop_after_seconds == 4 * 3600  # §8.1 default
    assert config.container.user == "host"
    assert config.container.sudo is True
    assert config.container.git_dir_mode == "read-write"  # §18.2 default
    assert config.ssh.mode == "agent"
    assert config.ssh.scope == "repository"


def test_git_policy_defaults_match_spec_18_9(minimal_config_dict: dict[str, Any]) -> None:
    policy = ServerConfig.model_validate(minimal_config_dict).git_policy
    assert policy.push == "allow"
    assert policy.default_branch_push == "allow_fast_forward"
    assert policy.force_push == "deny"
    assert policy.force_with_lease == "deny"
    assert policy.delete_remote_branch == "require_approval"
    assert policy.delete_remote_tag == "deny"
    assert policy.default_branch_commit == "allow"
    assert policy.amend_published == "deny"
    assert policy.pull_strategy == "ff_only"
    assert policy.require_remote_protection == "warn"
    assert policy.remote_protection_checks == ["force_push", "deletion"]
    assert policy.allowed_url_schemes == ["ssh", "https"]


def test_unknown_top_level_field_rejected(minimal_config_dict: dict[str, Any]) -> None:
    minimal_config_dict["not_a_real_field"] = True
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_unknown_git_policy_field_rejected(minimal_config_dict: dict[str, Any]) -> None:
    """§18.9's removed keys (hard_reset, clean_untracked, rebase,
    change_remote_url, submodules) must stay rejected, not silently
    ignored — a silently-ignored key is indistinguishable from a typo."""
    minimal_config_dict["git_policy"] = {"hard_reset": "require_approval"}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


@pytest.mark.parametrize(
    "invalid_key",
    ["hard_reset", "clean_untracked", "rebase", "change_remote_url", "submodules"],
)
def test_each_removed_policy_key_is_rejected(
    minimal_config_dict: dict[str, Any], invalid_key: str
) -> None:
    minimal_config_dict["git_policy"] = {invalid_key: "deny"}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_repository_roots_must_be_absolute(minimal_config_dict: dict[str, Any]) -> None:
    minimal_config_dict["repository_roots"] = ["relative/path"]
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_repository_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        RepositoryConfig.model_validate(
            {"id": "example", "path": "relative/path", "github": "owner/repo"}
        )


@pytest.mark.parametrize(
    "bad_github",
    ["not-a-repo-id", "/leading-slash/repo", "owner/", "/repo", "owner//repo"],
)
def test_repository_github_must_be_owner_slash_repo(bad_github: str) -> None:
    with pytest.raises(ValidationError):
        RepositoryConfig.model_validate(
            {"id": "example", "path": "/abs/path", "github": bad_github}
        )


def test_repository_id_must_be_lowercase_kebab() -> None:
    with pytest.raises(ValidationError):
        RepositoryConfig.model_validate(
            {"id": "Not Valid!", "path": "/abs/path", "github": "owner/repo"}
        )


def test_duplicate_repository_ids_rejected(minimal_config_dict: dict[str, Any]) -> None:
    repo = {"id": "dup", "path": "/abs/one", "github": "owner/one"}
    minimal_config_dict["repositories"] = [
        repo,
        {"id": "dup", "path": "/abs/two", "github": "owner/two"},
    ]
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_h37_authorized_git_dir_roots_combines_both_sources(
    minimal_config_dict: dict[str, Any],
) -> None:
    """H37 (`docs/HARDENING_TODO.md`): before this property existed, every
    caller of `resolve_git_dir`/`FilesystemSecurity` was passed
    `config.repository_roots` alone — a repository authorized only
    through `repositories:` (no `repository_roots:` configured) was
    never in its own `authorized_roots`, and §12.1 rule 3's gitfile-
    target exclusion went inert for it (see `test_mounts.py`'s
    `test_h37_*` tests for the concrete failure this caused)."""
    minimal_config_dict["repository_roots"] = ["/abs/roots/one", "/abs/roots/two"]
    minimal_config_dict["repositories"] = [
        {"id": "example", "path": "/abs/repos/example", "github": "owner/example"},
        {"id": "other", "path": "/abs/repos/other", "github": "owner/other"},
    ]
    config = ServerConfig.model_validate(minimal_config_dict)
    assert config.authorized_git_dir_roots == [
        Path("/abs/roots/one"),
        Path("/abs/roots/two"),
        Path("/abs/repos/example"),
        Path("/abs/repos/other"),
    ]


def test_h37_authorized_git_dir_roots_is_empty_when_neither_source_is_configured(
    minimal_config: ServerConfig,
) -> None:
    assert minimal_config.authorized_git_dir_roots == []


@pytest.mark.parametrize(
    ("raw", "expected_seconds"),
    [
        ("4h", 4 * 3600),
        ("30m", 30 * 60),
        ("1d", 86400),
        ("90s", 90),
        ("never", None),
    ],
)
def test_workspace_idle_stop_after_duration_parsing(raw: str, expected_seconds: int | None) -> None:
    config = WorkspaceConfig.from_yaml_value({"idle_stop_after": raw})
    assert config.idle_stop_after_seconds == expected_seconds


@pytest.mark.parametrize("raw", ["4", "4x", "h4", "", "4.5h", "never!"])
def test_workspace_idle_stop_after_rejects_malformed_duration(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid duration"):
        WorkspaceConfig.from_yaml_value({"idle_stop_after": raw})


def test_container_git_dir_mode_rejects_unknown_value(
    minimal_config_dict: dict[str, Any],
) -> None:
    minimal_config_dict["container"] = {"git_dir_mode": "read-write-and-more"}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_ssh_paths_must_be_absolute(minimal_config_dict: dict[str, Any], tmp_path: Path) -> None:
    minimal_config_dict["ssh"]["keys_dir"] = "relative/keys"
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_github_repositories_validated_as_owner_repo(
    minimal_config_dict: dict[str, Any],
) -> None:
    minimal_config_dict["github"] = {"repositories": ["not-valid"]}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_execution_fields_must_be_positive(minimal_config_dict: dict[str, Any]) -> None:
    minimal_config_dict["execution"] = {"max_output_bytes": 0}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


# --- H30 (`docs/HARDENING_TODO.md`, HR-010): declared config SHALL be
# enforced or removed -----------------------------------------------------------


def test_security_block_is_rejected_as_an_unknown_field(
    minimal_config_dict: dict[str, Any],
) -> None:
    """The `security:` block (`allow_docker_socket`, `allow_privileged_containers`,
    `allow_additional_host_mounts`) was declared but never read anywhere
    — removed rather than wired up, since nothing in this codebase can
    mount the Docker socket, run privileged, or mount an additional host
    path in the first place; there was nothing for it to gate (HR-010:
    "a policy key with no tool to enforce it is not a policy")."""
    minimal_config_dict["security"] = {"allow_docker_socket": True}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)


def test_git_policy_validate_remote_is_rejected_as_an_unknown_field(
    minimal_config_dict: dict[str, Any],
) -> None:
    """`git_policy.validate_remote` was declared but never read — remote
    identity validation (`git/remote.py`) is unconditional, foundational
    security behavior (AT-009, HR-014), not something this daemon offers
    an opt-out from."""
    minimal_config_dict["git_policy"] = {"validate_remote": False}
    with pytest.raises(ValidationError):
        ServerConfig.model_validate(minimal_config_dict)
