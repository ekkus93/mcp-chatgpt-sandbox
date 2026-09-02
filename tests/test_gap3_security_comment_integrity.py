"""GAP3 Y17 source-comment integrity checks."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text()


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end)]


def test_g3at_017_touched_security_comments_remain_current_and_test_backed() -> None:
    """G3AT-017: security comments describe current, adversarially tested controls."""
    dockerfile = _read("docker/default/Dockerfile")
    wrapper = _read("docker/default/entrypoint-wrapper.py")
    workspace = _read("src/dev_workspace_mcp/tools/workspace.py")
    retention = _read("src/dev_workspace_mcp/jobs/retention.py")
    maintenance = _read("src/dev_workspace_mcp/maintenance.py")

    # X3/Y1: the production control is isolated Python startup, not merely
    # sanitization after Python has already started.
    assert "G3R-001 / G3AT-001" in dockerfile
    assert "This flag is load-bearing" in dockerfile
    assert "production control and is covered by G3AT-001" in wrapper

    # X14/Y6: only container inspect is runtime image provenance; resolving a
    # mutable configured tag is never described as an equivalent fallback.
    runtime_digest_comment = _between(
        workspace,
        "def _runtime_image_digest(",
        "async def build_workspace_info(",
    )
    normalized_runtime_comment = " ".join(runtime_digest_comment.split())
    assert "G3R-006 / G3AT-006" in normalized_runtime_comment
    assert "tag/reference is configuration, not runtime provenance" in normalized_runtime_comment
    assert "no tag-lookup fallback" in normalized_runtime_comment

    # Y7/Y14: cleanup and maintenance comments must describe observable
    # failure/debt and the scheduler that exists now, not pre-fix history.
    assert "G3R-007 / G3AT-007" in retention
    assert "G3R-014 / G3AT-014" in retention
    assert "G3R-014 / G3AT-014" in maintenance

    stale_claims = {
        "src/dev_workspace_mcp/tools/workspace.py": (
            "no scheduler exists in the daemon",
            "rather than on a real timer",
        ),
        "src/dev_workspace_mcp/jobs/retention.py": (
            "will put the same functions on a real periodic scheduler",
            "until then,",
        ),
        "src/dev_workspace_mcp/maintenance.py": ("existed before Y14",),
    }
    for path, phrases in stale_claims.items():
        source = _read(path)
        for phrase in phrases:
            assert phrase not in source, f"{path} still contains stale security history: {phrase!r}"
