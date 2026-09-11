# Post-R8 Code Review Hardening TODO

**Status:** CRH-001 through CRH-003 complete; CRH-004 next  
**Spec:** `docs/POST_R8_CODE_REVIEW_HARDENING_SPEC.md`  
**Base:** `5d64af1a92bbe83154ab3eebdfd20c1b56495cd9` (`docs: close R8 remediation workstream`)  
**Task series:** `CRH-001`..`CRH-005`  
**Validation series:** `CRHV-001`..`CRHV-005`

This TODO tracks the follow-up hardening issues found during the post-R8 code review. It does not reopen the completed R1-R8 Post-MVP Operations Review Remediation workstream. It exists to reduce residual risk in type hygiene, audit identifier safety, generic redaction, SSH-agent cleanup legacy normalization, and Ralph Bridge default-branch write policy.

## Global rules

- [x] Work from an exact inspected base SHA
- [x] Prefer a source branch and exact-head guarded merge for implementation work unless the user explicitly authorizes another direct-to-`master` exception
- [x] Add or strengthen focused tests before implementation where the gap is behavioral
- [x] Prove a clean RED for each behavioral validation when feasible
- [x] Preserve all completed O1-O12 destructive-safety invariants
- [x] Preserve all completed PMORR R1-R8 guarantees
- [x] Do not introduce broad Docker cleanup, label-wide prune, prefix-wide prune, path-wide deletion, system-wide deletion, or `docker system prune`
- [x] Do not expose host paths, secrets, Docker credentials, SSH material, raw environment values, prompt text, arbitrary client metadata, or arbitrary raw failure strings in audit or public output
- [x] Avoid public MCP schema/output changes unless explicitly justified
- [x] Regenerate schemas and bump `contract_version` only if public schema, registered descriptions, or response semantics change
- [x] Run focused tests before full CI
- [ ] Run full Docker-backed CI before marking this workstream complete
- [x] Record exact RED/GREEN/revert evidence for each task where feasible
- [ ] Restore Ralph Bridge default-branch write protection unless there is a new explicit operator-approved exception

## Phase 0 — Planning docs

### CRH-000 — Create hardening spec and TODO

- [x] Add `docs/POST_R8_CODE_REVIEW_HARDENING_SPEC.md`
- [x] Add `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md`
- [x] Keep validation labels in the `CRHV-*` series until acceptance registry work is deliberately added
- [x] Verify docs-only commit does not break CI
- [x] Record exact CI evidence

**CRH-000 completion evidence:**

- Planning-doc commit: `3e19195a7c739846fe1d37f33c5bcbb288608ab8`.
- CI: run `34509779692`; workflow `CI`; event `push`; head `3e19195a7c739846fe1d37f33c5bcbb288608ab8`; result `success`.
- Gates passed: Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest.

## Phase 1 — Byte observation type hygiene

### CRH-001 — Reject boolean Docker volume sizes

**Satisfies:** CRH-001  
**Validated by:** CRHV-001

- [x] Locate Docker volume size parsing in `operations/inventory.py`
- [x] Add focused test coverage for `UsageData.Size = True`
- [x] Add focused test coverage for `UsageData.Size = False`
- [x] Add focused test coverage for negative size values
- [x] Add focused test coverage for missing and non-integer size values if not already covered
- [x] Confirm valid non-negative integer sizes still produce available byte observations
- [x] Confirm malformed size values remain unavailable rather than zero
- [x] Confirm oversized volume-list behavior still reports `truncated` and `incomplete`
- [x] Confirm the Docker volume-list call remains read-only and ownership-filtered
- [x] Implement the `bool` rejection without changing public schema shape
- [x] Run focused inventory tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify CRHV-001 if feasible

**CRH-001 completion evidence:**

