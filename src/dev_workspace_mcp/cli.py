"""Command-line entrypoint.

Startup follows §73's sequence. Steps 1 (config validation) and 2 (Docker
connectivity, reported not enforced) exist as of S2; step 8 (start
transport) as of S1. Steps 3-4 (state database, job reconciliation) land
with S3/S14; step 5 (eager container reconciliation, §44.1) with S18 —
`DockerWorkspaceManager.reconcile`'s per-workspace lazy version has
existed since S6 and remains in place alongside it. Step 6 (SSH-agent
reconciliation, §44.1 steps 4-5) lands with S23, after step 5, since it
needs step 5's up-to-date `running` state to know what to (re)start versus
tear down. Step 7 (validate repository roots) is enforced at config-load
time (step 1) rather than as a separate pass.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import click

import docker
from dev_workspace_mcp import __version__
from dev_workspace_mcp.config import ConfigError, docker_available, load_config
from dev_workspace_mcp.config.models import ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.errors import (
    RepositoryNotAllowedError,
    WorkspaceError,
    WorkspaceNotFoundError,
)
from dev_workspace_mcp.execution.service import ExecutionService
from dev_workspace_mcp.jobs.reconcile import reconcile_jobs
from dev_workspace_mcp.jobs.store import JobStore
from dev_workspace_mcp.logging_config import configure_logging
from dev_workspace_mcp.project_config import ProjectWorkspaceConfig, load_project_config
from dev_workspace_mcp.repositories import RepositoryRecord, RepositoryRegistry
from dev_workspace_mcp.schema_export import export_schemas
from dev_workspace_mcp.server import Transport, create_server, run_server
from dev_workspace_mcp.tools.workspace import run_setup_steps
from dev_workspace_mcp.workspace.caches import build_cache_mounts, cache_volume_name, resolve_caches
from dev_workspace_mcp.workspace.container_config import build_workspace_run_kwargs
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager, WorkspaceRecord
from dev_workspace_mcp.workspace.mounts import build_known_hosts_mount, build_workspace_mounts
from dev_workspace_mcp.workspace.naming import persistent_workspace_id
from dev_workspace_mcp.workspace.ssh_agent import SSHAgentManager, running_workspace_key_paths


@click.group()
@click.version_option(version=__version__, prog_name="dev-workspace-mcp")
def main() -> None:
    """Development Workspace MCP Server."""


@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the server configuration file (§52).",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="MCP transport to serve on (§56).",
)
def serve(config_path: Path, transport: Transport) -> None:
    """Start the MCP server."""
    # §73 step 1: validate configuration. Refuse to serve on invalid config.
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    # H31 (`docs/HARDENING_TODO.md`, HR-031): as early as possible after
    # config validation succeeds — every log call below this point,
    # anywhere in the daemon, depends on this having already run.
    configure_logging(config.logging.level)

    # §73 step 2: verify Docker connectivity. A missing daemon is reported,
    # not fatal — the daemon starts degraded rather than refusing to serve
    # tools that do not need Docker (e.g. reading a stopped workspace's
    # files, once the filesystem service is host-side per §6.3).
    if not docker_available():
        click.echo(
            "warning: Docker is not reachable; starting in a degraded state (§73)",
            err=True,
        )

    # §73 steps 3-5: open the state database, reconcile job records left
    # non-terminal by an unclean shutdown (§15.4), then reconcile every
    # workspace against real Docker state (§44.1 steps 1-3). One
    # short-lived connection for all of it, separate from the one
    # `create_server` opens below for its own lifetime — reconciliation
    # is a one-shot startup action, not something later tool calls need
    # this connection for.
    reconcile_db = open_db(config.state_db_path)
    try:
        lost = reconcile_jobs(JobStore(reconcile_db))
        for job_id, pid_still_alive in lost:
            detail = (
                " (host process may still be running; check and clean up by hand)"
                if pid_still_alive
                else ""
            )
            click.echo(
                f"warning: job {job_id} was not terminal at shutdown; marked lost{detail}",
                err=True,
            )

        # §44.1's daemon-restart sequence, steps 1-3: eagerly reconcile
        # every persisted workspace, in addition to S6's lazy
        # per-operation reconcile (which remains, as a safety net for
        # whatever changes between this sweep and actual use). Skipped
        # when Docker is unreachable, same degraded-start rule as the
        # rest of §73.
        try:
            reconcile_docker_client = docker.from_env()
        except docker.errors.DockerException:
            reconcile_docker_client = None
        if reconcile_docker_client is not None:
            try:
                DockerWorkspaceManager(reconcile_docker_client, reconcile_db).reconcile_all()
            finally:
                reconcile_docker_client.close()

        # §73 step 6, §44.1 steps 4-5: under `ssh.mode: agent`, the
        # previous agent died with the daemon (S23's own module docstring)
        # — start a fresh one for every workspace step 5 above just
        # confirmed is `running`, and tear down anything still tracked for
        # a workspace that is not. Runs after container reconciliation,
        # not before: it needs "is this workspace actually running"
        # answered by the up-to-date DB state that step just produced —
        # and is skipped under the same condition that step already is,
        # for the same reason: without Docker, "running" isn't trustworthy.
        if config.ssh.mode == "agent" and reconcile_docker_client is not None:
            SSHAgentManager(
                reconcile_db, runtime_dir=config.state_db_path.parent / "ssh-agents"
            ).reconcile(
                running_workspace_key_paths(reconcile_db, RepositoryRegistry(config), config.ssh)
            )
    finally:
        reconcile_db.close()

    # Step 7 (validate repository roots) is enforced at config-load time
    # (step 1) rather than as a separate pass.

    # §73 step 8: start the MCP transport.
    server = create_server(config)
    run_server(server, transport=transport)


@main.command(name="export-schemas")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the server configuration file (§52).",
)
@click.option(
    "--out",
    "out_path",
    default=Path("schemas/tools.json"),
    show_default=True,
    type=click.Path(path_type=Path),
    help="Where to write the exported tool contract (§33.1).",
)
def export_schemas_command(config_path: Path, out_path: Path) -> None:
    """Export the tool contract to a JSON file.

    Commit the result. CI (§71) fails if a fresh export differs from the
    committed copy — a tool's contract SHALL NOT change silently.
    """
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    server = create_server(config)
    asyncio.run(export_schemas(server, out_path))
    click.echo(f"wrote {out_path}")


def _repository_option(fn: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--repository",
        "repository_id",
        required=True,
        help="Repository id whose persistent workspace to act on (§7).",
    )(fn)


def _config_option(fn: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--config",
        "config_path",
        required=True,
        type=click.Path(path_type=Path),
        help="Path to the server configuration file (§52).",
    )(fn)


def _load_config_or_exit(config_path: Path) -> ServerConfig:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


def _docker_client_or_exit() -> docker.DockerClient:
    try:
        return docker.from_env()
    except docker.errors.DockerException as exc:
        click.echo(f"error: Docker is not reachable: {exc}", err=True)
        sys.exit(1)


def _recreated_run_kwargs(
    config: ServerConfig, docker_client: docker.DockerClient, repository: RepositoryRecord
) -> tuple[str, dict[str, Any], ProjectWorkspaceConfig | None]:
    """§8.4's `reset`/`rebuild`: a fresh container with the working tree,
    pinned known_hosts mount, `.mcp-workspace.yml`'s `image` if the
    repository has one (§9/§10, S30) — and, as of H32
    (`docs/HARDENING_SPEC.md` §9's §11/§10 rows), its `environment:` and
    named cache-volume mounts too. The earlier version of this function
    deliberately left those two out, reasoning they were "tied to state
    this standalone operator command has no reason to duplicate" — true
    of an `ssh.mode: agent` socket (a *running process*, genuinely not
    reconstructible without one), but not of `environment:` or a cache
    volume's name: `resolve_caches`/`cache_volume_name` are pure
    functions of `repository_id` and `.mcp-workspace.yml` alone, the
    exact same values `workspace_open` itself would compute — nothing
    to "duplicate state" from, and every reason to restore them, since
    §8.4 says `reset` discards *container-local* state, not the
    project's own configured environment. The ssh-agent socket remains
    the one deliberate exclusion: an operator who needs it back still
    follows this with a normal `workspace_open` call, which reattaches
    it because the container this just recreated has none yet.

    Returns the loaded `ProjectWorkspaceConfig` alongside the usual
    `(image, run_kwargs)` pair so callers (`reset`/`rebuild` below) can
    re-run its `setup` steps against the freshly recreated container
    without loading the file a second time."""
    project_config = load_project_config(repository.canonical_path)
    image = (
        project_config.image
        if project_config is not None and project_config.image is not None
        else config.docker.default_image
    )
    resolved_caches = resolve_caches(
        repository_id=repository.id,
        project_caches=project_config.caches if project_config is not None else [],
    )
    extra_env = dict(project_config.environment) if project_config is not None else {}
    if resolved_caches:
        extra_env["MCP_CACHE_PATHS"] = ":".join(c.home_relative_path for c in resolved_caches)
    run_kwargs = build_workspace_run_kwargs(
        container_config=config.container,
        docker_client=docker_client,
        ssh_config=config.ssh,
        extra_env=extra_env,
        resources_config=config.resources,
    )
    mounts = build_workspace_mounts(
        host_repository_path=repository.canonical_path,
        workspace_path="/workspace",
        git_dir_mode=config.container.git_dir_mode,
        authorized_roots=config.repository_roots,
    )
    mounts.append(build_known_hosts_mount(config.ssh.known_hosts))
    mounts.extend(build_cache_mounts(resolved_caches))
    run_kwargs["mounts"] = mounts
    return image, run_kwargs, project_config


def _run_async(coro: Coroutine[Any, Any, None]) -> None:
    """Runs `coro` to completion. `asyncio.run` alone refuses to nest
    inside an already-running event loop — never true for `reset`/
    `rebuild` in real, standalone CLI use (a plain `def` click command,
    invoked from a process with no event loop of its own), but real
    inside this project's own async test suite, which drives `main`
    through `click.testing.CliRunner` from *inside* an `async def`
    test. A dedicated thread with its own fresh loop sidesteps the
    nesting restriction entirely rather than assuming the caller is
    always loop-free."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, coro).result()


async def _run_setup_steps_after_recreate(
    config: ServerConfig,
    docker_client: docker.DockerClient,
    record: WorkspaceRecord,
    project_config: ProjectWorkspaceConfig | None,
) -> None:
    """H32 (`docs/HARDENING_SPEC.md` §9's §8.4/§10 rows): `reset` and
    `rebuild` both hand back a genuinely fresh container — no
    `.mcp-workspace.yml` `setup` step has ever run against it, and
    (`run_setup_steps`'s own docstring) the next `workspace_open` for
    this *already-existing* workspace id would never trigger them
    either, leaving it silently, permanently half-provisioned. Run
    here, synchronously, as part of the operator command that just
    discarded the old environment — the same steps `workspace_open`
    itself runs for a brand-new workspace, through the same execution
    service (§20.1's "no second path" rationale)."""
    if project_config is None or not project_config.setup:
        return
    execution_service = ExecutionService(docker_client)
    try:
        await run_setup_steps(
            execution_service,
            record,
            project_config.setup,
            container_config=config.container,
            execution_config=config.execution,
        )
    except WorkspaceError as exc:
        click.echo(f"error: setup step failed after recreation: {exc}", err=True)
        sys.exit(1)


@main.command()
@_config_option
@_repository_option
def reset(config_path: Path, repository_id: str) -> None:
    """§8.4 operator path (AT-005): discard container-local environment
    state and recreate the container fresh, from the currently-cached
    image — no image pull (that is `rebuild`'s distinct behavior, H32
    below). The host repository is a bind mount `DockerWorkspaceManager.
    reset` never touches — it SHALL remain exactly as it was.

    H32 (`docs/HARDENING_SPEC.md` §9's §10 row): `.mcp-workspace.yml`
    `setup` steps run again afterward, against the fresh container —
    without this, they would never run again for this workspace id at
    all (`run_setup_steps`'s own docstring)."""
    config = _load_config_or_exit(config_path)
    registry = RepositoryRegistry(config)
    try:
        repository = registry.get(repository_id)
    except RepositoryNotAllowedError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    docker_client = _docker_client_or_exit()
    db = open_db(config.state_db_path)
    workspace_id = persistent_workspace_id(repository.id)
    try:
        image, run_kwargs, project_config = _recreated_run_kwargs(config, docker_client, repository)
        record = DockerWorkspaceManager(docker_client, db).reset(
            workspace_id, image=image, run_kwargs=run_kwargs
        )
        _run_async(_run_setup_steps_after_recreate(config, docker_client, record, project_config))
    except WorkspaceNotFoundError:
        click.echo(
            f"error: no workspace open yet for repository {repository_id!r}; nothing to reset",
            err=True,
        )
        sys.exit(1)
    finally:
        db.close()
        docker_client.close()
    click.echo(f"workspace {workspace_id!r} reset: container recreated, repository untouched")


@main.command()
@_config_option
@_repository_option
def rebuild(config_path: Path, repository_id: str) -> None:
    """§8.4 operator path: rebuild the container while preserving the
    repository — the host repository is a bind mount this SHALL never
    touch, same guarantee as `reset`.

    H32 (`docs/HARDENING_SPEC.md` §9's §8.4 row): distinct from `reset`,
    not merely a second name for the same operation — the prior version
    of this command shared `reset`'s exact behavior, which is the
    defect that row names. `rebuild` pulls the image fresh first,
    picking up an upstream change to a mutable tag (a plain
    `docker.default_image: ubuntu:24.04` being updated, say) that
    `reset` deliberately leaves alone — `reset` is "discard container-
    local state, keep everything else exactly as it was," `rebuild` is
    "start over from a current image too." The pull is best-effort: a
    locally-built-only image (this project's own default `docker/
    default/Dockerfile`, or a hand-built `.mcp-workspace.yml` `image`)
    was never pushed anywhere, so "not found on any registry" is
    expected there, not a reason to refuse a rebuild the operator
    explicitly asked for — it falls back to the locally cached image
    with a warning, never a hard failure. `.mcp-workspace.yml` `setup`
    steps run again afterward, same as `reset` (`run_setup_steps`'s own
    docstring) — a freshly pulled image needs them at least as much as
    a freshly recreated container from the existing one does."""
    config = _load_config_or_exit(config_path)
    registry = RepositoryRegistry(config)
    try:
        repository = registry.get(repository_id)
    except RepositoryNotAllowedError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    docker_client = _docker_client_or_exit()
    db = open_db(config.state_db_path)
    workspace_id = persistent_workspace_id(repository.id)
    try:
        image, run_kwargs, project_config = _recreated_run_kwargs(config, docker_client, repository)
        try:
            docker_client.images.pull(image)
        except docker.errors.DockerException as exc:
            # Best-effort, not fail-closed: `image` is frequently a
            # locally-built tag (`docker/default/Dockerfile`'s own
            # image, or a project's own `.mcp-workspace.yml` `image`
            # built by hand) that was never pushed to any registry —
            # "not found on any registry" is the *expected* outcome
            # there, not a reason to refuse a rebuild the operator
            # explicitly asked for. Proceeds with whatever image is
            # already cached locally, same as `reset` would.
            click.echo(
                f"warning: could not pull image {image!r} ({exc}); "
                "using the locally cached image instead",
                err=True,
            )
        record = DockerWorkspaceManager(docker_client, db).rebuild(
            workspace_id, image=image, run_kwargs=run_kwargs
        )
        _run_async(_run_setup_steps_after_recreate(config, docker_client, record, project_config))
    except WorkspaceNotFoundError:
        click.echo(
            f"error: no workspace open yet for repository {repository_id!r}; nothing to rebuild",
            err=True,
        )
        sys.exit(1)
    finally:
        db.close()
        docker_client.close()
    click.echo(
        f"workspace {workspace_id!r} rebuilt: image pulled, container recreated, "
        f"repository untouched"
    )


@main.command(name="delete")
@_config_option
@_repository_option
def delete_workspace(config_path: Path, repository_id: str) -> None:
    """§8.4 operator path: remove workspace/container metadata (and
    terminate its ssh-agent, §19.2). SHALL NOT delete the host
    repository — Docker resources and the state-db row only."""
    config = _load_config_or_exit(config_path)
    registry = RepositoryRegistry(config)
    try:
        repository = registry.get(repository_id)
    except RepositoryNotAllowedError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    docker_client = _docker_client_or_exit()
    db = open_db(config.state_db_path)
    workspace_id = persistent_workspace_id(repository.id)
    ssh_agent_manager = (
        SSHAgentManager(db, runtime_dir=config.state_db_path.parent / "ssh-agents")
        if config.ssh.mode == "agent"
        else None
    )
    # HR-009 (`docs/HARDENING_SPEC.md` §4.2): reclaim this repository's
    # own cache volumes on delete — otherwise a repository id later
    # reused for a different project would silently inherit them.
    # Recomputed from the current `.mcp-workspace.yml`, the same way
    # `workspace_open` itself resolves them — best-effort: a cache
    # declaration removed or renamed since the workspace was last opened
    # means that specific volume is missed here, not an error.
    project_config = load_project_config(repository.canonical_path)
    cache_volume_names = [
        cache_volume_name(repository_id=repository.id, cache_name=entry.name)
        for entry in (project_config.caches if project_config is not None else [])
    ]
    try:
        DockerWorkspaceManager(docker_client, db, ssh_agent_manager=ssh_agent_manager).delete(
            workspace_id, cache_volume_names=cache_volume_names
        )
    except WorkspaceNotFoundError:
        click.echo(
            f"error: no workspace open yet for repository {repository_id!r}; nothing to delete",
            err=True,
        )
        sys.exit(1)
    finally:
        db.close()
        docker_client.close()
    click.echo(f"workspace {workspace_id!r} deleted: repository untouched")
