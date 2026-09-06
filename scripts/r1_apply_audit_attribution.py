from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}")
    if text.count(old) != 1:
        raise RuntimeError(f"expected text appears {text.count(old)} times in {path}")
    path.write_text(text.replace(old, new, 1))


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def patch_audit() -> None:
    path = ROOT / "src/dev_workspace_mcp/audit.py"
    replace_once(
        path,
        "designed to avoid for its own scope. `request_id`/`session_id` are\nalways `None`: nothing in the codebase threads either through to a tool\ncall yet.\n",
        "designed to avoid for its own scope. Request/session attribution is\noptional at this layer: callers that can provide bounded, non-secret\ncorrelation values thread them through explicitly, while older callers\ncontinue to write `None`.\n",
    )
    replace_once(path, "import os\nimport sys\n", "import os\nimport re\nimport sys\n")
    replace_once(
        path,
        "_AUDIT_HEALTH_LOCK = threading.Lock()\n_AUDIT_HEALTH_BY_PATH: dict[str, AuditHealth] = {}\n\n\ndef _audit_health_key(config: AuditConfig) -> str:\n",
        "_AUDIT_HEALTH_LOCK = threading.Lock()\n_AUDIT_HEALTH_BY_PATH: dict[str, AuditHealth] = {}\n_AUDIT_CORRELATION_ID_RE = re.compile(r\"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$\")\n\n\ndef _safe_audit_correlation_id(value: str | None) -> str | None:\n    \"\"\"Return a bounded audit correlation ID, or drop unsafe values.\n\n    PMORR-001: audit attribution is useful only when it is also safe to\n    persist. The audit writer therefore accepts only short opaque\n    identifiers made from this project's non-path ID alphabet. Raw\n    headers, prompts, environment values, paths, and free-form client\n    metadata fail closed to `None` instead of being copied into JSONL.\n    \"\"\"\n    if value is None:\n        return None\n    if _AUDIT_CORRELATION_ID_RE.fullmatch(value) is None:\n        return None\n    if \"/\" in value or \\\"\\\\\\\" in value:\n        return None\n    return value\n\n\ndef _audit_health_key(config: AuditConfig) -> str:\n",
    )
    replace_once(
        path,
        "    result: str,\n    workspace_id: str | None = None,\n    repository_id: str | None = None,\n    metadata: dict[str, Any] | None = None,\n) -> None:\n",
        "    result: str,\n    workspace_id: str | None = None,\n    repository_id: str | None = None,\n    request_id: str | None = None,\n    session_id: str | None = None,\n    metadata: dict[str, Any] | None = None,\n) -> None:\n",
    )
    replace_once(
        path,
        "    written, the same second layer `to_tool_error` applies to a client\n    response.\n",
        "    written, the same second layer `to_tool_error` applies to a client\n    response. `request_id` and `session_id` are optional PMORR-001\n    attribution fields and are accepted only when they are bounded,\n    opaque, non-path correlation IDs.\n",
    )
    replace_once(
        path,
        "        \"timestamp\": datetime.now(UTC).isoformat(),\n        \"request_id\": None,\n        \"session_id\": None,\n",
        "        \"timestamp\": datetime.now(UTC).isoformat(),\n        \"request_id\": _safe_audit_correlation_id(request_id),\n        \"session_id\": _safe_audit_correlation_id(session_id),\n",
    )


def patch_prune() -> None:
    path = ROOT / "src/dev_workspace_mcp/tools/prune.py"
    replace_once(path, "import re\nimport sqlite3\n", "import re\nimport sqlite3\nimport uuid\n")
    replace_once(
        path,
        "\ndef _audit_plan_reason(candidate: PruneCandidate) -> str:\n",
        "\ndef _new_audit_request_id() -> str:\n    \"\"\"Return a bounded, opaque per-tool-call audit attribution ID.\"\"\"\n    return f\"rq_{uuid.uuid4().hex}\"\n\n\ndef _audit_plan_reason(candidate: PruneCandidate) -> str:\n",
    )
    replace_once(
        path,
        "            operation=\"resource_prune_plan\",\n            result=\"success\",\n            repository_id=request.repository_id,\n",
        "            operation=\"resource_prune_plan\",\n            result=\"success\",\n            request_id=_new_audit_request_id(),\n            repository_id=request.repository_id,\n",
    )
    replace_once(
        path,
        "            operation=\"resource_prune_execute\",\n            result=result.status.value,\n            metadata={\n",
        "            operation=\"resource_prune_execute\",\n            result=result.status.value,\n            request_id=_new_audit_request_id(),\n            metadata={\n",
    )


def patch_tests() -> None:
    path = ROOT / "tests/test_audit.py"
    replace_once(
        path,
        "    assert event[\"session_id\"] is None\n    assert \"timestamp\" in event\n\n\ndef test_write_audit_event_appends_rather_than_overwrites(tmp_path: Path) -> None:\n",
        "    assert event[\"session_id\"] is None\n    assert \"timestamp\" in event\n\n\ndef test_write_audit_event_accepts_only_safe_correlation_ids(tmp_path: Path) -> None:\n    config = AuditConfig(enabled=True, path=tmp_path / \"audit.jsonl\")\n    write_audit_event(\n        config,\n        operation=\"safe\",\n        result=\"success\",\n        request_id=\"rq_\" + \"a\" * 32,\n        session_id=\"session:alpha-1\",\n    )\n    write_audit_event(\n        config,\n        operation=\"unsafe\",\n        result=\"success\",\n        request_id=\"/home/phil/.ssh/id_rsa\",\n        session_id=\"Bearer ghp_\" + \"A\" * 36,\n    )\n\n    safe, unsafe = _read_lines(config.path)\n    assert safe[\"request_id\"] == \"rq_\" + \"a\" * 32\n    assert safe[\"session_id\"] == \"session:alpha-1\"\n    assert unsafe[\"request_id\"] is None\n    assert unsafe[\"session_id\"] is None\n\n\ndef test_write_audit_event_appends_rather_than_overwrites(tmp_path: Path) -> None:\n",
    )


def cleanup_self() -> None:
    for relative in [
        ".github/workflows/r1-apply-audit-attribution.yml",
        "scripts/r1_apply_audit_attribution.py",
    ]:
        target = ROOT / relative
        if target.exists():
            target.unlink()


def main() -> None:
    patch_audit()
    patch_prune()
    patch_tests()
    cleanup_self()
    run("python", "-m", "compileall", "-q", "src", "tests/test_audit.py", "tests/tools/test_post_mvp_review_remediation_audit_attribution.py")
    run("git", "config", "user.name", "Phillip Chin")
    run("git", "config", "user.email", "ekkus93@gmail.com")
    run("git", "add", "src/dev_workspace_mcp/audit.py", "src/dev_workspace_mcp/tools/prune.py", "tests/test_audit.py", ".github/workflows/r1-apply-audit-attribution.yml", "scripts/r1_apply_audit_attribution.py")
    run("git", "commit", "-m", "feat: add R1 reclamation audit attribution")
    run("git", "push", "origin", "HEAD:post-mvp/review-remediation")


if __name__ == "__main__":
    main()