- Initial acceptance-test commit: `aa2d449a470c88c7c22b15142cb52b9e45d47e5f`; CI run `34513839435` failed before pytest at Ruff format, so it is not counted as behavioral RED evidence.
- Clean RED commit: `f720e4d1a41617f2eeb4d31f31153ca97f3f20d6`.
- Clean RED CI: run `34514129941`, job `102995224290`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, and `docs/TOOLS.md` drift check all passed; pytest then failed only `tests/operations/test_post_r8_code_review_hardening_inventory.py::test_crhv_001_boolean_docker_volume_sizes_are_unavailable[True]` and `[False]` because `True` and `False` were treated as measured byte values; final count `2 failed, 1205 passed`; coverage `92%` with TOTAL `6508` statements / `518` misses.
- Initial GREEN implementation commit: `6627aae167b74e8d35e5a4674fae402aa1e4f663`; CI run `34514836913`, job `102997541781`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed.
- Implementation-shape cleanup commit: `ad0bf2b480937f2fa3e6e109bf8bc4d2851bb9f8`; this moved the bool guard directly into `src/dev_workspace_mcp/operations/inventory.py` and restored `src/dev_workspace_mcp/operations/__init__.py` to a plain package marker, eliminating the temporary import-time wrapper used by the initial fix.
- Final retained GREEN evidence: CRH-002 GREEN run `34520888264` on `a10dc83b66c5819ec2fbd98a9fcaafd0bb2cff7c` passed with the direct `inventory.py` CRH-001 implementation still present.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: the clean RED run `34514129941` is the revert proof: with CRHV-001 present and the boolean rejection absent, the suite failed exactly on the two bool-as-byte observations while all other gates and tests passed.

## Phase 2 — Audit identifier safety

### CRH-002 — Sanitize top-level audit identifiers

**Satisfies:** CRH-002  
**Validated by:** CRHV-002

- [x] Inspect `audit.write_audit_event()` top-level fields
- [x] Identify existing safe request/session identifier sanitizer behavior
- [x] Decide whether to reuse the request/session sanitizer or introduce a separate top-level audit identifier sanitizer
- [x] Define allowed safe identifier shape and maximum length
- [x] Define fail-closed behavior for unsafe `workspace_id`
- [x] Define fail-closed behavior for unsafe `repository_id`
- [x] Add focused tests for safe `workspace_id` and `repository_id` preservation
- [x] Add focused tests for host-path-shaped top-level IDs
- [x] Add focused tests for token/authorization-shaped top-level IDs
- [x] Add focused tests for SSH-material-shaped top-level IDs
- [x] Add focused tests for raw environment key/value-shaped top-level IDs
- [x] Add focused tests for multiline or shell-fragment-shaped top-level IDs
- [x] Apply sanitizer before durable audit persistence
- [x] Confirm metadata redaction still behaves as before
- [x] Confirm audit write failure behavior remains non-fatal
- [x] Run focused audit tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify CRHV-002 if feasible

**CRH-002 completion evidence:**

- Acceptance-test commit: `02a0799cb9eed903472b3b684f93591636294e7f`.
- Clean RED CI: run `34520675695`, job `103017127084`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, and `docs/TOOLS.md` drift check all passed; pytest then failed the new `test_crhv_002_write_audit_event_drops_unsafe_top_level_ids` cases because raw top-level `workspace_id` / `repository_id` values were persisted instead of failing closed.
- Source cleanup commit: `ad0bf2b480937f2fa3e6e109bf8bc4d2851bb9f8`; unrelated to CRH-002 behavior, but retained while moving CRH-001 from temporary package wrapper to direct `operations/inventory.py` implementation.
- Implementation commit: `a10dc83b66c5819ec2fbd98a9fcaafd0bb2cff7c`.
- GREEN CI: run `34520888264`, job `103017823458`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed.
- Implementation detail: `write_audit_event()` now sanitizes `request_id`, `session_id`, `workspace_id`, and `repository_id` through the same bounded safe audit identifier boundary before JSONL persistence. Safe opaque IDs are preserved; host-path-shaped, authorization/token-shaped, environment-assignment-shaped, SSH-material-shaped, multiline, and shell-fragment-shaped top-level IDs fail closed to `None`.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: the clean RED run `34520675695` is the revert proof: with CRHV-002 present and the top-level audit identifier sanitizer absent, the suite failed exactly on the unsafe top-level ID persistence assertions after all non-pytest gates passed.

