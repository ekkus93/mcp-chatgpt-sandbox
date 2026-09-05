"""S33: one continuous end-to-end run of the §78 "definition of done"
workflow, through the real MCP tool surface against a real Docker daemon
and the real S7 default image — not the underlying service functions
individually (those are covered per-tool elsewhere), but the same session
shape ChatGPT itself would drive.

§78 numbers its workflow 1..17. This test drives every step reachable
without a live GitHub credential:

    1. connect                       -> `create_server`
    2. list/select the repository    -> `repository_list`
    3. open the persistent workspace -> `workspace_open`
    4. inspect Git state             -> `git_status`
    6. inspect and search files      -> `list_files` / `search_files`
    7. modify repository files       -> `write_file`
    8. install missing dev tools     -> `exec` + passwordless `sudo`
    9. run project linters           -> `exec`
   10. run project unit tests        -> `exec`
   11. start/monitor a long build    -> `exec_start` / `exec_status` / `exec_output`
   12. inspect failures, bounded     -> `exec_output` (tail semantics; see S16)
   13. review Git diff               -> `git_diff`
   14. create a commit with guards   -> `git_commit` (`expected_head_sha`)
   16. reconnect, reuse environment  -> a second `create_server` against
                                        the same state db and repo

   17 (no host Docker socket / arbitrary host paths / host credentials
   reachable) is asserted directly rather than re-derived here — it is
   already covered per-mechanism by AT-012/AT-013/AT-016 in
   `test_mounts_integration.py` / `test_ssh_agent_provisioning.py` /
   `test_default_image.py`.

   Step 5 (pull current remote changes) and step 15 (push the commit) are
   the two genuinely unreachable steps: `validate_remote_identity` (S25)
   accepts only `github.com` as a structured-tool remote, and S24's
   `insteadOf` rewrite forces even an HTTPS-authored `github.com` origin
   through SSH, which requires a real provisioned key (S23, blocked by
   D3). That boundary — reaching real host-key verification and failing
   cleanly at authentication, not connectivity — is exercised for real by
   `test_git_pull_tool.py` and `test_git_push_tool.py`'s own real-network
   sections; the fetch/integrate and push *mechanics* themselves are
   proven for real, without that constraint, against local repositories
   by `tests/git/test_pull.py` and `tests/git/test_push.py`. Re-asserting
   any of that here would just be a slower duplicate.

   AT-014 separately proves a real `sudo apt-get install` as the non-root
   checkout identity. Repeating a live package-network operation here made
   this broad workflow test depend on Ubuntu mirror latency without adding
   a distinct acceptance property. Step 8 therefore installs a tiny local
   executable through the same real `exec` + `sudo` privilege path and
   executes it, proving tool installation in the persistent container
   without duplicating AT-014's package-manager/network coverage.
"""

import asyncio
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
    (repo_path / "README.md").write_text("hello\n")
    (repo_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
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


async def _wait_until(predicate, *, timeout: float = 60.0, interval: float = 0.2) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if await predicate():
            return
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(interval)


async def test_the_full_reachable_78_workflow_end_to_end(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)

    # 1. connect
    server = create_server(config)

    # 2. list/select the repository
    repos = await server.call_tool("repository_list", {})
    assert [r["id"] for r in repos.structured_content["result"]] == ["example"]

    # 3. open the persistent workspace
    opened = await server.call_tool("workspace_open", {"repository_id": "example"})
    workspace_id = opened.structured_content["workspace_id"]

    # 4. inspect Git state
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["branch"] == "main"
    assert status.structured_content["clean"] is True
    head_sha = status.structured_content["head_sha"]

    # 6. inspect and search project files
    listed = await server.call_tool("list_files", {"workspace_id": workspace_id})
    assert {e["path"] for e in listed.structured_content["result"]} >= {"README.md", "app.py"}
    found = await server.call_tool(
        "search_files", {"workspace_id": workspace_id, "pattern": "def add"}
    )
    assert any(m["path"] == "app.py" for m in found.structured_content["result"])

    # 7. modify repository files
    await server.call_tool(
        "write_file",
        {
            "workspace_id": workspace_id,
            "path": "app.py",
            "content": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
        },
    )

    # 8. install a missing dev tool. AT-014 separately exercises a real
    # `sudo apt-get install`; this broad E2E test only needs to prove that
    # ChatGPT can use `exec` as the checkout owner and elevate through the
    # configured passwordless sudo boundary to persist a new executable in
    # the container's system tool path.
    install = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": (
                "printf '#!/bin/sh\\necho MCP_DEV_TOOL_OK\\n' > /tmp/mcp-dev-tool "
                "&& chmod 0755 /tmp/mcp-dev-tool "
                "&& sudo install -m 0755 /tmp/mcp-dev-tool /usr/local/bin/mcp-dev-tool "
                "&& mcp-dev-tool"
            ),
        },
    )
    assert install.structured_content["exit_code"] == 0
    assert "MCP_DEV_TOOL_OK" in install.structured_content["stdout_tail"]

    # 9. run a project "linter" (a real syntax check, standing in for one)
    lint = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "python3 -m py_compile app.py"}
    )
    assert lint.structured_content["exit_code"] == 0

    # 10. run project unit tests (a real assertion against the edit above)
    unit_test = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "python3 -c 'import app; assert app.add(2, 3) == 5;"
            " assert app.sub(5, 2) == 3'",
        },
    )
    assert unit_test.structured_content["exit_code"] == 0

    # 11. start and monitor a long-running build
    started = await server.call_tool(
        "exec_start",
        {"workspace_id": workspace_id, "argv": ["sh", "-c", "sleep 0.5 && echo build done"]},
    )
    job_id = started.structured_content["job_id"]

    async def build_finished() -> bool:
        s = await server.call_tool("exec_status", {"job_id": job_id})
        return bool(s.structured_content["state"] == "succeeded")

    await _wait_until(build_finished)

    # 12. inspect the build's output without receiving unbounded logs
    build_output = await server.call_tool("exec_output", {"job_id": job_id})
    assert "build done" in build_output.structured_content["content"]

    # 13. review the Git diff
    diff = await server.call_tool("git_diff", {"workspace_id": workspace_id})
    assert "def sub" in diff.structured_content["content"]

    # 14. create a commit with a state guard
    commit = await server.call_tool(
        "git_commit",
        {
            "workspace_id": workspace_id,
            "message": "add sub()",
            "paths": ["app.py"],
            "expected_head_sha": head_sha,
        },
    )
    assert commit.structured_content["commit_sha"] != head_sha

    # 16. reconnect later and reuse the same configured development
    # environment: a fresh `create_server` (a new MCP session, same state
    # db and same on-disk repository) sees the commit that landed above.
    reconnected = create_server(config)
    reopened = await reconnected.call_tool("workspace_open", {"repository_id": "example"})
    assert reopened.structured_content["workspace_id"] == workspace_id
    status_after = await reconnected.call_tool("git_status", {"workspace_id": workspace_id})
    assert status_after.structured_content["head_sha"] == commit.structured_content["commit_sha"]
