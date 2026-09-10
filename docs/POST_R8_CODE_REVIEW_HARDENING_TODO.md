# Post-R8 Code Review Hardening TODO

**Status:** proposed follow-up workstream  
**Spec:** `docs/POST_R8_CODE_REVIEW_HARDENING_SPEC.md`  
**Base:** `5d64af1a92bbe83154ab3eebdfd20c1b56495cd9` (`docs: close R8 remediation workstream`)  
**Task series:** `CRH-001`..`CRH-005`  
**Validation series:** `CRHV-001`..`CRHV-005`

This TODO tracks the follow-up hardening issues found during the post-R8 code review. It does not reopen the completed R1-R8 Post-MVP Operations Review Remediation workstream. It exists to reduce residual risk in type hygiene, audit identifier safety, generic redaction, SSH-agent cleanup legacy normalization, and Ralph Bridge default-branch write policy.

## Global rules

- [ ] Work from an exact inspected base SHA
- [ ] Prefer a source branch and exact-head guarded merge for implementation work unless the user explicitly authorizes another direct-to-`master` exception
- [ ] Add or strengthen focused tests before implementation where the gap is behavioral
- [ ] Prove a clean RED for each behavioral validation when feasible
- [ ] Preserve all completed O1-O12 destructive-safety invariants
- [ ] Preserve all completed PMORR R1-R8 guarantees
- [ ] Do not introduce broad Docker cleanup, label-wide prune, prefix-wide prune, path-wide deletion, system-wide deletion, or `docker system prune`
- [ ] Do not expose host paths, secrets, Docker credentials, SSH material, raw environment values, prompt text, arbitrary client metadata, or arbitrary raw failure strings in audit or public output
- [ ] Avoid public MCP schema/output changes unless explicitly justified
- [ ] Regenerate schemas and bump `contract_version` only if public schema, registered descriptions, or response semantics change
- [ ] Run focused tests before full CI
- [ ] Run full Docker-backed CI before marking this workstream complete
- [ ] Record exact RED/GREEN/revert evidence for each task where feasible
- [ ] Restore Ralph Bridge default-branch write protection unless there is a new explicit operator-approved exception

## Phase 0 — Planning docs

### CRH-000 — Create hardening spec and TODO

- [ ] Add `docs/POST_R8_CODE_REVIEW_HARDENING_SPEC.md`
- [ ] Add `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md`
- [ ] Keep validation labels in the `CRHV-*` series until acceptance registry work is deliberately added
- [ ] Verify docs-only commit does not break CI
- [ ] Record exact CI evidence

**CRH-000 completion evidence:** TODO

## Phase 1 — Byte observation type hygiene

### CRH-001 — Reject boolean Docker volume sizes

**Satisfies:** CRH-001  
**Validated by:** CRHV-001

- [ ] Locate Docker volume size parsing in `operations/inventory.py`
- [ ] Add focused test coverage for `UsageData.Size = True`
- [ ] Add focused test coverage for `UsageData.Size = False`
- [ ] Add focused test coverage for negative size values
- [ ] Add focused test coverage for missing and non-integer size values if not already covered
- [ ] Confirm valid non-negative integer sizes still produce available byte observations
- [ ] Confirm malformed size values remain unavailable rather than zero
- [ ] Confirm oversized volume-list behavior still reports `truncated` and `incomplete`
- [ ] Confirm the Docker volume-list call remains read-only and ownership-filtered
- [ ] Implement the `bool` rejection without changing public schema shape
- [ ] Run focused inventory tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify CRHV-001 if feasible

**CRH-001 completion evidence:** TODO

## Phase 2 — Audit identifier safety

### CRH-002 — Sanitize top-level audit identifiers

**Satisfies:** CRH-002  
**Validated by:** CRHV-002

- [ ] Inspect `audit.write_audit_event()` top-level fields
- [ ] Identify existing safe request/session identifier sanitizer behavior
- [ ] Decide whether to reuse the request/session sanitizer or introduce a separate top-level audit identifier sanitizer
- [ ] Define allowed safe identifier shape and maximum length
- [ ] Define fail-closed behavior for unsafe `workspace_id`
- [ ] Define fail-closed behavior for unsafe `repository_id`
- [ ] Add focused tests for safe `workspace_id` and `repository_id` preservation
- [ ] Add focused tests for host-path-shaped top-level IDs
- [ ] Add focused tests for token/authorization-shaped top-level IDs
- [ ] Add focused tests for SSH-material-shaped top-level IDs
- [ ] Add focused tests for raw environment key/value-shaped top-level IDs
- [ ] Add focused tests for multiline or shell-fragment-shaped top-level IDs
- [ ] Apply sanitizer before durable audit persistence
- [ ] Confirm metadata redaction still behaves as before
- [ ] Confirm audit write failure behavior remains non-fatal
- [ ] Run focused audit tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify CRHV-002 if feasible

**CRH-002 completion evidence:** TODO

## Phase 3 — Generic redaction coverage

### CRH-003 — Expand generic secret/token redaction

**Satisfies:** CRH-003  
**Validated by:** CRHV-003

