"""S10: filesystem tools end to end, through real MCP tool calls
(§13, AT-003, AT-018) — not just the underlying service functions.

`MCPServer.call_tool` (the high-level convenience method used here) raises
the SDK's `ToolError` on failure rather than returning a result with
`is_error=True` — that shape belongs to the lower-level tool-manager API.
Error-path tests below use `pytest.raises` accordingly.
"""

from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import FilesystemConfig, RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.db import open_db
from mcp_chatgpt_sandbox.filesystem.service import FilesystemService
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.server import create_server
from mcp_chatgpt_sandbox.tools import filesystem as filesystem_tools


def _workspace_with_repo(config: ServerConfig, repo_path: Path) -> tuple[ServerConfig, str]:
    """S11 (`workspace_open`) doesn't exist yet — insert the rows a real
    workspace_open would have produced directly, matching S6's schema."""
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    config = config.model_copy(
        update={
            "repositories": [RepositoryConfig(id="example", path=repo_path, github="owner/example")]
        }
    )
    db = open_db(config.state_db_path)
    db.execute(
        "INSERT INTO repository (id, canonical_path) VALUES (?, ?)",
        ("example", str(repo_path.resolve())),
    )
    db.execute(
        """
        INSERT INTO workspace
            (id, repository_id, mode, workspace_path, state, created_at, last_used_at)
        VALUES ('ws_example', 'example', 'persistent', '/workspace', 'running', '', '')
        """
    )
    db.commit()
    return config, "ws_example"


async def test_at_003_write_then_read_round_trips_through_real_tool_calls(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    server = create_server(config)

    write_result = await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "hello.txt", "content": "hi"}
    )

    # AT-003: appears immediately in the host checkout.
    assert (tmp_path / "work" / "example" / "hello.txt").read_text() == "hi"

    read_result = await server.call_tool(
        "read_file", {"workspace_id": workspace_id, "path": "hello.txt"}
    )
    assert read_result.structured_content["content"] == "hi"
    assert read_result.structured_content["sha256"] == write_result.structured_content["sha256"]


async def test_at_018_stale_expected_sha_is_a_tool_error(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    server = create_server(config)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "f.txt", "content": "one"}
    )
    with pytest.raises(ToolError, match="FILE_CONFLICT"):
        await server.call_tool(
            "write_file",
            {
                "workspace_id": workspace_id,
                "path": "f.txt",
                "content": "two",
                "expected_sha256": "0" * 64,
            },
        )
    # Rejected write must not have touched the file.
    assert (tmp_path / "work" / "example" / "f.txt").read_text() == "one"


async def test_list_files_tool(minimal_config: ServerConfig, tmp_path: Path) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "a.txt").write_text("x")
    server = create_server(config)

    result = await server.call_tool("list_files", {"workspace_id": workspace_id})
    paths = {e["path"] for e in result.structured_content["result"]}
    assert paths == {"a.txt"}


async def test_patch_file_tool(minimal_config: ServerConfig, tmp_path: Path) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "f.txt").write_text("one\ntwo\nthree\n")
    server = create_server(config)

    result = await server.call_tool(
        "patch_file",
        {
            "workspace_id": workspace_id,
            "path": "f.txt",
            "diff": "@@ -2,1 +2,1 @@\n-two\n+TWO\n",
        },
    )
    assert result.structured_content["applied"] is True
    assert (tmp_path / "work" / "example" / "f.txt").read_text() == "one\nTWO\nthree\n"


