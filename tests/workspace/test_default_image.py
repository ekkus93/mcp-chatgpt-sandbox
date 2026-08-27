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

from pathlib import Path

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
