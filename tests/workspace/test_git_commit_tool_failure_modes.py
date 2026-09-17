"""S22: `git_commit` through the real MCP tool layer — the failure-mode
half of the suite, split from `test_git_commit_tool.py` (originally 1066
lines) so no single file holds both halves at once; see that file's own
module docstring for the ordinary-operation half (creation,
direct-callability, HEAD/branch guards, strict mode, policy).

Here: preconditions against an already-conflicting repository (interrupted
merge, a stash-pop conflict marker files miss), pathspec-injection
protection (a magic pathspec, a root-normalizing path), `files_committed`
accuracy, and the failed-commit index-rollback contract (RR-005/HR-025/
AR-001) — the `git_commit`s that are *supposed* to fail, and what SHALL
be true of repository state afterward when they do.
"""

import os
import subprocess
from pathlib import Path

import docker
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server


def _configured(
    minimal_config: ServerConfig, default_image_tag: str, repo_path: Path
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
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


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_git_commit_interrupted_merge_fails_closed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("feature change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feature change"], cwd=repo_path, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("main change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main change"], cwd=repo_path, check=True)
    subprocess.run(["git", "merge", "-q", "feature"], cwd=repo_path, check=False)

    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["README.md"]},
        )


async def test_at_hr023_a_stash_pop_conflict_fails_closed_though_it_leaves_no_marker_file(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-023 (`docs/HARDENING_SPEC.md` §11): `detect_interrupted_operation`
    alone doesn't catch this — a `git stash pop` conflict leaves no
    `MERGE_HEAD` (confirmed empirically: only an `AUTO_MERGE` file
    appears, which nothing in `detect_interrupted_operation` reads) even
    though `git status` reports the same `UU` unresolved-conflict state
    an ordinary `git merge` conflict does. The precondition SHALL inspect
    `status.conflicted` directly, not only marker files."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    (repo_path / "README.md").write_text("line1-stash-change\n")
    subprocess.run(["git", "stash", "-q"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("line1-main-change\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main change"], cwd=repo_path, check=True)
    subprocess.run(["git", "stash", "pop"], cwd=repo_path, check=False)  # expected to conflict
    assert not (repo_path / ".git" / "MERGE_HEAD").exists()  # confirms no marker file exists

    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_CONFLICT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["README.md"]},
        )


async def test_at_hr024_a_magic_pathspec_does_not_stage_the_whole_repository(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-024 (`docs/HARDENING_SPEC.md` §11): a real regression —
    `paths=[":/"]` used to reach `git add` unprefixed, and `:/` is Git's
    own pathspec syntax for "the entire repository root", staging (and
    then committing) every changed file rather than failing the way a
    genuinely nonexistent literal path named ":/" should."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "b.txt", "content": "b\n"}
    )

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": [":/"]},
        )

    # Nothing was staged or committed — both files are still untracked.
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert set(status.structured_content["untracked"]) == {"a.txt", "b.txt"}
    assert status.structured_content["staged"] == []


@pytest.mark.parametrize("root_path", ["", ".", "./"])
async def test_rr004_an_empty_or_root_normalizing_path_is_rejected_before_staging(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
    root_path: str,
) -> None:
    """RR-004 (`docs/REMEDIATION_SPEC.md` §5): `paths=[""]` is a non-empty
    *list*, so it passed the `if not paths:` check untouched, and reached
    `git add -- ':(literal)'` — reproduced empirically against real git
    2.43 that this stages the *entire* working tree, not "no such file"
    the way a genuinely nonexistent literal path should fail. `"."` and
    `"./"` normalize to the same repository-root pathspec."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "b.txt", "content": "b\n"}
    )

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": [root_path]},
        )

    # Nothing was staged or committed — both files are still untracked.
    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert set(status.structured_content["untracked"]) == {"a.txt", "b.txt"}
    assert status.structured_content["staged"] == []


async def test_rr004_a_root_normalizing_path_mixed_with_a_real_path_is_still_rejected(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The check must inspect every entry, not just reject a lone one —
    a caller who names one real path and one root-normalizing path should
    not have the real path's presence mask the dangerous one."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "x", "paths": ["a.txt", ""]},
        )

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["staged"] == []


async def test_at_hr024_files_committed_reports_what_git_actually_committed(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """`files_committed` SHALL report what Git actually committed, not
    the request echo — a directory path is the clearest case where the
    two differ: the request names one path, but the commit touches every
    file beneath it."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "dir/a.txt", "content": "a\n"}
    )
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "dir/b.txt", "content": "b\n"}
    )

    result = await server.call_tool(
        "git_commit",
        {"workspace_id": workspace_id, "message": "add dir", "paths": ["dir"]},
    )

    assert sorted(result.structured_content["files_committed"]) == ["dir/a.txt", "dir/b.txt"]


