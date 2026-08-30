"""S7: the real default image and entrypoint, against a real Docker daemon
(§9.1, §5.3, AT-014, AT-015, AT-016).

Identity proofs run as **one-shot containers** (`containers.run(...,
remove=True)`) rather than `exec_run` on a long-lived one: `docker exec`
uses the image's configured user by default (root, absent a `USER`
directive), not whatever PID 1 dropped to via `entrypoint.sh`'s `setpriv`.
A one-shot container's command *is* PID 1, so it actually exercises the
entrypoint's identity-drop — which is the thing under test here. Which
user a later `exec` call should run as is S12's problem, not S7's.
"""

import uuid
from pathlib import Path

import docker.types
import pytest

import docker
from dev_workspace_mcp.config.models import ContainerConfig, SSHConfig
from dev_workspace_mcp.errors import ContainerCreateFailedError
from dev_workspace_mcp.repositories import RepositoryRecord
from dev_workspace_mcp.workspace.container_config import build_workspace_run_kwargs
from dev_workspace_mcp.workspace.manager import DockerWorkspaceManager
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED, LABEL_WORKSPACE_ID


def _run_oneshot(
    docker_client: docker.DockerClient, image: str, command: list[str], **kwargs: object
) -> str:
    labels = {LABEL_MANAGED: "true", **kwargs.pop("labels", {})}  # type: ignore[arg-type]
    output = docker_client.containers.run(image, command, remove=True, labels=labels, **kwargs)
    return output.decode() if isinstance(output, bytes) else str(output)


def test_entrypoint_drops_to_the_checkout_uid_and_grants_sudo(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    config = ContainerConfig(user="host", sudo=True)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
    )
    output = _run_oneshot(
        docker_client,
        default_image_tag,
        ["sh", "-c", "id -u && whoami && sudo whoami"],
        environment=run_kwargs["environment"],
    )
    lines = output.strip().splitlines()
    assert lines[0] == "1234"  # id -u: the checkout uid, not root
    assert lines[1] != "root"  # whoami: a real (non-root) username was created
    assert lines[2] == "root"  # sudo whoami: passwordless sudo works


def test_at_hat005_hostile_extra_env_cannot_make_pid_1_root(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """HR-006 (`docs/HARDENING_SPEC.md` §6), proven against a real
    container rather than only the `run_kwargs` dict: `extra_env`
    containing `MCP_UID`/`MCP_GID` — simulating the allowlist reject in
    `ProjectWorkspaceConfig` being bypassed, since it now runs first and
    would refuse this before `build_workspace_run_kwargs` is ever
    reached — must still not make PID 1 root, because
    `build_workspace_run_kwargs` merges `extra_env` first and the
    daemon's own identity variables last."""
    config = ContainerConfig(user="host", sudo=False)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        extra_env={"MCP_UID": "0", "MCP_GID": "0"},
    )
    output = _run_oneshot(
        docker_client,
        default_image_tag,
        ["sh", "-c", "id -u"],
        environment=run_kwargs["environment"],
    )
    assert output.strip() == "1234"  # not "0"


def test_at_gr005_a_hostile_path_does_not_skip_the_privilege_drop(
    docker_client: docker.DockerClient,
    default_image_tag: str,
    ssh_config: SSHConfig,
    tmp_path: Path,
) -> None:
    """GR-005 (`docs/GAP_SPEC.md`): `entrypoint.sh` now sets its own
    hardcoded `PATH` before the first external command it runs —
    proven here the same way `test_at_hat005_*` above proves HR-006's
    entrypoint-side backstop: simulate a filter above this layer being
    bypassed (`build_workspace_run_kwargs`'s `extra_env` is the lowest
    layer that still enforces nothing about `PATH`) and confirm the
    entrypoint's own defense holds regardless.

    A fake `id` planted on a poisoned `PATH` always reports a non-root
    uid, which — pre-fix — made `entrypoint.sh`'s own `[ "$(id -u)" !=
    "0" ]` check believe it wasn't root and `exec` the workspace command
    with **no privilege drop performed at all**, running it as the
    *real* root. `$EUID` (a bash builtin, never resolved through
    `PATH`) is what checks the result: the workspace command's own
    later `id -u` call would be shadowed by the same fake binary too,
    so asserting through it here could pass for the wrong reason even
    post-fix."""
    hostile_bin = tmp_path / "hostile-bin"
    hostile_bin.mkdir()
    fake_id = hostile_bin / "id"
    fake_id.write_text("#!/bin/sh\necho 1000\n")
    fake_id.chmod(0o755)

    config = ContainerConfig(user="host", sudo=False)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        extra_env={
            "PATH": "/hostile-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        },
    )
    output = _run_oneshot(
        docker_client,
        default_image_tag,
        ["bash", "-c", "echo $EUID"],
        environment=run_kwargs["environment"],
        volumes={str(hostile_bin): {"bind": "/hostile-bin", "mode": "ro"}},
    )
    # 1234 (the checkout uid): the drop happened despite the hostile
    # PATH. Not "0" (drop skipped, running as real root) and not
    # "1000" (the fake `id`'s own claim, which $EUID cannot report).
    assert output.strip() == "1234"


