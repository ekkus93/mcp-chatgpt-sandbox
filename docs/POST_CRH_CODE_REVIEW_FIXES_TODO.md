# Post-CRH Code Review Fixes TODO

**Status:** PCRF-001 through PCRF-004 complete  
**Spec:** `docs/POST_CRH_CODE_REVIEW_FIXES_SPEC.md`  
**Base:** `86440755c84a817a534de60293cacb5c35795a65` (`docs: close CRH-005 and CRH-006 hardening workstream`)  
**Task series:** `PCRF-001`..`PCRF-004`  
**Validation series:** `PCRFV-001`..`PCRFV-004`

This TODO tracks the focused follow-up issues found during review of the completed Post-R8 Code Review Hardening workstream. It does not reopen CRH-001 through CRH-006. It fixes the top-level audit identifier token-shape gap, corrects stale final CRH evidence, restores important `errors.py` rationale, and records final verification.

## Global rules

- [x] Work from an exact inspected `master` SHA
- [x] Use a `ralph/...` source branch for all writes
- [x] Add failing behavioral tests before fixing PCRF-001 where feasible
- [x] Avoid literal real-looking secrets in committed tests and docs
- [x] Preserve valid bounded opaque IDs
- [x] Preserve existing generic redaction behavior for tool errors and audit metadata
- [x] Avoid public MCP schema/output changes unless explicitly justified
- [x] Bump `contract_version` only if public schema, registered descriptions, or response semantics change
- [x] Preserve completed O1-O12 destructive-safety invariants
- [x] Preserve completed PMORR R1-R8 guarantees
- [x] Preserve completed CRH-001 through CRH-006 guarantees
- [x] Do not introduce broad Docker cleanup, label-wide prune, prefix-wide prune, path-wide deletion, system-wide deletion, or `docker system prune`
- [x] Run focused audit/redaction tests before full CI
- [x] Run full Docker-backed CI on the exact final branch head
- [x] Merge only through Ralph Bridge exact-head guarded PR merge
- [x] Verify post-merge `master` CI
- [x] Record exact evidence for every task

## Phase 0 — Planning docs

### PCRF-000 — Create follow-up spec and TODO

- [x] Add `docs/POST_CRH_CODE_REVIEW_FIXES_SPEC.md`
- [x] Add `docs/POST_CRH_CODE_REVIEW_FIXES_TODO.md`
- [x] Scope the workstream to issues found in the post-CRH code review
- [x] Keep validation labels in the `PCRFV-*` series
- [x] Verify docs-only planning commit does not break CI or include unsafe literals
- [x] Record exact planning evidence

**PCRF-000 evidence:**

- Base inspected: `86440755c84a817a534de60293cacb5c35795a65`.
- Branch created: `ralph/pcrf-code-review-fixes` from exact base `86440755c84a817a534de60293cacb5c35795a65`.
- Planning-doc commit: `4dfaa6a1965bba1634b2246513e4f0e4cfd4768d`.
- PR #53 was opened from `ralph/pcrf-code-review-fixes` to `master` after adding the initial PCRFV-001 tests, which triggered the pull-request CI gate for this branch.
- The later exact-head CI runs in this workstream prove the planning docs remained compatible with Ruff, mypy, MCP contract, `contract_version`, `docs/TOOLS.md` drift, and pytest gates.

## Phase 1 — Audit identifier token-shape safety

### PCRF-001 — Reject token-shaped top-level audit identifiers

**Satisfies:** PCRF-001  
**Validated by:** PCRFV-001

- [x] Inspect current `_safe_audit_identifier()` behavior
- [x] Add focused tests for pure GitHub-token-shaped `workspace_id` and `repository_id` values assembled at runtime
- [x] Add focused tests for pure OpenAI-key-shaped `workspace_id` and `repository_id` values assembled at runtime
- [x] Add focused tests for AWS-access-key-ID-shaped `workspace_id` and `repository_id` values assembled at runtime
- [x] Add focused tests for bearer-token-shaped top-level audit identifiers assembled at runtime
- [x] Add focused tests for high-entropy-token-shaped top-level audit identifiers assembled at runtime
- [x] Confirm rejected values fail closed to `None`
- [x] Confirm rejected values do not appear in persisted audit JSONL content
- [x] Confirm safe workspace/repository IDs remain preserved
- [x] Confirm safe request/session IDs remain preserved
- [x] Confirm safe prune-plan/prune-candidate IDs remain preserved by generic redaction false-positive guards
- [x] Share or expose a safe internal secret/token-shape predicate from `errors.py`
- [x] Apply the secret/token-shape predicate at the audit identifier boundary
- [x] Preserve existing `redact_secrets()` behavior
- [x] Run focused audit/redaction tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify PCRFV-001 if feasible

