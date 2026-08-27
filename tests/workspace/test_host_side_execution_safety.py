"""H3 (`docs/HARDENING_SPEC.md` HR-002, HAT-002): a host-side Git read
must not execute repository-supplied code under the daemon's identity —
through the real MCP tool layer, across every tool `git/service.py`'s
`run_git_host_side` backs.

Real regressions, reproduced during the 2026-08-27 review: a repository
configuring `diff.external`, a `.gitattributes`-bound `diff.<name>.textconv`,
or a `.gitattributes`-bound `filter.<name>.clean` driver got that driver
executed as the daemon uid by `git_diff`/`git_log`/`git_status` — and, via
`read_basic_git_state`, by `workspace_open`/`workspace_info` too. `.git/`
is container-writable by default, and `exec` gives the client arbitrary
shell inside the container, so a client can author `.git/config` and
`.gitattributes` itself — HR-000's framing: this is client-reachable, not
merely a hostile-repository concern.

Each driver script touches a marker file *on the host* (these are
host-side reads, running as the daemon uid, per §20.1) — deliberately
outside anything mounted into the container, so a marker's existence can
only mean the host-side `git` invocation itself ran the driver.
"""

import subprocess
from pathlib import Path

import docker
from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.server import create_server


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


def _driver_script(marker: Path, *, echo: str) -> Path:
    script = marker.parent / f"{marker.name}.driver.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\necho {echo}\n")
    script.chmod(0o755)
    return script


async def _call_every_host_side_read(server, workspace_id: str) -> None:
    """The exact tool set HAT-002 names: the three structured host-side
    reads plus `workspace_info`, which reaches the same plumbing through
    `read_basic_git_state`."""
    await server.call_tool("git_status", {"workspace_id": workspace_id})
    await server.call_tool("git_diff", {"workspace_id": workspace_id})
    await server.call_tool("git_log", {"workspace_id": workspace_id})
    await server.call_tool("workspace_info", {"workspace_id": workspace_id})


async def test_at_hat002_diff_external_does_not_execute(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "a.txt").write_text("two\n")  # an unstaged change for diff to see
    marker = tmp_path / "EXTERNAL_DIFF_RAN"
    script = _driver_script(marker, echo="ext")
    subprocess.run(["git", "config", "diff.external", str(script)], cwd=repo_path, check=True)

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_host_side_read(server, workspace_id)

    assert not marker.exists()
    # the read still works, and still reports the real change — a read
    # that merely fails is not a pass.
    diff = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert "+two" in diff.structured_content["content"]


async def test_at_hat002_textconv_does_not_execute(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "a.txt").write_text("two\n")
    marker = tmp_path / "TEXTCONV_RAN"
    script = _driver_script(marker, echo="converted")
    (repo_path / ".gitattributes").write_text("a.txt diff=hardening\n")
    subprocess.run(
        ["git", "config", "diff.hardening.textconv", str(script)], cwd=repo_path, check=True
    )

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_host_side_read(server, workspace_id)

    assert not marker.exists()
    diff = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert "+two" in diff.structured_content["content"]


async def test_at_hat002_filter_clean_does_not_execute(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`filter.<name>.clean` is not a diff-specific mechanism the way
    `diff.external`/`textconv` are — it can fire from `git status` itself,
    not only `git diff`, depending on Git's own internal stat-based
    "racily clean" heuristics (verified inconsistent across repository
    states in isolation; the fix cannot rely on which host-side tool
    happens to trigger it in a given case, which is exactly why the
    neutralization lives in the shared `run_git_host_side`, not in a
    diff-specific flag). Calling all four tools in sequence below and
    asserting the marker never appears exercises this without depending
    on which one is responsible in this particular scenario."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    marker = tmp_path / "FILTER_CLEAN_RAN"
    script = _driver_script(marker, echo="cleaned")
    (repo_path / ".gitattributes").write_text("a.txt filter=hardening\n")
    subprocess.run(
        ["git", "config", "filter.hardening.clean", str(script)], cwd=repo_path, check=True
    )
    (repo_path / "a.txt").write_text("two\n")

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_host_side_read(server, workspace_id)

    assert not marker.exists()
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert "a.txt" in status.structured_content["unstaged"]


async def test_at_hat002_an_arbitrary_unlisted_driver_name_is_still_neutralized(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The mechanism has to be dynamic discovery, not a static list of
    expected driver names — a driver named something nobody anticipated
    is neutralized exactly the same way, because it's read back out of
    the repository's own configuration rather than matched by name."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    marker = tmp_path / "ARBITRARY_NAME_RAN"
    script = _driver_script(marker, echo="surprise")
    (repo_path / ".gitattributes").write_text(
        "a.txt filter=totally-unexpected-driver-name-xyz123\n"
    )
    subprocess.run(
        ["git", "config", "filter.totally-unexpected-driver-name-xyz123.clean", str(script)],
        cwd=repo_path,
        check=True,
    )
    (repo_path / "a.txt").write_text("two\n")

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_host_side_read(server, workspace_id)

    assert not marker.exists()
