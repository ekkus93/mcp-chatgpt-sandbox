from pathlib import Path


tools_path = Path("docs/TOOLS.md")
tools = tools_path.read_text()
tools = tools.replace("24 tools total as of O10", "24 tools total as of O12", 1)
tools = tools.replace(
    "Dry-run-only immutable reclamation planning (PMOR-006..007, PMOR-010).",
    "Dry-run-only immutable reclamation planning (PMOR-006..007, PMOR-010, PMOR-012).",
    1,
)
tools = tools.replace(
    "Planning performs no stop/kill/remove/prune action; raw SSH boot/process-start and cleanup device/inode identity remain plan-internal, while the public preconditions report only whether cleanup identity is bound. |",
    "Planning performs no stop/kill/remove/prune action; raw SSH boot/process-start and cleanup device/inode identity remain plan-internal, while the public preconditions report only whether cleanup identity is bound. Plan creation emits path-safe audit metadata containing the plan ID plus opaque candidate IDs/kinds, bounded reasons, and `planned` decisions. |",
    1,
)
tools = tools.replace(
    "Explicit plan-scoped reclamation execution (PMOR-008..010).",
    "Explicit plan-scoped reclamation execution (PMOR-008..010, PMOR-012).",
    1,
)
tools = tools.replace(
    "If SSH-agent primary reclamation succeeds but socket cleanup fails, the primary failure context is preserved as a failed candidate while path-free durable cleanup debt remains discoverable, byte-accounted, and safely retryable by the original plan or a fresh cleanup-debt plan. |",
    "If SSH-agent primary reclamation succeeds but socket cleanup fails, the primary failure context is preserved as a failed candidate while path-free durable cleanup debt remains discoverable, byte-accounted, and safely retryable by the original plan or a fresh cleanup-debt plan. Execution audit metadata records only the plan ID, confirmation/aggregate state, opaque candidate IDs/kinds, decisions/outcomes, reclaimed bytes where known, and allowlisted/sanitized failure classifications; arbitrary host paths, secrets, Docker credentials, SSH key material, and raw environment values are not copied into audit output. |",
    1,
)
tools_path.write_text(tools)

spec_path = Path("docs/POST_MVP_OPERATIONS_SPEC.md")
spec = spec_path.read_text()
spec = spec.replace(
    "**Status:** proposed next workstream",
    "**Status:** complete — O1-O12 implemented and verified",
    1,
)
spec_path.write_text(spec)

todo_path = Path("docs/POST_MVP_OPERATIONS_TODO.md")
todo = todo_path.read_text()
todo = todo.replace(
    "**Status:** open — O1-O11 complete; O12 remains",
    "**Status:** complete — O1-O12 complete",
    1,
)
marker = "### O12 — Audit reclamation and close the workstream"
prefix, tail = todo.split(marker, 1)
tail = tail.replace("- [ ]", "- [x]")
evidence_anchor = "- [x] Mark this TODO complete only after exact-head CI passes\n\n---"
evidence = """- [x] Mark this TODO complete only after exact-head CI passes

**O12 completion evidence:**

- PMOAT-012 clean acceptance RED: Actions run `34012778089`, job `101431325149`, on acceptance-only head `d74321e27dae3e4d82e34597e65a4df76ef2285d` passed Docker build, Ruff format/lint, mypy (`72 source files`), MCP contract, contract-version, and docs drift, then failed exactly the new O12 acceptance case while the O11 baseline remained green (`1 failed, 1183 passed in 622.85s`). The only audit operation present was `resource_prune_plan`; `resource_prune_execute` emitted no audit event. This is the direct pre-feature/revert proof for PMOR-012.
- Implementation: plan audit metadata now records the immutable plan ID plus opaque candidate IDs/kinds, a bounded allowlisted/fallback reason, and `planned` decision. Execute audit metadata records the same plan ID, confirmation and aggregate state, per-candidate decision/outcome, and reclaimed bytes where known. Free-form execution failure text is never copied into audit metadata: known safe classifications are allowlisted and every other value becomes `failure_detail_redacted`.
- PMOAT-012 redaction acceptance drives the real MCP `resource_prune_plan` / `resource_prune_execute` tool boundary with a mixed result containing both a GitHub-token-shaped secret and `/home/phil/.ssh/id_rsa`; neither appears in audit JSONL. The reclaimed candidate records 17 known bytes and the failed candidate records only `failure_detail_redacted`, with no arbitrary host path.
- Broad-prune closure audit: the execution service remains exact-plan/exact-identity only and explicitly states that no label-wide, prefix-wide, path-wide, or system-wide fallback exists. Repository searches found no `containers.prune`, `volumes.prune`, or `images.prune` execution API. Remaining `docker system prune` text is prohibition/specification language or historical examples of out-of-band deletion, not an MCP cleanup fallback.
- Citation/TODO audit: O1-O12 task headers collectively cover PMOR-001..PMOR-012 and PMOAT-001..PMOAT-012; O12 is explicitly `PMOR-012` / `PMOAT-012`. All O1-O12 task checkboxes and final definition-of-done items are closed in this generation.
- Foreign-resource closure evidence: the complete O12 implementation suite includes the existing PMOAT-009 real-Docker destructive acceptance under GAP3 Y15 (`tests/workspace/test_post_mvp_o8_prune_execution.py`), which passed while its foreign lookalike container/volume sentinels remained outside the authorized plan. O12 adds audit behavior only and does not broaden deletion scope.
- Public contract remains unchanged: O12 adds audit metadata only, not an MCP tool or response field. The tool count remains 24 and `contract_version` remains `0.10.2`; schema export, contract-version, and docs-drift checks all pass.
- Superseded implementation run `34013769642` stopped only at Ruff formatting before lint/mypy/contracts/pytest. The follow-up changed one blank line and no O12 semantics.
- Final implementation GREEN: Actions run `34013939043`, job `101434386535`, on exact implementation head `6d1c3a1d87ce9c7746b91958bb9a5c15d184bf82` passed Docker build, Ruff format/lint, mypy (`72 source files`), MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1184 passed in 618.06s` with 92% overall coverage. PMOAT-012 passes in this generation.
- Revert/pre-feature verification is the clean `34012778089` RED: removing O12 restores exactly the missing execute-audit failure while the prior 1183-test O11 baseline remains green.

---"""
assert evidence_anchor in tail
tail = tail.replace(evidence_anchor, evidence, 1)
todo_path.write_text(prefix + marker + tail)
