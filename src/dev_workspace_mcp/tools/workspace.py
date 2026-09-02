"""`workspace_open`, `workspace_info`, `server_info` (§8.3, §25, §33.3,
§34.1, §62).

`workspace_open` is the point where S5 (repository registry), S6 (Docker
lifecycle), S7 (container identity), and S8 (mounts) actually come
together into a running container a client can use.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import docker
from mcp.server import MCPServer

from dev_workspace_mcp import __version__
from dev_workspace_mcp.audit import write_audit_event
from dev_workspace_mcp.config.models import (
    AuditConfig,
    ContainerConfig,
    DockerConfig,
    ExecutionConfig,
    GithubConfig,
    GitPolicyConfig,
    ResourcesConfig,
    SSHConfig,
    WorkspaceConfig,
)
from dev_workspace_mcp.errors import (
    CommandFailedError,
    ContainerRuntimeFailedError,
    ImageNotFoundError,
    RemoteProtectionMissingError,
    RepositoryNotAllowedError,
    translate_workspace_errors,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.git.commit import detect_default_branch
from dev_workspace_mcp.git_state import NO_GIT_STATE, read_basic_git_state
from dev_workspace_mcp.github_api import (
    RemoteProtectionReportOutput,
    RemoteProtectionResult,
    deserialize_remote_protection,
    serialize_remote_protection,
    to_report_output,
    verify_remote_protection,
)
from dev_workspace_mcp.jobs.store import JobState, JobStore
from dev_workspace_mcp.project_config import (
    ProjectWorkspaceConfig,
    devcontainer_json_is_ignored,
    load_project_config,
)
from dev_workspace_mcp.repositories import RepositoryRegistry
from dev_workspace_mcp.schema_export import CONTRACT_VERSION
from dev_workspace_mcp.tools.execution import kill_after_timeout
from dev_workspace_mcp.workspace.caches import build_cache_mounts, resolve_caches
from dev_workspace_mcp.workspace.container_config import (
    build_workspace_run_kwargs,
    resolve_exec_user,
)
from dev_workspace_mcp.workspace.manager import (
    DockerWorkspaceManager,
    WorkspaceRecord,
    WorkspaceState,
    get_authorized,
)
from dev_workspace_mcp.workspace.mounts import (
    build_known_hosts_mount,
    build_ssh_agent_mount,
    build_workspace_mounts,
)
from dev_workspace_mcp.workspace.naming import WorkspaceMode, persistent_workspace_id
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager, resolve_ssh_key_path

DEFAULT_WORKSPACE_PATH = "/workspace"

#: `.mcp-workspace.yml`'s `setup` steps (§10) run once, on genuine
#: container creation — package installs and toolchain bootstraps
#: legitimately take longer than an ordinary `exec` call's default
#: `execution.sync_timeout_seconds` (30s), so this gets its own, more
#: generous budget rather than a new config knob for a single call site.
_SETUP_STEP_TIMEOUT_SECONDS = 600.0
#: G2R-016 (`docs/GAP2_SPEC.md`): a real cross-import of
#: `tools/execution.py`'s `KILL_GRACE_PERIOD_SECONDS` now, not a second,
#: separately hand-kept constant — the previous comment here claimed the
#: two tool modules were "otherwise independent of each other" as the
#: reason to duplicate rather than import, but that was the exact
#: condition that let this one grace period and `run_exec`'s kill-failure
#: handling (missing entirely from the setup-step copy) drift apart.


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


async def run_setup_steps(
    execution_service: ExecutionService,
    record: WorkspaceRecord,
    steps: list[str],
    *,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
) -> None:
    """§10: run `.mcp-workspace.yml`'s `setup` steps against `record`'s
    container, in order, failing closed on the first one that fails.
    Module-level (not a `register()` closure) so both `open_workspace`
    below (R27, `docs/REMEDIATION_TODO.md` — itself module-level for the
    same reason) and `cli.py`'s `reset`/`rebuild` commands (H32,
    `docs/HARDENING_SPEC.md` §9's §8.4/§10 rows — a genuinely fresh
    container from either command had never had these steps run against
    it at all, until the *next* `workspace_open`, which `was_created`'s
    own "did a DB row already exist" check could never actually trigger
    for a *reused* workspace id, leaving it permanently, silently
    half-provisioned) can call the same logic.

    Each step is a shell command, run the same way `exec`'s own `shell`
    form is (`["bash", "-c", step]`) — `sudo` works exactly as it does
    from `exec`, since this is the same execution service, the same
    container user, no second path (§20.1's rationale)."""
    assert record.container_id is not None  # implied by state == RUNNING
    container_id = record.container_id
    user = resolve_exec_user(container_config)
    for step in steps:
        # HR-022 (`docs/HARDENING_SPEC.md` §10): `docker.errors.DockerException`
        # from either call used to escape uncaught, past
        # `@translate_workspace_errors`, reaching the client as an
        # unstructured crash instead of a clean workspace_open failure.
        try:
            exec_id = await asyncio.to_thread(
                execution_service.start,
                container_id,
                ["bash", "-c", step],
                cwd=record.workspace_path,
                user=user,
                env=None,
            )
        except docker.errors.DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed to start .mcp-workspace.yml setup step: {step!r}: {exc}",
                details={"workspace_id": record.id, "step": step},
            ) from exc
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    execution_service.consume,
                    exec_id,
                    max_output_bytes=execution_config.max_output_bytes,
                    response_tail_lines=execution_config.response_tail_lines,
                ),
                timeout=_SETUP_STEP_TIMEOUT_SECONDS,
            )
        except docker.errors.DockerException as exc:
            raise ContainerRuntimeFailedError(
                f"failed while running .mcp-workspace.yml setup step: {step!r}: {exc}",
                details={"workspace_id": record.id, "step": step},
            ) from exc
        except TimeoutError:
            # G2R-016 (`docs/GAP2_SPEC.md`): `kill_after_timeout` is what
            # handles a failed kill (`PermissionError`/`DockerException`,
            # `tools/execution.py`'s own docstring) as a structured error
            # rather than letting it escape past
            # `@translate_workspace_errors` as an unstructured crash —
            # exactly the gap `run_exec` already closed and this copy
            # had not.
            await kill_after_timeout(
                execution_service,
                exec_id,
                workspace_id=record.id,
                timeout_seconds=_SETUP_STEP_TIMEOUT_SECONDS,
            )
            raise CommandFailedError(
                f".mcp-workspace.yml setup step exceeded {_SETUP_STEP_TIMEOUT_SECONDS}s: {step!r}",
                details={"workspace_id": record.id, "step": step},
            ) from None
        if result.exit_code != 0:
            raise CommandFailedError(
                f".mcp-workspace.yml setup step failed (exit {result.exit_code}): "
                f"{step!r}: {result.stderr_tail}",
                details={
                    "workspace_id": record.id,
                    "step": step,
                    "exit_code": result.exit_code,
                },
            )