## Phase 3 — Generic redaction coverage

### CRH-003 — Expand generic secret/token redaction

**Satisfies:** CRH-003  
**Validated by:** CRHV-003

- [x] Inventory current generic redaction helpers and call sites
- [x] Add tests for OpenAI-style API key prefixes without committing real-looking secrets
- [x] Add tests for GitHub token prefix shapes without committing real-looking secrets
- [x] Add tests for AWS access key ID prefix shapes without committing real-looking secrets
- [x] Add tests for bearer-token header/value shapes
- [x] Add tests for common `api_key`, `access_token`, `secret`, and `password` assignment shapes
- [x] Add tests for high-entropy token-like strings using synthetic non-secret fixtures
- [x] Add false-positive guard tests for commit SHAs
- [x] Add false-positive guard tests for UUIDs
- [x] Add false-positive guard tests for bounded request IDs and public failure classifications
- [x] Expand redaction patterns while keeping output deterministic and bounded
- [x] Confirm public prune execution failure handling still uses fail-closed reason classification rather than regex-only redaction
- [x] Confirm audit metadata redaction still removes unsafe values
- [x] Run focused redaction, audit, and prune tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify CRHV-003 if feasible

**CRH-003 completion evidence:**

- Initial CRHV-003 test-only commit: `1c83221f87f69530fb92e18dbfc38481fa5085cc`; CI run `34544180189` failed at Ruff format before pytest, so it is not counted as clean behavioral RED evidence.
- Implementation commit: `c73d2b9b34e3f131ab93b567b09ee3ce7e62cbdc`; expanded generic redaction patterns in `src/dev_workspace_mcp/errors.py`.
- Formatting/lint repair commits: `ae110d75ef3d8f4745b304fed822196c1e9b8231`, `ca363d6dbeb1750a79bb9c4e269b621ce166d68d`, `a699f5e89f07df9b8baf553f704b56288803fc37`, `11bd53c9dfa32c9aa2bdec1d180bcecb42980f60`, `1396fca9fda011c89dc9b7a1ac9aee23efbc8ec0`, and `e9384065064d0f1eaf6723f1beaa4858db018f05`; these fixed Ruff formatting/import-order issues in the new test file and source formatting.
- Correctness repair commit: `780442f07d6a7adc57418527d4ed3742e893b20a`; preserved known safe opaque internal IDs from the high-entropy fallback while broadening OpenAI-style token matching for project-token-like values.
- GREEN CI: run `34546959288`, job `103101561803`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed.
- Implementation detail: generic redaction now covers representative OpenAI-style, GitHub-token-style, AWS-access-key-ID-style, bearer-token-style, common assignment-style, and high-entropy token-like values using synthetic fixtures that avoid committing real-looking secrets. False-positive guards preserve commit SHAs, UUIDs, bounded request IDs, safe internal prune-plan/candidate IDs, and public failure classifications.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: clean behavioral RED evidence was not retained for CRH-003 because the test-only attempt failed before pytest at Ruff format, and subsequent commits combined source/test formatting and implementation repairs. The retained evidence is the final exact-head GREEN run plus the earlier failed CRH-003 run documenting that the initial test-only attempt was structurally blocked before behavioral assertion execution.

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
- [x] CRHV-001 evidence recorded
- [x] CRHV-002 evidence recorded
- [x] CRHV-003 evidence recorded
- [x] Boolean Docker volume sizes are unavailable, not byte counts
- [x] Top-level audit `workspace_id` and `repository_id` values are sanitized at the audit boundary
- [x] Generic redaction covers representative modern token/key shapes with false-positive guards
- [ ] SSH-agent cleanup legacy durable errors are normalized at the read/model boundary
- [ ] Ralph Bridge default-branch write protection is restored or an explicit exception is documented
- [x] O1-O12 destructive-safety invariants remain intact
- [x] PMORR R1-R8 guarantees remain intact
- [ ] No public schema or contract-version drift remains unresolved
- [ ] Full exact-head Docker-backed CI is green
