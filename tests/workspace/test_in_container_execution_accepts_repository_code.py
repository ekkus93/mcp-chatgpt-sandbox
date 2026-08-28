"""H36 (`docs/HARDENING_TODO.md`): the inverse of HAT-002/HR-002
(`docs/HARDENING_SPEC.md` §4.1) for the four tools this migration has
moved in-container so far — `git_status`, `git_diff`, `git_log`,
`workspace_info` (steps 2-5/8; `git_commit`/`git_pull`/`git_push`'s own
*internal* host-side reads migrate in steps 6-7, and still carry HR-002's
protection until they do).

Replaces `tests/workspace/test_host_side_execution_safety.py`, which
asserted the opposite for these same four tools and the same four driver
shapes. Not every one of those four assertions actually flipped, and
this file distinguishes the two rather than assuming uniformly:

- `filter.<name>.clean` and an arbitrary unlisted driver name bound to
  it: now genuinely execute, in-container, as the container user —
  exactly as `exec("git ...")` already could before this migration.
  HR-002's dynamic-discovery neutralization (`_discover_code_executing_
  overrides`) is what blocked these before, and it is gone. This is not
  a regression to guard against; it is H36's own stated rationale
  (`docs/HARDENING_SPEC.md` §4's host-side execution *class* is retired,
  not merely relocated) made directly observable.
- `diff.external`/`diff.<name>.textconv`: still do not execute —
  `git/diff.py`'s `build_diff_argv` passes `--no-ext-diff --no-textconv`
  unconditionally, on *every* `git diff` invocation, kept for a
  content-correctness reason unrelated to HR-002 (an external diff/
  textconv driver's own output replacing the real diff would corrupt
  what the client sees, regardless of any code-execution concern). These
  two were never reachable through `git_diff` for a reason H36's
  retirement of HR-002 doesn't touch, confirmed here empirically rather
  than assumed.
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


def _driver_script(marker_container_path: str, *, echo: str) -> str:
    """A driver *inside the container* — `.git/config`'s value is a
    shell command the container's own `git` invokes, so the script
    itself has to exist in a path the container can execute, not the
    host. `sh -c` inline is simplest: no separate script file to bind
    mount or write into the container's filesystem."""
    return f"sh -c 'touch {marker_container_path}; echo {echo}'"


async def _call_every_migrated_read(server, workspace_id: str) -> None:
    """The same four tools HAT-002 originally named — now all in-container."""
    await server.call_tool("git_status", {"workspace_id": workspace_id})
    await server.call_tool("git_diff", {"workspace_id": workspace_id})
    await server.call_tool("git_log", {"workspace_id": workspace_id})
    await server.call_tool("workspace_info", {"workspace_id": workspace_id})


async def _marker_exists_in_container(
    server, workspace_id: str, marker_container_path: str
) -> bool:
    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["test", "-e", marker_container_path]}
    )
    return bool(result.structured_content["exit_code"] == 0)


async def test_diff_external_still_does_not_execute(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Not every driver H36 stopped neutralizing actually reaches Git:
    `build_diff_argv` still passes `--no-ext-diff` unconditionally on
    every `git diff` invocation, kept for a *content-correctness* reason
    unrelated to HR-002 (an external diff driver's own output replacing
    the real diff would corrupt what the client sees) — so `diff.external`
    was never reachable through `git_diff` for a reason HR-002's
    retirement doesn't touch. Confirmed empirically here, not assumed,
    the same discipline the rest of this file applies to what *did*
    change."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "a.txt").write_text("two\n")  # an unstaged change for diff to see
    marker = "/tmp/EXTERNAL_DIFF_RAN"
    subprocess.run(
        ["git", "config", "diff.external", _driver_script(marker, echo="ext")],
        cwd=repo_path,
        check=True,
    )

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_migrated_read(server, workspace_id)

    assert not await _marker_exists_in_container(server, workspace_id, marker)
    # the read still works, and still reports the real change.
    diff = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert "+two" in diff.structured_content["content"]


async def test_textconv_still_does_not_execute(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Same reasoning as `test_diff_external_still_does_not_execute`:
    `build_diff_argv`'s unconditional `--no-textconv` is unrelated to
    HR-002 and still applies."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "a.txt").write_text("two\n")
    marker = "/tmp/TEXTCONV_RAN"
    (repo_path / ".gitattributes").write_text("a.txt diff=hardening\n")
    subprocess.run(
        ["git", "config", "diff.hardening.textconv", _driver_script(marker, echo="converted")],
        cwd=repo_path,
        check=True,
    )

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_migrated_read(server, workspace_id)

    assert not await _marker_exists_in_container(server, workspace_id, marker)
    diff = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert "+two" in diff.structured_content["content"]


async def test_filter_clean_now_executes_in_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    marker = "/tmp/FILTER_CLEAN_RAN"
    (repo_path / ".gitattributes").write_text("a.txt filter=hardening\n")
    subprocess.run(
        ["git", "config", "filter.hardening.clean", _driver_script(marker, echo="cleaned")],
        cwd=repo_path,
        check=True,
    )
    (repo_path / "a.txt").write_text("two\n")

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_migrated_read(server, workspace_id)

    assert await _marker_exists_in_container(server, workspace_id, marker)
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert "a.txt" in status.structured_content["unstaged"]


async def test_an_arbitrary_unlisted_driver_name_now_executes_too(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """Pre-H36, HR-002's neutralization had to be dynamic discovery, not
    a static list — a driver named something nobody anticipated was
    neutralized exactly the same way it discovered any other. Post-H36
    there is no discovery mechanism at all to have missed one: nothing
    is neutralized, by name or otherwise, so an unlisted driver name
    executes exactly as readily as `diff.external` does."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    marker = "/tmp/ARBITRARY_NAME_RAN"
    (repo_path / ".gitattributes").write_text(
        "a.txt filter=totally-unexpected-driver-name-xyz123\n"
    )
    subprocess.run(
        [
            "git",
            "config",
            "filter.totally-unexpected-driver-name-xyz123.clean",
            _driver_script(marker, echo="surprise"),
        ],
        cwd=repo_path,
        check=True,
    )
    (repo_path / "a.txt").write_text("two\n")

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await _call_every_migrated_read(server, workspace_id)

    assert await _marker_exists_in_container(server, workspace_id, marker)


async def test_at_025_fsmonitor_now_executes_in_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-025 (`docs/SPEC.md`), superseded by H36: `core.fsmonitor` — not
    `core.hooksPath`, this test's own pre-H36 title notwithstanding; no
    real Git hook fires on a plain `status`/`diff`/`log` at all,
    `core.fsmonitor` is the one hook-*like* mechanism that does — was
    previously overridden unconditionally on every host-side read
    (`-c core.fsmonitor=`, §18.7). An in-container `git_status` now runs
    it, as the container user, the same as `exec("git status")` already
    could."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    marker = "/tmp/FSMONITOR_RAN"
    subprocess.run(
        ["git", "config", "core.fsmonitor", _driver_script(marker, echo="fsmonitor")],
        cwd=repo_path,
        check=True,
    )

    server = create_server(config)
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    await server.call_tool("git_status", {"workspace_id": workspace_id})

    assert await _marker_exists_in_container(server, workspace_id, marker)