def test_at_hat007_a_traversal_in_mcp_cache_paths_is_rejected_by_the_entrypoint(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """HR-008 (`docs/HARDENING_SPEC.md` §6): `workspace/caches.py`
    validates `caches[].path` from `.mcp-workspace.yml`, but this proves
    the entrypoint's own defense holds regardless of what a caller
    passes as `cache_paths` — the daemon-side validation the docstring
    above describes as "necessary but not sufficient on its own." Since
    this runs as root, before the identity drop, an unvalidated entry
    here means an arbitrary `mkdir`/`chown` as root: reproduced with
    `MCP_CACHE_PATHS='../../etc'` leaving `/etc` owned by the container
    user. (RR-031, `docs/REMEDIATION_SPEC.md` §32: `cache_paths` is now
    the only channel `MCP_CACHE_PATHS` reaches `environment` through —
    `extra_env` no longer carries it at all, so it is not this test's
    tool for simulating a bypass the way `test_at_hat005_*` above uses
    it for `MCP_UID`/`SSH_AUTH_SOCK`.)"""
    config = ContainerConfig(user="host", sudo=False)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=["../../etc"],
    )
    with pytest.raises(docker.errors.ContainerError, match="MCP_CACHE_PATHS"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=run_kwargs["environment"],
        )


def test_rr029_a_newline_in_mcp_cache_paths_is_rejected_not_silently_truncated(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """RR-029 (`docs/REMEDIATION_SPEC.md` §26): `IFS=':' read -ra ... <<<
    "$MCP_CACHE_PATHS"` stops at the first newline, so
    `$'.cargo\\n../../etc'` used to silently drop `../../etc` — no
    rejection, no mount — while the entrypoint's own comment (corrected
    by this task) claimed it "exits non-zero rather than silently
    skipping the bad entry". It must actually exit non-zero here."""
    config = ContainerConfig(user="host", sudo=False)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".cargo\n../../etc"],
    )
    with pytest.raises(docker.errors.ContainerError, match="newline"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=run_kwargs["environment"],
        )


def test_rr029_a_trailing_empty_entry_is_rejected_the_same_as_a_leading_one(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """RR-029: `read -ra`'s field splitting silently absorbed a
    *trailing* empty field (`.cargo:` used to produce one field, not the
    two elements a leading or middle empty correctly produced) — a
    position-dependent inconsistency in what's supposed to be a uniform
    rejection rule."""
    config = ContainerConfig(user="host", sudo=False)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".cargo", ""],
    )
    with pytest.raises(docker.errors.ContainerError, match="empty entry"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=run_kwargs["environment"],
        )


def test_rr030_a_symlink_planted_in_a_cache_volume_is_not_followed(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """RR-030: `mkdir -p`/`chown` (no `-h`) both follow symlinks, as
    root, before the identity drop. Reproduced across two real container
    runs sharing one real, persisted named volume — exactly the
    mechanism §11 cache persistence relies on: the first run's
    (unprivileged, post-identity-drop) command plants a symlink pointing
    outside `$HOME` inside the mounted cache, the same way a real build
    tool's cache directory could end up with one; the second run names a
    cache path that reaches through it and must be refused, not
    silently followed into chowning whatever the symlink points at."""
    volume_name = f"mcp-test-r19-{uuid.uuid4().hex[:12]}"
    mount = docker.types.Mount(
        target="/home/mcpuser/.cargo", source=volume_name, type="volume", read_only=False
    )
    config = ContainerConfig(user="host", sudo=False)

    plant_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".cargo"],
    )
    docker_client.containers.run(
        default_image_tag,
        ["sh", "-c", "ln -s /etc /home/mcpuser/.cargo/registry"],
        remove=True,
        labels={LABEL_MANAGED: "true"},
        environment=plant_kwargs["environment"],
        mounts=[mount],
    )

    reach_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".cargo/registry"],
    )
    with pytest.raises(docker.errors.ContainerError, match="symlink"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=reach_kwargs["environment"],
            mounts=[mount],
        )