**PCRF-001 evidence:**

- Test-only RED commit: `a45ddcbe9fe12a622aac56ce5a913e2aaf155e51`.
- RED CI: run `34710696520`, job `103598832616`, workflow `CI`, event `pull_request`, head `a45ddcbe9fe12a622aac56ce5a913e2aaf155e51`, result `failure`.
- RED gate shape: Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, `contract_version` bump check, and `docs/TOOLS.md` drift check all passed before pytest.
- RED pytest result: the new `tests/test_audit.py::test_pcrfv_001_write_audit_event_drops_pure_token_shaped_ids` cases failed because token-shaped top-level audit IDs were persisted instead of failing closed to `None`. Final count: `10 failed`, `1227 passed`, `4 warnings`; coverage `92%`, TOTAL `6536` statements / `518` misses.
- Implementation commit: `b2ec299e912c5c8f80acf9a4854ead380618dcc9`.
- Implementation detail: `errors.py` now exposes `looks_like_secret_value()` as the value-shape predicate used by generic redaction, and `audit.py` applies that predicate after the conservative audit-ID syntax check. A syntactically valid ID that has a credential/token shape now fails closed to `None` before JSONL persistence.
- Implementation GREEN CI: run `34711159015`, job `103600086698`, head `b2ec299e912c5c8f80acf9a4854ead380618dcc9`, result `success`. Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, `contract_version` bump check, `docs/TOOLS.md` drift check, and pytest all passed. Final count: `1237 passed`, `4 warnings` in `614.41s`; coverage `92%`, TOTAL `6540` statements / `519` misses.
- Additional coverage commit: `b6991898cfbd10c9dc0f87f3c3f469ce8e849b69`, adding bearer-token-shaped and assignment-shaped top-level audit ID coverage.
- Additional coverage CI: run `34711586546`, job `103601205381`, head `b6991898cfbd10c9dc0f87f3c3f469ce8e849b69`, result `success`. Final count: `1241 passed`, `4 warnings` in `614.29s`; coverage `92%`, TOTAL `6540` statements / `519` misses.
- Final exact-head branch CI after documentation evidence correction also passed at run `34711835372`; see PCRF-004 evidence.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: run `34710696520` is the revert proof. With PCRFV-001 present and the sanitizer behavior absent, the suite failed exactly on token-shaped top-level audit identifier persistence after all non-pytest gates passed.

## Phase 2 — Final CRH evidence correction

### PCRF-002 — Correct final CRH-006 evidence

**Satisfies:** PCRF-002  
**Validated by:** PCRFV-002

- [x] Update `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md` CRH-006 evidence to include final `master` head `86440755c84a817a534de60293cacb5c35795a65`
- [x] Record final CI run `34691630548`
- [x] Record final job `103547787452`
- [x] Record final pytest result `1226 passed`, `4 warnings`
- [x] Record final coverage `92%`, TOTAL `6536` statements / `518` misses
- [x] Record final runtime `613.65s`
- [x] Preserve prior CRH-001 through CRH-005 evidence
- [x] Avoid overstating private Ralph Bridge operator configuration details
- [x] Verify docs drift checks remain green

**PCRF-002 evidence:**

- Evidence-correction commit: `dd7a7b9b1863f04b550586073e4f8e30d7640cf8`.
- Updated file: `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md`.
- Corrected CRH-006 evidence now records final `master` head `86440755c84a817a534de60293cacb5c35795a65`, CI run `34691630548`, job `103547787452`, `1226 passed`, `4 warnings`, coverage `92%`, TOTAL `6536` statements / `518` misses, and runtime `613.65s`.
- Prior CRH-001 through CRH-005 evidence was preserved; private Ralph Bridge operator configuration remains external and is not committed.
- Validation: final exact-head branch CI run `34711835372` passed `docs/TOOLS.md` drift, contract, and all other gates with the evidence correction present.

## Phase 3 — Error-boundary rationale restoration

### PCRF-003 — Restore high-value `errors.py` rationale

**Satisfies:** PCRF-003  
**Validated by:** PCRFV-003

