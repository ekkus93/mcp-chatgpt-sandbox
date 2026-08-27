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

from dev_workspace_mcp.config.models import RepositoryConfig, ServerConfig
from dev_workspace_mcp.db import open_db
from dev_workspace_mcp.server import create_server


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
