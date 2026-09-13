"""Explicit non-secret operator choices persisted by the setup wizard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self


class SetupChoicesError(ValueError):
    """A persisted setup choice has an unsupported type or value."""


def _optional_string(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SetupChoicesError(f"{name} must be a string or null")
    return value


def _string(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise SetupChoicesError(f"{name} must be a string")
    return value


def _safe_mapping(value: object, *, name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SetupChoicesError(f"{name} must be an object with string keys")
    return dict(value)


@dataclass(slots=True)
class SetupChoices:
    """Only non-secret values safe to resume across processes.

    Raw API/runtime credentials are deliberately absent. ``github_auth_file``
    is a filesystem path reference, not credential content. Tunnel runtime
    material is process-local or supplied by the operator's external secure
    environment and therefore has no field here.
    """

    state_db_path: str | None = None
    audit_path: str | None = None
    keys_dir: str | None = None
    known_hosts: str | None = None

    docker_image: str = "mcp-dev-ubuntu:24.04"
    workspace_path: str = "/workspace"
    container_user: str = "host"
    git_dir_mode: str = "read-write"
    idle_stop_after: str = "4h"

    repository_roots: tuple[str, ...] = ()

    ssh_mode: str = "agent"
    ssh_scope: str = "repository"
    shared_key: str | None = None
    github_api_auth: str = "none"
    github_auth_file: str | None = None
    github_api_base_url: str = "https://api.github.com"

    git_policy_preset: str = "recommended"
    git_policy_custom: dict[str, object] | None = None
    runtime_overrides: dict[str, object] | None = None

    tunnel_id: str | None = None
    tunnel_profile: str = "chatgpt-sandbox"
    chatgpt_user_attested: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "state_db_path": self.state_db_path,
            "audit_path": self.audit_path,
            "keys_dir": self.keys_dir,
            "known_hosts": self.known_hosts,
            "docker_image": self.docker_image,
            "workspace_path": self.workspace_path,
            "container_user": self.container_user,
            "git_dir_mode": self.git_dir_mode,
            "idle_stop_after": self.idle_stop_after,
            "repository_roots": list(self.repository_roots),
            "ssh_mode": self.ssh_mode,
            "ssh_scope": self.ssh_scope,
            "shared_key": self.shared_key,
            "github_api_auth": self.github_api_auth,
            "github_auth_file": self.github_auth_file,
            "github_api_base_url": self.github_api_base_url,
            "git_policy_preset": self.git_policy_preset,
            "git_policy_custom": self.git_policy_custom,
            "runtime_overrides": self.runtime_overrides,
            "tunnel_id": self.tunnel_id,
            "tunnel_profile": self.tunnel_profile,
            "chatgpt_user_attested": self.chatgpt_user_attested,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise SetupChoicesError("setup choices must be an object")
        allowed = set(cls().to_dict())
        unknown = set(value) - allowed
        if unknown:
            raise SetupChoicesError("setup choices contain unknown fields")
        roots = value.get("repository_roots", [])
        if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
            raise SetupChoicesError("repository_roots must be a list of strings")
        attested = value.get("chatgpt_user_attested", False)
        if not isinstance(attested, bool):
            raise SetupChoicesError("chatgpt_user_attested must be a boolean")
        result = cls(
            state_db_path=_optional_string(value.get("state_db_path"), name="state_db_path"),
            audit_path=_optional_string(value.get("audit_path"), name="audit_path"),
            keys_dir=_optional_string(value.get("keys_dir"), name="keys_dir"),
            known_hosts=_optional_string(value.get("known_hosts"), name="known_hosts"),
            docker_image=_string(value.get("docker_image", cls.docker_image), name="docker_image"),
            workspace_path=_string(
                value.get("workspace_path", cls.workspace_path), name="workspace_path"
            ),
            container_user=_string(
                value.get("container_user", cls.container_user), name="container_user"
            ),
            git_dir_mode=_string(
                value.get("git_dir_mode", cls.git_dir_mode), name="git_dir_mode"
            ),
            idle_stop_after=_string(
                value.get("idle_stop_after", cls.idle_stop_after), name="idle_stop_after"
            ),
            repository_roots=tuple(roots),
            ssh_mode=_string(value.get("ssh_mode", cls.ssh_mode), name="ssh_mode"),
            ssh_scope=_string(value.get("ssh_scope", cls.ssh_scope), name="ssh_scope"),
            shared_key=_optional_string(value.get("shared_key"), name="shared_key"),
            github_api_auth=_string(
                value.get("github_api_auth", cls.github_api_auth), name="github_api_auth"
            ),
            github_auth_file=_optional_string(
                value.get("github_auth_file"), name="github_auth_file"
            ),
            github_api_base_url=_string(
                value.get("github_api_base_url", cls.github_api_base_url),
                name="github_api_base_url",
            ),
            git_policy_preset=_string(
                value.get("git_policy_preset", cls.git_policy_preset), name="git_policy_preset"
            ),
            git_policy_custom=_safe_mapping(
                value.get("git_policy_custom"), name="git_policy_custom"
            ),
            runtime_overrides=_safe_mapping(
                value.get("runtime_overrides"), name="runtime_overrides"
            ),
            tunnel_id=_optional_string(value.get("tunnel_id"), name="tunnel_id"),
            tunnel_profile=_string(
                value.get("tunnel_profile", cls.tunnel_profile), name="tunnel_profile"
            ),
            chatgpt_user_attested=attested,
        )
        if result.container_user not in {"host", "root"}:
            raise SetupChoicesError("container_user is unsupported")
        if result.git_dir_mode not in {"read-write", "read-only", "none"}:
            raise SetupChoicesError("git_dir_mode is unsupported")
        if result.ssh_mode not in {"agent", "file"}:
            raise SetupChoicesError("ssh_mode is unsupported")
        if result.ssh_scope not in {"repository", "shared"}:
            raise SetupChoicesError("ssh_scope is unsupported")
        if result.github_api_auth not in {"none", "token", "app"}:
            raise SetupChoicesError("github_api_auth is unsupported")
        if result.git_policy_preset not in {"recommended", "conservative", "custom"}:
            raise SetupChoicesError("git_policy_preset is unsupported")
        return result

    def clone(self) -> Self:
        return type(self).from_dict(self.to_dict())