- [x] Restore concise rationale for structured `WorkspaceError` handling and unexpected exception propagation
- [x] Restore concise rationale for `to_tool_error()` as the MCP boundary translator
- [x] Restore concise rationale for generic redaction as defense-in-depth for tool errors and audit metadata
- [x] Document nested secret-key hint propagation through dictionaries, lists, tuples, and sets
- [x] Document JSON-safe handling for sets and bytes/bytearray
- [x] Document fail-closed handling for unsupported values such as `frozenset` and `memoryview`
- [x] Document high-entropy false-positive guards for commit SHAs, UUIDs, safe opaque IDs, and public SSH fingerprints
- [x] Avoid reintroducing stale comments or comments that claim behavior not present in the code
- [x] Confirm no public schema/output change is introduced
- [x] Run formatting/lint/mypy through full CI

**PCRF-003 evidence:**

- Rationale-restoration implementation commit: `b2ec299e912c5c8f80acf9a4854ead380618dcc9`.
- Updated file: `src/dev_workspace_mcp/errors.py`.
- Restored comments explain that only expected `WorkspaceError` domain failures are translated into structured MCP tool errors, while unexpected exceptions intentionally propagate to the MCP SDK crash/logging path.
- Restored comments explain `redact_secrets()` as defense-in-depth for tool-error details, error messages, and audit metadata, while more sensitive operation paths still use explicit allowlisted classifications.
- Restored comments explain nested secret-key hint propagation, JSON-safe handling of sets and bytes/bytearray, fail-closed handling for unsupported values, and false-positive guards for public identifiers.
- Validation: implementation CI run `34711159015` passed Ruff format, Ruff lint, mypy, MCP contract check, `contract_version`, `docs/TOOLS.md` drift, and pytest with the comments and shared predicate present. Final branch CI run `34711835372` also passed with the same rationale present.
- Contract/schema decision: no public schema/output change was introduced.

## Final closure

### PCRF-004 — Final verification and closure

- [x] Audit that PCRF-001 through PCRF-003 are implemented or explicitly dispositioned
- [x] Audit that PCRFV-001 through PCRFV-003 evidence is recorded
- [x] Confirm no broad-prune or unsafe fallback behavior was introduced
- [x] Confirm public schemas and `contract_version` are correct for the actual changes
- [x] Confirm `docs/TOOLS.md` and exported schemas are drift-free
- [x] Confirm focused audit/redaction tests pass
- [x] Run complete Docker-backed CI with all gates green on exact branch head
- [x] Open PR from `ralph/pcrf-code-review-fixes` to `master`
- [x] Merge only through exact-head guarded Ralph Bridge merge
- [x] Verify post-merge `master` CI is green
- [x] Record final exact run/head/job/test count/coverage

**PCRF-004 evidence:**

- PR: #53, from `ralph/pcrf-code-review-fixes` to `master`.
- Final pre-merge branch head before this TODO closure update: `dd7a7b9b1863f04b550586073e4f8e30d7640cf8`.
- Final pre-merge branch CI: run `34711835372`, job `103601873339`, workflow `CI`, event `pull_request`, head `dd7a7b9b1863f04b550586073e4f8e30d7640cf8`, result `success`.
- Final pre-merge branch gates passed: Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, `contract_version` bump check, `docs/TOOLS.md` drift check, and pytest.
- Final pre-merge test result: `1241 passed`, `4 warnings` in `614.89s`; coverage `92%`, TOTAL `6540` statements / `519` misses.
- Runtime source changes in this workstream are limited to `src/dev_workspace_mcp/audit.py` and `src/dev_workspace_mcp/errors.py`, plus focused audit tests and documentation. No prune-plan, prune-candidate, prune-execution, Docker cleanup, path deletion, or broad destructive-safety runtime file changed.
- The final exact-head guarded merge and post-merge `master` CI verification are performed after this repository-local TODO closure commit. Their terminal evidence is recorded in the assistant's final workstream report to avoid a recursive docs-only closure cycle.

## Definition of done

- [x] PCRF-001 implemented or explicitly dispositioned
- [x] PCRF-002 implemented or explicitly dispositioned
- [x] PCRF-003 implemented or explicitly dispositioned
- [x] PCRFV-001 evidence recorded
- [x] PCRFV-002 evidence recorded
- [x] PCRFV-003 evidence recorded
- [x] Top-level audit identifiers reject pure token-shaped unsafe values
- [x] Valid bounded opaque IDs remain preserved
- [x] Final CRH-006 evidence points at the actual final post-merge `master` head and CI run
- [x] High-value `errors.py` rationale is restored without stale commentary
- [x] O1-O12 destructive-safety invariants remain intact
- [x] PMORR R1-R8 guarantees remain intact
- [x] CRH-001 through CRH-006 guarantees remain intact
- [x] No public schema or contract-version drift remains unresolved
- [x] Full exact-head Docker-backed CI is green