- [ ] Inventory current generic redaction helpers and call sites
- [ ] Add tests for OpenAI-style API key prefixes without committing real-looking secrets
- [ ] Add tests for GitHub token prefix shapes without committing real-looking secrets
- [ ] Add tests for AWS access key ID prefix shapes without committing real-looking secrets
- [ ] Add tests for bearer-token header/value shapes
- [ ] Add tests for common `api_key`, `access_token`, `secret`, and `password` assignment shapes
- [ ] Add tests for high-entropy token-like strings using synthetic non-secret fixtures
- [ ] Add false-positive guard tests for commit SHAs
- [ ] Add false-positive guard tests for UUIDs
- [ ] Add false-positive guard tests for bounded request IDs and public failure classifications
- [ ] Expand redaction patterns while keeping output deterministic and bounded
- [ ] Confirm public prune execution failure handling still uses fail-closed reason classification rather than regex-only redaction
- [ ] Confirm audit metadata redaction still removes unsafe values
- [ ] Run focused redaction, audit, and prune tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify CRHV-003 if feasible

**CRH-003 completion evidence:** TODO

## Phase 4 — SSH-agent cleanup durable error normalization

### CRH-004 — Normalize SSH-agent cleanup legacy errors at read/model boundary

**Satisfies:** CRH-004  
**Validated by:** CRHV-004

- [ ] Locate SSH-agent cleanup durable row-to-model construction
- [ ] Compare job-log cleanup legacy error normalization behavior
- [ ] Define SSH-agent legacy `last_error` safe classification behavior
- [ ] Add focused tests that seed legacy SSH cleanup debt with host-path-shaped raw errors
- [ ] Add focused tests that seed legacy SSH cleanup debt with Docker/SSH/environment/token-shaped raw errors
- [ ] Confirm resulting internal debt model does not carry raw legacy exception text
- [ ] Confirm health output does not expose raw legacy SSH cleanup error text
- [ ] Confirm inventory output does not expose raw legacy SSH cleanup error text
- [ ] Confirm prune planning output does not expose raw legacy SSH cleanup error text
- [ ] Confirm prune execution/audit output does not expose raw legacy SSH cleanup error text
- [ ] Preserve retry metadata, timestamps, socket identity, ownership metadata, and safe failure distinctions
- [ ] Avoid SQLite schema changes unless strictly necessary
- [ ] Preserve active SSH cleanup APIs verified by R6
- [ ] Run focused SSH cleanup, maintenance, inventory, and prune tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify CRHV-004 if feasible

**CRH-004 completion evidence:** TODO

## Phase 5 — Ralph Bridge operator policy hygiene

### CRH-005 — Restore default-branch write protection after R8 direct publication

**Satisfies:** CRH-005  
**Validated by:** CRHV-005

- [ ] Identify the active Ralph Bridge operator setting that controls default-branch writes
- [ ] Restore direct default-branch write protection for `ekkus93/mcp-chatgpt-sandbox` unless a new explicit exception is active
- [ ] Restart or refresh Ralph Bridge if required by the operator configuration change
- [ ] Verify normal source-branch writes remain available if needed for future work
- [ ] Verify exact-head guarded PR merges remain available if needed for future work
- [ ] Verify direct writes to `master` are rejected by policy after restoration
- [ ] Record the verification method without committing private operator config or secrets
- [ ] Update repository operator documentation if this repo contains the relevant Ralph Bridge setup guidance
- [ ] Run any docs or CI checks affected by documentation updates
- [ ] Record exact evidence for CRHV-005

**CRH-005 completion evidence:** TODO

## Final closure

### CRH-006 — Final hardening audit and closure

- [ ] Audit that CRH-001..CRH-005 are implemented or explicitly dispositioned
- [ ] Audit that CRHV-001..CRHV-005 evidence is recorded
- [ ] Confirm no broad-prune or unsafe fallback behavior was introduced
- [ ] Confirm public schemas and `contract_version` are correct for the actual changes
- [ ] Confirm `docs/TOOLS.md` and exported schemas are drift-free
- [ ] Confirm all focused tests pass
- [ ] Run complete Docker-backed CI with all gates green
- [ ] Verify final exact-head CI is green before marking this workstream complete
- [ ] Record final exact run/head/job/test count/coverage

**CRH-006 completion evidence:** TODO

## Definition of done

- [ ] CRH-001..CRH-005 implemented or explicitly dispositioned
- [ ] CRHV-001..CRHV-005 evidence recorded
- [ ] Boolean Docker volume sizes are unavailable, not byte counts
- [ ] Top-level audit `workspace_id` and `repository_id` values are sanitized at the audit boundary
- [ ] Generic redaction covers representative modern token/key shapes with false-positive guards
- [ ] SSH-agent cleanup legacy durable errors are normalized at the read/model boundary
- [ ] Ralph Bridge default-branch write protection is restored or an explicit exception is documented
- [ ] O1-O12 destructive-safety invariants remain intact
- [ ] PMORR R1-R8 guarantees remain intact
- [ ] No public schema or contract-version drift remains unresolved
- [ ] Full exact-head Docker-backed CI is green
