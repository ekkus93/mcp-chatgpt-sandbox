# Post-CRH Code Review Fixes TODO

**Status:** in progress  
**Spec:** `docs/POST_CRH_CODE_REVIEW_FIXES_SPEC.md`  
**Base:** `86440755c84a817a534de60293cacb5c35795a65` (`docs: close CRH-005 and CRH-006 hardening workstream`)  
**Task series:** `PCRF-001`..`PCRF-004`  
**Validation series:** `PCRFV-001`..`PCRFV-004`

This TODO tracks the focused follow-up issues found during review of the completed Post-R8 Code Review Hardening workstream. It does not reopen CRH-001 through CRH-006. It fixes the top-level audit identifier token-shape gap, corrects stale final CRH evidence, restores important `errors.py` rationale, and records final verification.

## Global rules

- [x] Work from an exact inspected `master` SHA
- [x] Use a `ralph/...` source branch for all writes
- [ ] Add failing behavioral tests before fixing PCRF-001 where feasible
- [ ] Avoid literal real-looking secrets in committed tests and docs
- [ ] Preserve valid bounded opaque IDs
- [ ] Preserve existing generic redaction behavior for tool errors and audit metadata
- [ ] Avoid public MCP schema/output changes unless explicitly justified
- [ ] Bump `contract_version` only if public schema, registered descriptions, or response semantics change
- [ ] Preserve completed O1-O12 destructive-safety invariants
- [ ] Preserve completed PMORR R1-R8 guarantees
- [ ] Preserve completed CRH-001 through CRH-006 guarantees
- [ ] Do not introduce broad Docker cleanup, label-wide prune, prefix-wide prune, path-wide deletion, system-wide deletion, or `docker system prune`
- [ ] Run focused audit/redaction tests before full CI
- [ ] Run full Docker-backed CI on the exact final branch head
- [ ] Merge only through Ralph Bridge exact-head guarded PR merge
- [ ] Verify post-merge `master` CI
- [ ] Record exact evidence for every task

## Phase 0 — Planning docs

### PCRF-000 — Create follow-up spec and TODO

- [x] Add `docs/POST_CRH_CODE_REVIEW_FIXES_SPEC.md`
- [x] Add `docs/POST_CRH_CODE_REVIEW_FIXES_TODO.md`
- [x] Scope the workstream to issues found in the post-CRH code review
- [x] Keep validation labels in the `PCRFV-*` series
- [ ] Verify docs-only planning commit does not break CI or include unsafe literals
- [ ] Record exact planning evidence

**PCRF-000 evidence:**

- Base inspected: `86440755c84a817a534de60293cacb5c35795a65`.
- Branch created: `ralph/pcrf-code-review-fixes`.

## Phase 1 — Audit identifier token-shape safety

### PCRF-001 — Reject token-shaped top-level audit identifiers

**Satisfies:** PCRF-001  
**Validated by:** PCRFV-001

- [ ] Inspect current `_safe_audit_identifier()` behavior
- [ ] Add focused tests for pure GitHub-token-shaped `workspace_id` and `repository_id` values assembled at runtime
- [ ] Add focused tests for pure OpenAI-key-shaped `workspace_id` and `repository_id` values assembled at runtime
- [ ] Add focused tests for AWS-access-key-ID-shaped `workspace_id` and `repository_id` values assembled at runtime
- [ ] Add focused tests for bearer-token-shaped top-level audit identifiers assembled at runtime
- [ ] Add focused tests for high-entropy-token-shaped top-level audit identifiers assembled at runtime
- [ ] Confirm rejected values fail closed to `None`
- [ ] Confirm rejected values do not appear in persisted audit JSONL content
- [ ] Confirm safe workspace/repository IDs remain preserved
- [ ] Confirm safe request/session IDs remain preserved
- [ ] Confirm safe prune-plan/prune-candidate IDs remain preserved by generic redaction false-positive guards
- [ ] Share or expose a safe internal secret/token-shape predicate from `errors.py`
- [ ] Apply the secret/token-shape predicate at the audit identifier boundary
- [ ] Preserve existing `redact_secrets()` behavior
- [ ] Run focused audit/redaction tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PCRFV-001 if feasible

