"""Public output models for workspace and server tools."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_chatgpt_sandbox.github_api import RemoteProtectionReportOutput


@dataclass
class GitStateOutput:
    branch: str | None
    head_sha: str | None
    clean: bool | None
    upstream: str | None


@dataclass
class WorkspaceInfoOutput:
    workspace_id: str
    repository_id: str
    container_state: str
    workspace_path: str
    git_dir_mode: str
    ssh_key_mode: str
    ssh_key_scope: str
    remote_protection: RemoteProtectionReportOutput
    git: GitStateOutput
    image: str | None = None
    image_digest: str | None = None
    """§9.2's reproducibility record: base image reference, its digest,
    the `.mcp-workspace.yml` `version` that configured it (`None` when no
    project config was present), and the workspace's own creation time —
    `None`/absent for a workspace that predates this task's migration."""
    configured_image: str | None = None
    """G2R-014 (`docs/GAP2_SPEC.md`): what `.mcp-workspace.yml` `image:`
    (or `docker.default_image`) currently resolves to — as opposed to
    `image` above, what the container actually runs. A persistent
    workspace's container is never rebuilt just because this changed
    (`workspace_open` reconciles an already-`RUNNING` container without
    touching its image at all), so the two can legitimately diverge
    until an operator runs `reset`/`rebuild` (`cli.py`); this field is
    what makes that divergence visible instead of silently reconciled
    into a reported `image_digest` that no longer describes the running
    container."""
    config_version: int | None = None
    created_at: str | None = None
    oom_killed: bool = False
    """H32 (`docs/HARDENING_SPEC.md` §9/§26 row): the reporting surface
    OOM detection was missing — `container_state: "failed"` alone does
    not distinguish an OOM kill from any other crash. Reflects the
    *current* container's state (`WorkspaceRecord.oom_killed`'s own
    docstring), not a sticky history flag."""
    devcontainer_ignored: bool = False
    """H32 (`docs/HARDENING_SPEC.md` §9/§30 row): `True` when this
    repository has a `.devcontainer/devcontainer.json` the daemon is
    not reading — see `devcontainer_json_is_ignored`'s own docstring."""


@dataclass
class ServerInfoOutput:
    version: str
    contract_version: str
    docker_available: bool
    docker_version: str | None
    active_workspaces: int
    running_jobs: int
    configured_repositories: int
    capabilities: dict[str, bool]