def test_at_vat004_a_symlinked_ssh_config_planted_in_a_cache_volume_is_not_followed(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """VR-004 (`docs/REVIEW_SPEC.md` §4): `caches[].path` reaches
    `$HOME/.ssh` (`_home_relative_path` accepts both `.ssh` and `.`,
    $HOME itself). Unlike the cache loop above — hardened by RR-030 —
    the SSH block below it wrote into `$HOME/.ssh/config` with a plain
    redirect and `chmod`, both of which follow a symlink, as root,
    before the identity drop. Same two-real-container-runs-sharing-one-
    persisted-volume shape as `test_rr030_*` above: the first run's
    unprivileged command plants `config -> /etc/hostname` inside the
    mounted `.ssh`; the second run's entrypoint (root phase) must refuse,
    not truncate and `chmod 600` whatever `config` points at. The plant
    target is a plain file, not a directory: a symlink to a directory
    hits the shell redirect's own "Is a directory" error regardless of
    this fix, which would make a pre-fix run fail for the wrong reason."""
    volume_name = f"mcp-test-vr004-{uuid.uuid4().hex[:12]}"
    mount = docker.types.Mount(
        target="/home/mcpuser/.ssh", source=volume_name, type="volume", read_only=False
    )
    config = ContainerConfig(user="host", sudo=False)

    plant_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".ssh"],
    )
    docker_client.containers.run(
        default_image_tag,
        # `-f`: entrypoint.sh's own root phase already wrote a real
        # `config` file into the cache-mounted `.ssh` on *this* run,
        # before dropping to this unprivileged command — unlike
        # `.cargo/registry` above, nothing else ever populates that
        # path, but `.ssh/config` is written on every run. Forcing
        # replaces it with the symlink the *next* run's root phase must
        # refuse to follow.
        ["sh", "-c", "ln -sf /etc/hostname /home/mcpuser/.ssh/config"],
        remove=True,
        labels={LABEL_MANAGED: "true"},
        environment=plant_kwargs["environment"],
        mounts=[mount],
    )

    reach_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=[".ssh"],
    )
    with pytest.raises(docker.errors.ContainerError, match="symlink"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=reach_kwargs["environment"],
            mounts=[mount],
        )


def test_at_gr009_a_symlinked_ssh_directory_planted_in_a_cache_volume_is_not_followed(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """GR-009 (`docs/GAP_SPEC.md`): VAT-004 above exercises
    `entrypoint.sh`'s `config`-*file* refusal (`[ -L
    "$HOME_DIR/.ssh/config" ]`); the sibling refusal one line earlier
    (`[ -L "$HOME_DIR/.ssh" ]`, for `.ssh` itself) had no test at all.

    `.ssh` itself cannot be replaced with a symlink through a cache
    volume mounted *at* `.ssh` (`cache_paths=[".ssh"]`, VAT-004's own
    setup) — a Docker volume mount always creates a real directory at
    its own mount point, so the mount point itself can never be a
    symlink. `cache_paths=["."]` — `.` normalizes to `$HOME` itself, per
    `entrypoint.sh`'s own docstring here — mounts a persistent,
    container-writable volume at `$HOME` *containing* `.ssh` as an
    ordinary subdirectory, which an unprivileged run genuinely can
    replace with a symlink."""
    volume_name = f"mcp-test-gr009-{uuid.uuid4().hex[:12]}"
    mount = docker.types.Mount(
        target="/home/mcpuser", source=volume_name, type="volume", read_only=False
    )
    config = ContainerConfig(user="host", sudo=False)

    plant_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=["."],
    )
    docker_client.containers.run(
        default_image_tag,
        # entrypoint.sh's own root phase already created a real `.ssh`
        # directory (with `config` inside it) on *this* run, before
        # dropping to this unprivileged command — replaced outright with
        # a symlink for the *next* run's root phase to encounter.
        ["sh", "-c", "rm -rf /home/mcpuser/.ssh && ln -s /etc /home/mcpuser/.ssh"],
        remove=True,
        labels={LABEL_MANAGED: "true"},
        environment=plant_kwargs["environment"],
        mounts=[mount],
    )

    reach_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        cache_paths=["."],
    )
    with pytest.raises(docker.errors.ContainerError, match="symlink"):
        docker_client.containers.run(
            default_image_tag,
            ["sh", "-c", "id -u"],
            remove=True,
            labels={LABEL_MANAGED: "true"},
            environment=reach_kwargs["environment"],
            mounts=[mount],
        )