async def test_rr027_run_patch_file_is_directly_callable_without_a_server(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """R27 (`docs/REMEDIATION_TODO.md`): `run_patch_file` — the
    module-level extraction of `patch_file`'s body, the one tool body in
    this module with real inline logic — called directly, with no
    `MCPServer`/`create_server`/`server.call_tool` anywhere in the
    chain, only its real dependencies (a real `sqlite3.Connection` with
    the workspace/repository rows `_workspace_with_repo` inserts,
    `RepositoryRegistry`) constructed directly. No Docker needed at all
    — this tool body is host-side, unlike every other module this task
    extracted (workspace/git/execution), which is exactly why it's the
    cheapest of the four to prove."""
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "f.txt").write_text("one\ntwo\nthree\n")
    db = open_db(config.state_db_path)
    registry = RepositoryRegistry(config)

    result = filesystem_tools.run_patch_file(
        workspace_id,
        "f.txt",
        "@@ -2,1 +2,1 @@\n-two\n+TWO\n",
        None,
        db=db,
        registry=registry,
        filesystem_config=config.filesystem,
        repository_roots=config.repository_roots,
    )

    assert result.applied is True
    assert (tmp_path / "work" / "example" / "f.txt").read_text() == "one\nTWO\nthree\n"


# --- HR-004/HR-005 (docs/HARDENING_SPEC.md §5): patch_file cannot destroy data -------


async def test_at_hat004_patch_file_refuses_a_truncated_read_rather_than_destroying_the_file(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """A real regression, reproduced exactly this way during the
    2026-08-27 review: with `max_read_bytes` below a file's size,
    `patch_file` used to write the truncated read back over the whole
    file and report `{'applied': True}`. With a 2000-byte file and a
    50-byte limit, 1950 bytes were silently destroyed."""
    config = minimal_config.model_copy(update={"filesystem": FilesystemConfig(max_read_bytes=50)})
    config, workspace_id = _workspace_with_repo(config, tmp_path / "work" / "example")
    big_file = tmp_path / "work" / "example" / "big.txt"
    original_content = "line\n" * 400  # 2000 bytes
    big_file.write_text(original_content)
    server = create_server(config)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "patch_file",
            {
                "workspace_id": workspace_id,
                "path": "big.txt",
                "diff": "@@ -1,1 +1,1 @@\n-line\n+LINE\n",
            },
        )

    assert big_file.read_text() == original_content


async def test_at_hat004_max_patch_bytes_is_enforced(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """`filesystem.max_patch_bytes` was declared and never read — an
    unbounded diff payload was accepted regardless of the configured
    limit."""
    config = minimal_config.model_copy(update={"filesystem": FilesystemConfig(max_patch_bytes=20)})
    config, workspace_id = _workspace_with_repo(config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "a.txt").write_text("line\n")
    server = create_server(config)
    oversized_diff = "@@ -1,1 +1,1 @@\n-line\n+this replacement line is deliberately too long\n"
    assert len(oversized_diff.encode("utf-8")) > 20

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "patch_file",
            {"workspace_id": workspace_id, "path": "a.txt", "diff": oversized_diff},
        )

    assert (tmp_path / "work" / "example" / "a.txt").read_text() == "line\n"