def load_stored_remote_protection(
    db: sqlite3.Connection, repository_id: str
) -> RemoteProtectionResult | None:
    row = db.execute(
        "SELECT remote_protection FROM repository WHERE id = ?", (repository_id,)
    ).fetchone()
    return deserialize_remote_protection(row["remote_protection"]) if row is not None else None


def store_remote_protection(
    db: sqlite3.Connection, repository_id: str, result: RemoteProtectionResult
) -> None:
    with db:
        db.execute(
            "UPDATE repository SET remote_protection = ?, default_branch = ? WHERE id = ?",
            (serialize_remote_protection(result), result.default_branch, repository_id),
        )


def load_image_provenance(
    db: sqlite3.Connection, workspace_id: str
) -> tuple[str | None, int | None]:
    row = db.execute(
        "SELECT image_digest, config_version FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone()
    if row is None:
        return None, None
    return row["image_digest"], row["config_version"]


def resolve_effective_image(
    project_config: ProjectWorkspaceConfig | None, docker_config: DockerConfig
) -> str:
    """§9/§10: `.mcp-workspace.yml`'s `image:`, when present, is the
    highest `image` precedence level this daemon implements
    (`workspace_path`'s own sibling gaps — devcontainer.json, per-project
    Dockerfile detection — are a documented, deliberate scope cut, not
    silent approximation: see docs/TODO.md) — `docker.default_image`
    otherwise. Factored out of `open_workspace` (G2R-014,
    `docs/GAP2_SPEC.md`) so `build_workspace_info` can compute the exact
    same value `workspace_open` just used to provision the container, to
    report a config-vs-running-container mismatch rather than only ever
    comparing against whatever the container happened to start with."""
    return (
        project_config.image
        if project_config is not None and project_config.image is not None
        else docker_config.default_image
    )


def load_setup_completed_container_id(db: sqlite3.Connection, workspace_id: str) -> str | None:
    """G2R-012 (`docs/GAP2_SPEC.md`): the container id `.mcp-workspace.yml`
    `setup` steps last completed successfully against, for `workspace_id`
    — `None` if they never have. `open_workspace` runs `setup` again
    whenever this doesn't match the *current* `container_id`, which
    covers both ways the old "was this DB row just created?" check
    missed: a brand-new row's container was never recorded here, and a
    container recreated behind an existing row (Docker assigns the
    replacement a new id) no longer matches whatever id was recorded for
    the one it replaced — one comparison, not two special cases."""
    row = db.execute(
        "SELECT setup_completed_container_id FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone()
    return None if row is None else row["setup_completed_container_id"]


def _runtime_image_digest(
    docker_client: docker.DockerClient, record: WorkspaceRecord
) -> str | None:
    """Return the immutable image ID from the container's own inspect data.

    G3R-006 / G3AT-006 (`docs/GAP3_SPEC.md`): a tag/reference is configuration, not
    runtime provenance. A mutable tag can be retargeted while an existing
    container continues to run the image it was created from, so resolving
    `record.image` through `images.get()` can silently report the wrong
    digest. Docker stores the immutable image ID directly in the container
    inspect payload (`Image`); that is the authoritative source.

    There is deliberately no tag-lookup fallback here. If a persisted row
    claims a container exists but Docker cannot return trustworthy inspect
    metadata, manufacturing a digest from a mutable name would convert
    unknown provenance into a false success.
    """
    if record.container_id is None:
        return None
    try:
        container = docker_client.containers.get(record.container_id)
        container.reload()
    except docker.errors.DockerException as exc:
        raise ContainerRuntimeFailedError(
            f"failed to inspect runtime image for workspace {record.id!r}: {exc}",
            details={"workspace_id": record.id, "container_id": record.container_id},
        ) from exc
    image_id = container.attrs.get("Image")
    if not isinstance(image_id, str) or not image_id:
        raise ContainerRuntimeFailedError(
            f"container {record.container_id!r} for workspace {record.id!r} "
            "did not report an immutable image ID",
            details={"workspace_id": record.id, "container_id": record.container_id},
            recoverable=False,
        )
    return image_id


async def build_workspace_info(
    record: WorkspaceRecord,
    *,
    registry: RepositoryRegistry,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    ssh_config: SSHConfig,
    db: sqlite3.Connection,
    execution_service: ExecutionService,
    docker_client: docker.DockerClient,
    docker_config: DockerConfig,
) -> WorkspaceInfoOutput:
    """H36 (`docs/HARDENING_TODO.md`, 5/8): the `git` block is only
    populated when `record` is currently `RUNNING` — `NO_GIT_STATE`
    (every field `None`) otherwise. Unlike `git_status`/`git_diff`/
    `git_log`, `workspace_info` itself does not raise
    `WorkspaceNotRunningError` for a stopped or absent workspace: it is
    the tool that reports container state, including "not running", so
    it has to keep succeeding for exactly the workspaces where that is
    the answer — only the git block it can no longer read anything from
    goes empty. Both callers (`open_workspace`, `workspace_info_tool`)
    already require Docker to be reachable at all before calling this;
    `execution_service` is `ExecutionService`, not `Optional`, on that
    basis."""
    repository = registry.get(record.repository_id)
    if record.state == WorkspaceState.RUNNING:
        assert record.container_id is not None  # implied by state == RUNNING
        git_state = await read_basic_git_state(
            container_id=record.container_id,
            workspace_path=record.workspace_path,
            execution_service=execution_service,
            container_config=container_config,
            execution_config=execution_config,
        )
    else:
        git_state = NO_GIT_STATE
    stored_image_digest, config_version = load_image_provenance(db, record.id)
    runtime_image_digest = _runtime_image_digest(docker_client, record)
    image_digest = runtime_image_digest if runtime_image_digest is not None else stored_image_digest
    # G2R-014 (`docs/GAP2_SPEC.md`): `resolve_effective_image` is the
    # exact same precedence `open_workspace` used to provision the
    # container with — recomputed here (a project config load is cheap)
    # so a configured image changed since the container was last built
    # is *reported*, not silently reconciled: `image` (below) is what
    # actually runs, `configured_image` is what `.mcp-workspace.yml`/
    # `docker.default_image` currently asks for. They diverge exactly
    # when a rebuild is pending — `reset`/`rebuild` (`cli.py`) is what
    # picks up the difference.
    project_config = load_project_config(repository.canonical_path)
    configured_image = resolve_effective_image(project_config, docker_config)
    return WorkspaceInfoOutput(
        workspace_id=record.id,
        repository_id=record.repository_id,
        container_state=record.state.value,
        workspace_path=record.workspace_path,
        git_dir_mode=container_config.git_dir_mode,
        ssh_key_mode=ssh_config.mode,
        ssh_key_scope=ssh_config.scope,
        remote_protection=to_report_output(load_stored_remote_protection(db, record.repository_id)),
        git=GitStateOutput(
            branch=git_state.branch,
            head_sha=git_state.head_sha,
            clean=git_state.clean,
            upstream=git_state.upstream,
        ),
        image=record.image,
        image_digest=image_digest,
        configured_image=configured_image,
        config_version=config_version,
        created_at=record.created_at,
        oom_killed=record.oom_killed,
        devcontainer_ignored=devcontainer_json_is_ignored(repository.canonical_path),
    )


async def open_workspace(
    repository_id: str,
    mode: WorkspaceMode,
    *,
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    execution_service: ExecutionService,
    registry: RepositoryRegistry,
    db: sqlite3.Connection,
    job_store: JobStore,
    docker_config: DockerConfig,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    resources_config: ResourcesConfig,
    ssh_config: SSHConfig,
    workspace_config: WorkspaceConfig,
    github_config: GithubConfig,
    git_policy_config: GitPolicyConfig,
    audit_config: AuditConfig,
    repository_roots: list[Path],
    ssh_agent_manager: SSHAgentManager | None,
) -> WorkspaceInfoOutput:
    """§8.3: the `workspace_open` tool's full body — module-level, not a
    `register()` closure (R27, `docs/REMEDIATION_TODO.md`), so it can be
    unit-tested by constructing its dependencies directly rather than a
    whole running server, the same reason `run_setup_steps` above already
    lives at module level. `register()`'s `workspace_open` tool is a thin
    wrapper: resolve `docker_client`/`manager`/`execution_service` past
    `_require_docker()`'s degraded-state check, then call this."""
    try:
        repository = registry.get(repository_id)
    except RepositoryNotAllowedError:
        # §24's "repository authorization failure" event: audited here
        # rather than inside `RepositoryRegistry` itself, which has no
        # `AuditConfig` of its own — every caller of `.get()` that can
        # reach an actual client request goes through this tool.
        write_audit_event(
            audit_config,
            operation="repository_authorization_failure",
            result="denied",
            metadata={"repository_id": repository_id},
        )
        raise

    # G3R-003 (`docs/GAP3_SPEC.md`): verification stays three-state
    # (`verified`/`missing`/`unverified`), but enforcement must not turn
    # "could not verify" into success when the operator selected `deny`.
    # This check runs before project configuration, SSH-agent key loading,
    # or Docker work so a denied workspace never gains a credential or a
    # container as a side effect of the failed open.
    default_branch = detect_default_branch(repository.canonical_path)
    remote_protection = verify_remote_protection(
        github_config=github_config,
        owner_repo=repository.github,
        default_branch=default_branch,
        checks=git_policy_config.remote_protection_checks,
    )
    if (
        git_policy_config.require_remote_protection == "deny"
        and remote_protection.status != "verified"
    ):
        write_audit_event(
            audit_config,
            operation="workspace_opened",
            result="denied",
            repository_id=repository_id,
            metadata={
                "policy_decision": "deny",
                "policy": "require_remote_protection",
                "remote_protection_status": remote_protection.status,
                "remote_protection_reason": remote_protection.reason,
            },
        )
        raise RemoteProtectionMissingError(
            f"required remote protection is not verified for default branch "
            f"{remote_protection.default_branch!r} (§18.5/G3R-003): "
            f"status={remote_protection.status!r}; {remote_protection.reason}",
            details={
                "repository_id": repository_id,
                "default_branch": remote_protection.default_branch,
                "remote_protection_status": remote_protection.status,
                "reason": remote_protection.reason,
            },
        )

    # §9/§10: `.mcp-workspace.yml`, when present, is the highest
    # `image` precedence level this daemon implements (`docker.
    # workspace_path`'s own sibling gaps — devcontainer.json,
    # per-project Dockerfile detection — are a documented, deliberate
    # scope cut, not silent approximation: see docs/TODO.md).
    project_config = load_project_config(repository.canonical_path)
    effective_image = resolve_effective_image(project_config, docker_config)
    resolved_caches = resolve_caches(
        repository_id=repository.id,
        project_caches=project_config.caches if project_config is not None else [],
    )
    extra_env = dict(project_config.environment) if project_config is not None else {}

    run_kwargs = build_workspace_run_kwargs(
        container_config=container_config,
        docker_client=docker_client,
        ssh_config=ssh_config,
        workspace_path=DEFAULT_WORKSPACE_PATH,
        extra_env=extra_env,
        resources_config=resources_config,
        cache_paths=[c.home_relative_path for c in resolved_caches],
    )
    mounts = build_workspace_mounts(
        host_repository_path=repository.canonical_path,
        workspace_path=DEFAULT_WORKSPACE_PATH,
        git_dir_mode=container_config.git_dir_mode,
        authorized_roots=repository_roots,
    )
    mounts.append(build_known_hosts_mount(ssh_config.known_hosts))
    mounts.extend(build_cache_mounts(resolved_caches))
    workspace_key_path: Path | None = None
    if ssh_agent_manager is not None and mode == "persistent":
        # G3R-005 (`docs/GAP3_SPEC.md`): the container needs the agent
        # socket mounted at creation time, but it does not need the
        # repository key loaded yet. Keep those lifecycle steps separate
        # so Docker create/start failures cannot leave a credential live
        # merely because the socket had to exist first.
        #
        # `persistent_workspace_id` is a pure, deterministic function of
        # `repository.id` — the exact same id `ensure_workspace` computes
        # internally for `mode: "persistent"`. Ephemeral workspaces get
        # a random-suffixed id only `ensure_workspace` itself generates,
        # after mounts must already exist; ephemeral agent provisioning is
        # a known, documented gap (docs/TODO.md).
        workspace_key_path = resolve_ssh_key_path(ssh_config, repository)
        if workspace_key_path is not None:
            agent_record = ssh_agent_manager.ensure_agent(persistent_workspace_id(repository.id))
            mounts.append(build_ssh_agent_mount(agent_record.socket_path))
    run_kwargs["mounts"] = mounts
    record = manager.ensure_workspace(
        repository=repository,
        image=effective_image,
        mode=mode,
        workspace_path=DEFAULT_WORKSPACE_PATH,
        run_kwargs=run_kwargs,
    )
    # Persisted only now: `ensure_workspace` is what upserts the
    # `repository` row this UPDATE targets (§67 — the row may not
    # have existed a moment ago, for a repository opened for the
    # first time), and the verification above already happened
    # in-memory, before any container work, for the `deny` policy
    # check that needed it early.
    store_remote_protection(db, repository_id, remote_protection)

    # G2R-013 (`docs/GAP2_SPEC.md`) still validates that the currently
    # configured image reference exists locally. This is a configuration
    # check only: its resolved image object MUST NOT be used as runtime
    # provenance, because a mutable tag may now point at an image different
    # from the one backing an already-running persistent container.
    try:
        docker_client.images.get(effective_image)
    except docker.errors.ImageNotFound as exc:
        raise ImageNotFoundError(
            f"image not found locally: {effective_image!r}",
            details={"image": effective_image},
        ) from exc

    # G3R-006 / G3AT-006: bind the reproducibility digest to the actual
    # container, not to `record.image` or `effective_image`. Both are image
    # references and can be mutable; Docker's container inspect `Image` field
    # is the immutable image ID the current container was created from.
    # `_runtime_image_digest` intentionally has no name/tag fallback.
    image_digest = _runtime_image_digest(docker_client, record)
    if image_digest is None:
        raise ContainerRuntimeFailedError(
            f"workspace {record.id!r} has no container image identity after open",
            details={"workspace_id": record.id},
            recoverable=False,
        )
    config_version = project_config.version if project_config is not None else None
    with db:
        db.execute(
            "UPDATE workspace SET image_digest = ?, config_version = ? WHERE id = ?",
            (image_digest, config_version, record.id),
        )

    # G2R-012 (`docs/GAP2_SPEC.md`): whether `setup` runs depends on
    # whether *this container* has already had it run against it, not
    # on whether the DB row was just created — a row and a container
    # have independent lifetimes. The old "did a DB row already exist
    # before this call" check (`was_created`) got this wrong two ways:
    # a setup step failing transiently left the row in place with
    # setup permanently skipped on every retry, and a container
    # recreated behind an existing row (`docker system prune`,
    # HR-017's recovery branch in `manager_lifecycle.py`) got a
    # genuinely empty container with no signal to run setup again
    # either — `load_setup_completed_container_id`'s own docstring has
    # the full comparison this replaces it with.
    setup_completed_for = load_setup_completed_container_id(db, record.id)
    setup_ran = (
        project_config is not None
        and project_config.setup
        and record.container_id is not None
        and record.container_id != setup_completed_for
    )
    credential_cleanup_needed = False
    try:
        if workspace_key_path is not None and ssh_agent_manager is not None:
            # G3R-005: Docker create/start has succeeded. Only now may the
            # repository key enter the agent. Mark cleanup required before
            # calling `ssh-add`: even a failed provisioning attempt is
            # treated as potentially partial until Y4's checked unload path
            # proves the agent no longer holds credentials.
            credential_cleanup_needed = True
            ssh_agent_manager.load_key(record.id, workspace_key_path)

        if setup_ran:
            assert project_config is not None  # implied by setup_ran, mypy can't see through it
            await run_setup_steps(
                execution_service,
                record,
                project_config.setup,
                container_config=container_config,
                execution_config=execution_config,
            )
            # Recorded only after every step succeeds — `run_setup_steps`
            # itself fails closed on the first failing step (its own
            # docstring), so a transient failure here leaves this column
            # exactly as it was and the *next* `workspace_open` for this
            # workspace id sees the same mismatch and retries, rather than
            # silently marking a half-provisioned container complete.
            with db:
                db.execute(
                    "UPDATE workspace SET setup_completed_container_id = ? WHERE id = ?",
                    (record.container_id, record.id),
                )

        # G3R-014 / G3AT-014: the lifecycle scheduler performs idle sweeps
        # without client traffic. Keep this request-triggered sweep as
        # defense in depth; the just-opened workspace is fresh, while another
        # persistent workspace may already be eligible to stop.
        manager.sweep_idle_workspaces(
            idle_stop_after_seconds=workspace_config.idle_stop_after_seconds,
            job_store=job_store,
            audit_config=audit_config,
        )

        write_audit_event(
            audit_config,
            operation="workspace_opened",
            result="success",
            workspace_id=record.id,
            repository_id=record.repository_id,
            metadata={
                "mode": mode,
                "container_state": record.state.value,
                "remote_protection_status": remote_protection.status,
                "image": effective_image,
                "setup_steps_run": setup_ran,
                # H32 (`docs/HARDENING_SPEC.md` §9/§30 row): audited on
                # every open, not only visible to a client that already
                # thought to call `workspace_info` and check the field.
                "devcontainer_ignored": devcontainer_json_is_ignored(repository.canonical_path),
            },
        )

        return await build_workspace_info(
            record,
            registry=registry,
            container_config=container_config,
            execution_config=execution_config,
            ssh_config=ssh_config,
            db=db,
            execution_service=execution_service,
            docker_client=docker_client,
            docker_config=docker_config,
        )
    except BaseException:
        # G3R-005: every failure after credential provisioning begins —
        # including task cancellation — rolls the key back. Do not hide a
        # cleanup failure: Y4's `unload_key` raises a typed, durable
        # credential-cleanup error if it cannot prove the key/agent is gone.
        if credential_cleanup_needed and ssh_agent_manager is not None:
            ssh_agent_manager.unload_key(record.id)
        raise


def get_server_info(
    *,
    docker_client: docker.DockerClient | None,
    db: sqlite3.Connection,
    job_store: JobStore,
    registry: RepositoryRegistry,
) -> ServerInfoOutput:
    docker_available = False
    docker_version: str | None = None
    if docker_client is not None:
        try:
            docker_client.ping()
            docker_available = True
            docker_version = docker_client.version().get("Version")
        except docker.errors.DockerException:
            pass

    (active_workspaces,) = db.execute(
        "SELECT COUNT(*) FROM workspace WHERE state = 'running'"
    ).fetchone()
    running_jobs = len(
        job_store.list_by_state(JobState.QUEUED, JobState.STARTING, JobState.RUNNING)
    )

    return ServerInfoOutput(
        version=__version__,
        contract_version=CONTRACT_VERSION,
        docker_available=docker_available,
        docker_version=docker_version,
        active_workspaces=active_workspaces,
        running_jobs=running_jobs,
        configured_repositories=len(registry.list_all()),
        capabilities={
            "filesystem.patch.unified_diff": True,
            "filesystem.write.expected_sha256": True,
            "exec.jobs": True,
            "git.commit.expected_head_sha": True,
            "git.push.remote_protection": True,
            "workspace.devcontainer": False,
        },
    )


def register(
    server: MCPServer,
    *,
    db: sqlite3.Connection,
    registry: RepositoryRegistry,
    docker_client: docker.DockerClient | None,
    docker_config: DockerConfig,
    container_config: ContainerConfig,
    execution_config: ExecutionConfig,
    resources_config: ResourcesConfig,
    ssh_config: SSHConfig,
    workspace_config: WorkspaceConfig,
    github_config: GithubConfig,
    git_policy_config: GitPolicyConfig,
    audit_config: AuditConfig,
    repository_roots: list[Path],
    ssh_agent_runtime_dir: Path,
) -> None:
    """`docker_client` is `None` when Docker was unreachable at startup
    (§73's degraded state) — the tools that need it fail clearly when
    called, rather than the server failing to start at all.

    `ssh_agent_manager` is built here (not only when a container is
    actually created) so `DockerWorkspaceManager.stop`/`.delete` — not
    only `workspace_open` below — can reach it too (§19.2: the key comes
    out of the agent on every stop, including idle stop, regardless of
    which call path triggered it). `None` under `ssh.mode: file`, which
    has no agent lifecycle at all (§19.2's `file` mode).

    `execution_service` (S30) runs `.mcp-workspace.yml`'s `setup` steps —
    the same execution service `exec`/the structured Git tools already
    use, not a second path (§20.1's own rationale applies here too)."""
    ssh_agent_manager = (
        SSHAgentManager(db, runtime_dir=ssh_agent_runtime_dir)
        if ssh_config.mode == "agent"
        else None
    )
    manager = (
        DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agent_manager)
        if docker_client is not None
        else None
    )
    execution_service = ExecutionService(docker_client) if docker_client is not None else None
    job_store = JobStore(db)

    def _require_docker() -> tuple[DockerWorkspaceManager, docker.DockerClient]:
        if manager is None or docker_client is None:
            raise ContainerRuntimeFailedError(
                "Docker is not reachable; the server is running in a degraded "
                "state (§73) and cannot open or inspect workspaces"
            )
        return manager, docker_client

    @server.tool(
        name="workspace_open",
        description="Open (creating and starting as needed) a repository's workspace (§8.3).",
    )
    @translate_workspace_errors
    async def workspace_open(
        repository_id: str, mode: WorkspaceMode = "persistent"
    ) -> WorkspaceInfoOutput:
        active_manager, active_client = _require_docker()
        assert execution_service is not None  # implied by `_require_docker` above
        return await open_workspace(
            repository_id,
            mode,
            manager=active_manager,
            docker_client=active_client,
            execution_service=execution_service,
            registry=registry,
            db=db,
            job_store=job_store,
            docker_config=docker_config,
            container_config=container_config,
            execution_config=execution_config,
            resources_config=resources_config,
            ssh_config=ssh_config,
            workspace_config=workspace_config,
            github_config=github_config,
            git_policy_config=git_policy_config,
            audit_config=audit_config,
            repository_roots=repository_roots,
            ssh_agent_manager=ssh_agent_manager,
        )

    @server.tool(name="workspace_info", description="Report current workspace state (§25).")
    @translate_workspace_errors
    async def workspace_info(workspace_id: str) -> WorkspaceInfoOutput:
        # `get()` only reads the DB row; it does not itself need Docker.
        # Still gated behind `_require_docker()` for the MVP — a workspace's
        # reported container_state is only as meaningful as the daemon's
        # ability to act on it, and a degraded daemon should say so plainly
        # rather than report state it cannot currently corroborate.
        active_manager, active_client = _require_docker()
        assert execution_service is not None  # implied by `_require_docker` above
        record = get_authorized(active_manager, workspace_id, registry)
        return await build_workspace_info(
            record,
            registry=registry,
            container_config=container_config,
            execution_config=execution_config,
            ssh_config=ssh_config,
            db=db,
            execution_service=execution_service,
            docker_client=active_client,
            docker_config=docker_config,
        )

    @server.tool(name="server_info", description="Server version, capabilities, and health (§25).")
    @translate_workspace_errors
    async def server_info() -> ServerInfoOutput:
        return get_server_info(
            docker_client=docker_client, db=db, job_store=job_store, registry=registry
        )