def test_at_014_sudo_apt_get_install_works_as_non_root(
    docker_client: docker.DockerClient, default_image_tag: str, ssh_config: SSHConfig
) -> None:
    """AT-014: the project container is capable of installing normal OS
    packages through `sudo apt-get` while its default identity is the
    checkout owner, not root."""
    config = ContainerConfig(user="host", sudo=True)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
    )
    install_and_verify = (
        "whoami && sudo apt-get update -qq "
        "&& sudo apt-get install -y -qq figlet "
        "&& figlet -v >/dev/null && echo INSTALL_OK"
    )
    output = _run_oneshot(
        docker_client,
        default_image_tag,
        ["sh", "-c", install_and_verify],
        environment=run_kwargs["environment"],
    )
    assert "root" not in output.splitlines()[0]
    assert "INSTALL_OK" in output


def test_at_015_file_written_from_container_is_owned_by_checkout_uid_on_host(
    docker_client: docker.DockerClient,
    default_image_tag: str,
    tmp_path: Path,
    ssh_config: SSHConfig,
) -> None:
    """AT-015: a file written from the container appears in the host
    checkout owned by the checkout owner's uid/gid, and the human can
    overwrite/delete it without elevated privileges."""
    import os

    checkout_uid, checkout_gid = os.getuid(), os.getgid()
    config = ContainerConfig(user="host", sudo=True)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=checkout_uid,
        checkout_gid=checkout_gid,
    )
    _run_oneshot(
        docker_client,
        default_image_tag,
        ["sh", "-c", "echo from-container > /workspace/testfile.txt"],
        environment=run_kwargs["environment"],
        volumes={str(tmp_path): {"bind": "/workspace", "mode": "rw"}},
    )

    written = tmp_path / "testfile.txt"
    assert written.exists()
    stat = written.stat()
    assert (stat.st_uid, stat.st_gid) == (checkout_uid, checkout_gid)

    # The human (this test process, running as checkout_uid) can overwrite
    # and delete it without elevated privileges — plain filesystem calls,
    # no sudo, no chmod.
    written.write_text("overwritten by the human")
    written.unlink()


def test_at_016_root_mode_without_userns_remap_creates_no_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
    default_image_tag: str,
    ssh_config: SSHConfig,
) -> None:
    """AT-016: with `container.user: root` and no user-namespace remapping
    active, workspace creation SHALL fail with `CONTAINER_CREATE_FAILED`
    rather than start a container that would write root-owned files."""
    config = ContainerConfig(user="root", sudo=True)

    with pytest.raises(ContainerCreateFailedError):
        run_kwargs = build_workspace_run_kwargs(
            ssh_config=ssh_config, container_config=config, docker_client=docker_client
        )
        manager.ensure_workspace(
            repository=repository, image=default_image_tag, run_kwargs=run_kwargs
        )

    # No container was created under any label for this repository.
    labeled = docker_client.containers.list(
        all=True, filters={"label": f"{LABEL_WORKSPACE_ID}=ws_{repository.id}"}
    )
    assert labeled == []


def test_ensure_workspace_with_default_image_is_a_long_running_non_root_container(
    manager: DockerWorkspaceManager,
    docker_client: docker.DockerClient,
    repository: RepositoryRecord,
    default_image_tag: str,
    ssh_config: SSHConfig,
) -> None:
    """The manager-level integration: a persistent workspace built from the
    real default image, left running on its default CMD (`sleep
    infinity`), presents the checkout identity to a command run as that
    user explicitly — the actual user selection for `exec` is S12's
    concern; this only proves the container the manager produces is
    coherent with what the entrypoint established."""
    import os

    config = ContainerConfig(user="host", sudo=True)
    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=config,
        docker_client=docker_client,
        checkout_uid=os.getuid(),
        checkout_gid=os.getgid(),
    )
    record = manager.ensure_workspace(
        repository=repository, image=default_image_tag, run_kwargs=run_kwargs
    )
    assert record.state.value == "running"

    container = docker_client.containers.get(record.container_id)  # type: ignore[arg-type]
    exit_code, output = container.exec_run(["id", "-u"], user=str(os.getuid()))
    assert exit_code == 0
    assert output.decode().strip() == str(os.getuid())