async def test_patch_file_still_works_normally_below_both_limits(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """Regression guard: the two new checks must not interfere with an
    ordinary patch that is within both limits."""
    config = minimal_config.model_copy(
        update={"filesystem": FilesystemConfig(max_read_bytes=1024, max_patch_bytes=1024)}
    )
    config, workspace_id = _workspace_with_repo(config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "f.txt").write_text("one\ntwo\nthree\n")
    server = create_server(config)

    result = await server.call_tool(
        "patch_file",
        {"workspace_id": workspace_id, "path": "f.txt", "diff": "@@ -2,1 +2,1 @@\n-two\n+TWO\n"},
    )

    assert result.structured_content["applied"] is True
    assert (tmp_path / "work" / "example" / "f.txt").read_text() == "one\nTWO\nthree\n"


async def test_rr027_patch_file_expected_sha256_pass_through_rejects_a_concurrent_write(
    minimal_config: ServerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RR-027 (`docs/REMEDIATION_SPEC.md` §27): `patch_file`'s internal
    `svc.write_file(..., expected_sha256=current.sha256)` call — passing
    the sha256 it just read through as the write's own optimistic-
    concurrency guard, independent of whether the *client* supplied one
    (HR-004) — was implemented but unconstrained: nothing in the
    existing suite would fail if that pass-through were dropped, since
    no test's `patch_file` call ever races a concurrent write (unlike
    `test_at_018_stale_expected_sha_is_a_tool_error` above, which only
    exercises the *client*-supplied `expected_sha256` on a plain
    `write_file` call). Monkeypatches `FilesystemService.read_file` to
    make a real, independent modification to the file on disk right
    after it computes the real result but before returning it — the same
    observable effect a genuine concurrent writer landing in that window
    would have — then confirms `patch_file` refuses to silently overwrite
    it with a patch computed against the now-stale content."""
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    file_path = tmp_path / "work" / "example" / "f.txt"
    file_path.write_text("one\ntwo\nthree\n")
    server = create_server(config)

    real_read_file = FilesystemService.read_file

    def _racing_read_file(self: FilesystemService, *args: object, **kwargs: object) -> object:
        result = real_read_file(self, *args, **kwargs)  # type: ignore[arg-type]
        file_path.write_text("one\nCONCURRENTLY-CHANGED\nthree\n")
        return result

    monkeypatch.setattr(FilesystemService, "read_file", _racing_read_file)

    with pytest.raises(ToolError, match="FILE_CONFLICT"):
        await server.call_tool(
            "patch_file",
            {
                "workspace_id": workspace_id,
                "path": "f.txt",
                "diff": "@@ -2,1 +2,1 @@\n-two\n+TWO\n",
            },
        )

    # The concurrent writer's content survives untouched — not silently
    # overwritten by a patch computed against the stale read.
    assert file_path.read_text() == "one\nCONCURRENTLY-CHANGED\nthree\n"


async def test_delete_file_tool(minimal_config: ServerConfig, tmp_path: Path) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "f.txt").write_text("x")
    server = create_server(config)

    result = await server.call_tool("delete_file", {"workspace_id": workspace_id, "path": "f.txt"})
    assert result.structured_content["deleted"] is True
    assert not (tmp_path / "work" / "example" / "f.txt").exists()


async def test_at_hat003_delete_file_cannot_erase_the_repository_root(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    """A real regression, reproduced exactly this way during the
    2026-08-27 review: `delete_file(path="", recursive=True)` called
    through the published MCP tool contract removed the working tree and
    `.git` together and returned `{'deleted': True}`."""
    repo_path = tmp_path / "work" / "example"
    config, workspace_id = _workspace_with_repo(minimal_config, repo_path)
    (repo_path / "f.txt").write_text("keep me\n")
    server = create_server(config)

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "delete_file", {"workspace_id": workspace_id, "path": "", "recursive": True}
        )

    assert repo_path.exists()
    assert (repo_path / ".git").exists()
    assert (repo_path / "f.txt").exists()


async def test_search_files_tool(minimal_config: ServerConfig, tmp_path: Path) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    (tmp_path / "work" / "example" / "f.txt").write_text("needle in a haystack\n")
    server = create_server(config)

    result = await server.call_tool(
        "search_files", {"workspace_id": workspace_id, "pattern": "needle"}
    )
    matches = result.structured_content["result"]
    assert len(matches) == 1
    assert matches[0]["path"] == "f.txt"


async def test_unknown_workspace_id_is_a_tool_error(minimal_config: ServerConfig) -> None:
    server = create_server(minimal_config)
    with pytest.raises(ToolError, match="WORKSPACE_NOT_FOUND"):
        await server.call_tool("read_file", {"workspace_id": "ws_does-not-exist", "path": "f.txt"})


async def test_git_metadata_path_is_a_tool_error(
    minimal_config: ServerConfig, tmp_path: Path
) -> None:
    config, workspace_id = _workspace_with_repo(minimal_config, tmp_path / "work" / "example")
    server = create_server(config)

    with pytest.raises(ToolError, match="PATH_GIT_METADATA"):
        await server.call_tool(
            "write_file",
            {"workspace_id": workspace_id, "path": ".git/hooks/pre-commit", "content": "x"},
        )
    assert not (tmp_path / "work" / "example" / ".git" / "hooks").exists()