async def test_at_hr025_a_failed_commit_leaves_the_index_unchanged(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """HR-025 (`docs/HARDENING_SPEC.md` §12): §20.6 requires a failed
    operation to leave repository state unmodified — `git add` SHALL be
    rolled back when the subsequent `git commit` fails. Reproduced via a
    rejecting pre-commit hook (the same mechanism
    `test_git_commit_no_verify_skips_hooks` uses to fail a commit for a
    real reason, not a "nothing changed" no-op)."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)
    server = create_server(config)
    workspace_id = await _open(server)
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "a\n"}
    )

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["a.txt"]}
        )

    status = await server.call_tool("git_status", {"workspace_id": workspace_id})
    assert status.structured_content["staged"] == []
    assert status.structured_content["untracked"] == ["a.txt"]  # git add was rolled back


async def test_rr005_a_failed_commit_restores_prior_staged_content_not_head(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """RR-005 (`docs/REMEDIATION_SPEC.md` §5): the old HR-025 rollback
    (`git reset -- <paths>`) resets to HEAD — reproduced empirically that
    this silently destroys any content staged *before* this call ran,
    whenever that differs from HEAD. This test distinguishes all three
    states a naive rollback could land on: HEAD's original content, the
    content staged before this call (what the restore SHALL recover),
    and the content this call's own `git add` staged over top of it
    (what the restore SHALL discard)."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    # HEAD: "v1" — committed *before* the rejecting hook exists, or this
    # setup commit would be blocked by it too.
    await server.call_tool(
        "write_file", {"workspace_id": workspace_id, "path": "a.txt", "content": "v1\n"}
    )
    add_v1 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "add", "a.txt"]}
    )
    assert add_v1.structured_content["exit_code"] == 0
    commit_v1 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "commit", "-m", "v1"]}
    )
    assert commit_v1.structured_content["exit_code"] == 0

    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)

    # Staged before git_commit_tool ever runs — distinct from HEAD. This
    # is the state the restore SHALL recover.
    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "a.txt", "content": "v2-prior-staged\n"},
    )
    add_v2 = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["git", "add", "a.txt"]}
    )
    assert add_v2.structured_content["exit_code"] == 0

    # Not yet staged — git_commit_tool's own `git add` is what stages
    # this. The restore SHALL discard it, not leave it staged.
    await server.call_tool(
        "write_file",
        {"workspace_id": workspace_id, "path": "a.txt", "content": "v3-this-calls-own-add\n"},
    )

    with pytest.raises(ToolError, match="COMMAND_FAILED"):
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["a.txt"]}
        )

    staged = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "git show :a.txt"}
    )
    assert staged.structured_content["stdout_tail"] == "v2-prior-staged\n"


async def test_at_aat001_a_non_utf8_path_present_only_pre_add_is_not_restored_as_a_phantom(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AR-001 site 1 (`docs/AUDIT_SPEC.md` §4), isolated from site 2.

    `git_commit`'s rollback is nested inside *two* lossy-decode gates (the
    pre-`add` capture that produces `prior_entries`, and the post-`add`
    re-capture that produces `current_entries`) — `build_restore_index_argv
    (prior_entries)` only ever runs if *both* gates pass. A fixture where
    the bad path is present in *both* captures trips both gates at once and
    proves nothing about site 1 alone (confirmed empirically while writing
    this: reverting only site 1's guard left such a test passing, because
    site 2's still caught the same corrupted path first).

    Isolating site 1 needs a fixture only the *pre*-`add` capture sees: a
    non-UTF-8 path already tracked, then deleted from the working tree
    before this call — so this call's own `git add` stages the deletion,
    and the *post*-`add` capture no longer contains it at all. Site 2's
    gate is then irrelevant (nothing left to decode lossily); only site
    1's guard stands between `prior_entries` and a corrupted restore."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    many_dir = repo_path / "many"
    many_dir.mkdir()
    bad_path = many_dir / os.fsdecode(b"bad\xffname.txt")
    bad_path.write_bytes(b"v1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add bad"], cwd=repo_path, check=True)

    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)

    # Still tracked (present pre-add), but gone from the working tree —
    # this call's own `git add -- many` stages the removal, so it is
    # absent from the post-add capture entirely.
    bad_path.unlink()

    with pytest.raises(ToolError, match="COMMAND_FAILED") as excinfo:
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["many"]}
        )
    assert '"index_left_dirty": true' in str(excinfo.value)

    index = subprocess.run(
        ["git", "ls-files", "-z", "--stage"], cwd=repo_path, capture_output=True
    ).stdout
    entries = [e for e in index.split(b"\0") if e]
    assert not any(b"bad" in e for e in entries), (
        f"no phantom restore of the deleted path: {entries}"
    )


