"""GAP3 entrypoint-isolation acceptance tests.

These tests exercise the default image's real Docker ENTRYPOINT. They do
not call `entrypoint-wrapper.py` directly, because G3R-001 is specifically
about the interpreter state that exists *before* that module reaches its
first executable line.
"""

from pathlib import Path

import docker

from dev_workspace_mcp.config.models import ContainerConfig, SSHConfig
from dev_workspace_mcp.workspace.container_config import build_workspace_run_kwargs
from dev_workspace_mcp.workspace.naming import LABEL_MANAGED


def test_at_g3at001_python_startup_state_cannot_bypass_the_privilege_drop(
    docker_client: docker.DockerClient,
    default_image_tag: str,
    ssh_config: SSHConfig,
    tmp_path: Path,
) -> None:
    """G3AT-001: hostile `PYTHONPATH` must not run `sitecustomize` as root.

    The fixture deliberately bypasses `ProjectWorkspaceConfig` validation
    and feeds `PYTHONPATH` through `extra_env`, the same lower layer the
    GAP2 mechanism-level entrypoint tests use. A pre-fix Python startup
    imports `/workspace/sitecustomize.py` before the wrapper runs. That
    module rewrites the daemon's intended uid/gid to 0, so the real Bash
    entrypoint reaches the final command as root even with `sudo: false`.

    Isolated startup (`python3 -I`) must ignore the hostile Python search
    path entirely. The final command therefore runs as uid 1234 and the
    marker proving `sitecustomize` executed must not exist.
    """
    marker = tmp_path / "sitecustomize-imported"
    (tmp_path / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path('/workspace/sitecustomize-imported').write_text(str(os.geteuid()))\n"
        "os.environ['MCP_UID'] = '0'\n"
        "os.environ['MCP_GID'] = '0'\n"
    )

    run_kwargs = build_workspace_run_kwargs(
        ssh_config=ssh_config,
        container_config=ContainerConfig(user="host", sudo=False),
        docker_client=docker_client,
        checkout_uid=1234,
        checkout_gid=1234,
        extra_env={"PYTHONPATH": "/workspace"},
    )

    output = docker_client.containers.run(
        default_image_tag,
        ["bash", "-c", "echo $EUID"],
        remove=True,
        labels={LABEL_MANAGED: "true"},
        environment=run_kwargs["environment"],
        volumes={str(tmp_path): {"bind": "/workspace", "mode": "rw"}},
    )
    text = output.decode() if isinstance(output, bytes) else str(output)

    assert text.strip() == "1234"
    assert not marker.exists()