**PCRF-001 evidence:**

- Pending.

## Phase 2 — Final CRH evidence correction

### PCRF-002 — Correct final CRH-006 evidence

**Satisfies:** PCRF-002  
**Validated by:** PCRFV-002

- [ ] Update `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md` CRH-006 evidence to include final `master` head `86440755c84a817a534de60293cacb5c35795a65`
- [ ] Record final CI run `34691630548`
- [ ] Record final job `103547787452`
- [ ] Record final pytest result `1226 passed`, `4 warnings`
- [ ] Record final coverage `92%`, TOTAL `6536` statements / `518` misses
- [ ] Record final runtime `613.65s`
- [ ] Preserve prior CRH-001 through CRH-005 evidence
- [ ] Avoid overstating private Ralph Bridge operator configuration details
- [ ] Verify docs drift checks remain green

**PCRF-002 evidence:**

- Pending.

## Phase 3 — Error-boundary rationale restoration

### PCRF-003 — Restore high-value `errors.py` rationale

**Satisfies:** PCRF-003  
**Validated by:** PCRFV-003

- [ ] Restore concise rationale for structured `WorkspaceError` handling and unexpected exception propagation
- [ ] Restore concise rationale for `to_tool_error()` as the MCP boundary translator
- [ ] Restore concise rationale for generic redaction as defense-in-depth for tool errors and audit metadata
- [ ] Document nested secret-key hint propagation through dictionaries, lists, tuples, and sets
- [ ] Document JSON-safe handling for sets and bytes/bytearray
- [ ] Document fail-closed handling for unsupported values such as `frozenset` and `memoryview`
- [ ] Document high-entropy false-positive guards for commit SHAs, UUIDs, safe opaque IDs, and public SSH fingerprints
- [ ] Avoid reintroducing stale comments or comments that claim behavior not present in the code
- [ ] Confirm no public schema/output change is introduced
- [ ] Run formatting/lint/mypy through full CI

**PCRF-003 evidence:**

- Pending.

## Final closure

### PCRF-004 — Final verification and closure

- [ ] Audit that PCRF-001 through PCRF-003 are implemented or explicitly dispositioned
- [ ] Audit that PCRFV-001 through PCRFV-003 evidence is recorded
- [ ] Confirm no broad-prune or unsafe fallback behavior was introduced
- [ ] Confirm public schemas and `contract_version` are correct for the actual changes
- [ ] Confirm `docs/TOOLS.md` and exported schemas are drift-free
- [ ] Confirm focused audit/redaction tests pass
- [ ] Run complete Docker-backed CI with all gates green on exact branch head
- [ ] Open PR from `ralph/pcrf-code-review-fixes` to `master`
- [ ] Merge only through exact-head guarded Ralph Bridge merge
- [ ] Verify post-merge `master` CI is green
- [ ] Record final exact run/head/job/test count/coverage

**PCRF-004 evidence:**

- Pending.

## Definition of done

- [ ] PCRF-001 implemented or explicitly dispositioned
- [ ] PCRF-002 implemented or explicitly dispositioned
- [ ] PCRF-003 implemented or explicitly dispositioned
- [ ] PCRFV-001 evidence recorded
- [ ] PCRFV-002 evidence recorded
- [ ] PCRFV-003 evidence recorded
- [ ] Top-level audit identifiers reject pure token-shaped unsafe values
- [ ] Valid bounded opaque IDs remain preserved
- [ ] Final CRH-006 evidence points at the actual final post-merge `master` head and CI run
- [ ] High-value `errors.py` rationale is restored without stale commentary
- [ ] O1-O12 destructive-safety invariants remain intact
- [ ] PMORR R1-R8 guarantees remain intact
- [ ] CRH-001 through CRH-006 guarantees remain intact
- [ ] No public schema or contract-version drift remains unresolved
- [ ] Full exact-head Docker-backed CI is green