async def test_at_aat003_a_new_non_utf8_path_the_calls_own_add_stages_is_left_honestly_dirty(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AR-001 site 2 / AR-003 (`docs/AUDIT_SPEC.md` §4): the post-`add`
    re-capture's `extra_paths` feeds its own write-back, `git rm --cached
    --ignore-unmatch` — independent of site 1's, and needs a fixture site
    1's capture cannot see: a path that is *untracked* until this call's
    own `git add` stages it, so the pre-`add` capture is clean and only
    the post-`add` one decodes lossily. Old unguarded behavior: the
    corrupted path matches nothing, `--ignore-unmatch` exits 0, and
    `index_left_dirty` is reported `false` even though the file is still
    staged."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    many_dir = repo_path / "many"
    many_dir.mkdir()
    (many_dir / "clean.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add clean"], cwd=repo_path, check=True)

    hooks_dir = repo_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks_dir / "pre-commit").chmod(0o755)

    # Untracked until this call's own `git add` stages it — the pre-`add`
    # capture (site 1) sees only clean.txt and decodes cleanly.
    new_path = many_dir / os.fsdecode(b"new\xffname.txt")
    new_path.write_bytes(b"new\n")

    with pytest.raises(ToolError, match="COMMAND_FAILED") as excinfo:
        await server.call_tool(
            "git_commit", {"workspace_id": workspace_id, "message": "blocked", "paths": ["many"]}
        )
    assert '"index_left_dirty": true' in str(excinfo.value)


async def test_at_hr025_nothing_to_commit_maps_to_a_specific_error_code(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """The second HR-025 requirement: "nothing to commit" (the paths
    named are real and already committed, with no actual change) gets
    its own stable, matchable code rather than the generic
    `COMMAND_FAILED` every other commit failure reports."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)
    server = create_server(config)
    workspace_id = await _open(server)

    with pytest.raises(ToolError, match="GIT_NOTHING_TO_COMMIT"):
        await server.call_tool(
            "git_commit",
            {"workspace_id": workspace_id, "message": "no-op", "paths": ["README.md"]},
        )


async def test_at_vat001_a_line_cropped_index_snapshot_does_not_crash_the_commit(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """VR-001 (`docs/REVIEW_SPEC.md` §4): `git_commit`'s pre-`add` index
    snapshot is read through `ExecutionService.consume`, which crops
    `stdout_tail` to the last `response_tail_lines` (200) **lines**.
    `ExecResult.stdout_truncated` reflects only the *byte* cap, so it is
    `False` while the data is already line-cropped, and
    `parse_index_entries` — called unconditionally, before this call's
    own `git add` even runs — raises an untranslated `ValueError` on the
    cropped fragment.

    More than `response_tail_lines` newline-bearing paths, already
    staged, reproduce it: `git ls-files -z --stage` embeds a literal
    newline inside each such path (git does not escape `-z` output —
    R4's stated "no newlines to split on" rationale was wrong), so 220
    such entries already exceed 200 *lines* well before 200 *entries*."""
    repo_path = tmp_path / "work" / "example"
    config = _configured(minimal_config, default_image_tag, repo_path)

    many_dir = repo_path / "many"
    many_dir.mkdir()
    for i in range(220):
        (many_dir / f"a\nb_{i}.txt").write_text("x")
    subprocess.run(["git", "add", "-A", "--", ":(literal)many"], cwd=repo_path, check=True)

    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "git_commit",
        {
            "workspace_id": workspace_id,
            "message": "add many newline-bearing paths",
            "paths": ["many"],
        },
    )
    assert result.structured_content["commit_sha"] is not None
    assert len(result.structured_content["files_committed"]) == 220
