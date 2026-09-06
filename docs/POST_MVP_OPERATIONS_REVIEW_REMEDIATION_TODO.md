# Post-MVP Operations Review Remediation TODO

**Status:** active follow-up workstream  
**Spec:** `docs/POST_MVP_OPERATIONS_REVIEW_REMEDIATION_SPEC.md`  
**Base:** completed Post-MVP Operations workstream at or after `97ff037b6a2ce731340c89d2a08375f7531eac5b`  
**Task series:** `R1`..`R8`  
**Requirement series:** `PMORR-001`..`PMORR-007`  
**Acceptance series:** `PMORRAT-001`..`PMORRAT-007`

This TODO tracks the remediation pass created from the Post-MVP Operations code review. It does not reopen O1-O12. Each task must preserve the completed O1-O12 destructive-safety invariants: exact positive ownership, immutable plans, explicit confirmation, immediate revalidation, candidate-scoped execution, and no broad prune fallback.

## Global rules

- [ ] Work on `post-mvp/review-remediation`, not directly on `master`
- [ ] Add or strengthen acceptance tests before implementation for behavioral tasks
- [ ] Prove the acceptance test fails for the intended missing behavior before the fix
- [ ] Preserve all O1-O12 destructive-safety invariants
- [ ] Do not expose host paths, secrets, Docker credentials, SSH material, raw environment values, prompt text, arbitrary client metadata, or arbitrary raw failure strings in audit or public output
- [ ] Regenerate schemas and bump `contract_version` when public MCP schema/output/description changes require it
- [ ] Run focused tests before full CI
- [ ] Record exact RED/GREEN evidence for each task
- [ ] Revert-verify behavioral acceptance tests
- [ ] Merge only after exact-head CI is green and the user authorizes merge
- [ ] Verify post-merge `master` CI before marking the workstream complete

## Phase 0 — Audit attribution

### R1 — Add request attribution to reclamation audit

**Satisfies:** PMORR-001  
**Verified by:** PMORRAT-001

- [x] Identify whether the MCP framework exposes a safe request/session/caller identifier at tool invocation time
- [x] If no safe framework value exists, generate an opaque per-tool-call request correlation ID
- [x] Thread bounded request attribution into `write_audit_event` without exposing unsafe metadata
- [x] Ensure `resource_prune_plan` audit records include non-null request attribution
- [x] Ensure `resource_prune_execute` audit records include non-null request attribution
- [x] Preserve existing PMOR-012 plan/candidate/outcome/reclaimed-byte audit metadata
- [x] Ensure attribution fields do not contain host paths, secrets, prompt text, raw authorization data, Docker credentials, SSH material, or raw environment values
- [x] Preserve audit health behavior when audit writes fail
- [x] Add PMORRAT-001 through the real MCP tool surface
- [x] Prove PMORRAT-001 RED against current null `request_id`/`session_id` behavior
- [x] Run focused audit/prune tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify PMORRAT-001

**R1 completion evidence:**

- RED run/head/job: `34019098378` / `43b023f593cb64c7442fc161575026204498288f` / `101448247761`.
- RED failure shape: clean behavioral RED after the PMORRAT naming collision was removed; Docker build, Ruff format, Ruff lint, mypy, schema export/drift, contract-version, and docs drift all passed, then pytest failed only `tests/tools/test_post_mvp_review_remediation_audit_attribution.py::test_r1_reclamation_audit_has_request_attribution` because `resource_prune_plan` emitted an audit record with `request_id: None`; final count `1 failed, 1184 passed`, coverage `92%`.
- Implementation summary: `write_audit_event` now accepts optional bounded `request_id`/`session_id` values and drops unsafe values fail-closed; `resource_prune_plan` and `resource_prune_execute` generate opaque per-tool-call `rq_<uuid>` attribution IDs when no safe framework ID is available; PMOR-012 plan/candidate/outcome/reclaimed-byte audit metadata is preserved; public MCP schema/output is unchanged.
- Focused audit/prune evidence: covered inside GREEN full CI by passing `tests/test_audit.py`, `tests/tools/test_post_mvp_review_remediation_audit_attribution.py`, `tests/tools/test_post_mvp_o12_reclamation_audit.py`, `tests/tools/test_resource_prune_execute.py`, `tests/tools/test_resource_prune_partial_failure.py`, and `tests/tools/test_resource_prune_plan.py`.
- GREEN run/head/job/test count/coverage: `34019843500` / `aafc279cac595105afa23d75f1ab4aee8d369768` / `101450264818`; Docker build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, docs/TOOLS drift check, and pytest all passed; pytest count `1186 passed in 641.90s (0:10:41)`; coverage `92%` with TOTAL `6513` statements / `544` misses.
- Revert verification: the clean RED run `34019098378` at acceptance-only head `43b023f593cb64c7442fc161575026204498288f` is the revert proof: with the acceptance present and the implementation absent, the real MCP tool surface produced null request attribution and failed PMORRAT-001 exactly.

## Phase 1 — Public and durable redaction

### R2 — Redact public `resource_prune_execute` failure detail

**Satisfies:** PMORR-002  
**Verified by:** PMORRAT-002

- [x] Define the safe public failure-classification allowlist/fallback policy
- [x] Decide whether to constrain existing `reason` values or add a public `failure_classification` field
- [x] If schema/output shape changes, bump `contract_version` and regenerate `schemas/tools.json`
- [x] Update `docs/TOOLS.md` for the public failure-detail policy
- [x] Apply the policy to public per-candidate `resource_prune_execute` output
- [x] Preserve useful known classifications such as `identity_changed`, `secondary_cleanup_failed`, and `deletion_unproven`
- [x] Convert arbitrary raw failure detail to `failure_detail_redacted` or an equivalent safe fallback
- [x] Add PMORRAT-002 with hostile secret/path/environment/Docker/SSH-shaped failure detail
- [x] Prove PMORRAT-002 RED against current public raw-reason behavior
- [x] Verify audit redaction remains intact
- [x] Run focused prune execute/audit tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify PMORRAT-002

**R2 completion evidence:**

- RED run/head/job: `34023768817` / `be14b4dccfcb982a7f68e32607b520207b12c358` / `101461001006`.
- RED failure shape: Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift all passed; pytest then failed only `tests/tools/test_post_mvp_review_remediation_public_redaction.py::test_r2_public_prune_execute_failure_detail_is_classified_redacted` because public `outcomes[].reason` returned the injected raw host-path/authorization/Docker/SSH/environment-shaped failure string instead of `failure_detail_redacted`; final count `1 failed, 1186 passed in 627.94s (0:10:27)`, coverage `92%` with TOTAL `6513` statements / `544` misses.
- Implementation summary: the existing public `reason` field is retained and constrained through a fail-closed allowlist in `tools/prune.py`; useful stable classifications including `identity_changed`, `secondary_cleanup_failed`, and `deletion_unproven` remain visible while arbitrary free-form values become `failure_detail_redacted`. PMOR-012 audit redaction remains intact. `docs/TOOLS.md` now states the same public-output rule explicitly.
- Schema/contract decision: no response field or type changed, so `schemas/tools.json` and `contract_version` did not require a bump; the schema/contract CI gates passed on the implementation and post-merge generations.
- Focused prune/audit evidence: covered inside the GREEN full suite by PMORRAT-002, PMOAT-012, `tests/tools/test_resource_prune_execute.py`, `tests/tools/test_resource_prune_partial_failure.py`, and the reclamation audit tests.
- GREEN run/head/job/test count/coverage: `34023990812` / `d6b1ab97147a20c482a7a29da4695d3c84126e54` / `101461594019`; all gates passed; pytest `1187 passed in 649.32s`, coverage `92%` with TOTAL `6522` statements / `544` misses.
- Merge/post-merge verification: PR #42 was squash-merged as `a13b3a8a84204ebe853a0f09cd85bd95ab6547a6`; post-merge `master` CI `34043698202` / job `101514867270` passed with `1187 passed in 617.68s` and TOTAL `6522` / `544` at `92%` coverage.
- Revert verification: the clean acceptance-only RED run `34023768817` is the revert proof: with PMORRAT-002 present and the public-output sanitization absent, the real MCP surface exposed the raw hostile reason and failed exactly the new requirement.

### R3 — Sanitize durable cleanup error storage

**Satisfies:** PMORR-004  
**Verified by:** PMORRAT-004

- [x] Inventory durable cleanup error fields for job-log cleanup and SSH-agent cleanup
- [x] Define allowed durable error classification/category/errno fields
- [x] Replace arbitrary job-log cleanup raw exception persistence with safe bounded classification data where feasible
- [x] Confirm SSH-agent cleanup persistence already follows the safer path or align it if needed
- [x] Preserve retry visibility and operator-useful failure distinction
- [x] Add migration/backward-compatibility handling if schema changes
- [x] Ensure legacy raw durable error text is not exposed through inventory, audit, health, or logs
- [x] Add PMORRAT-004 for secret/path-shaped cleanup exceptions
- [x] Prove PMORRAT-004 RED against current raw job-log durable error storage, if applicable
- [x] Run focused job retention, SSH cleanup, inventory, and maintenance tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify PMORRAT-004

**R3 completion evidence:**

- RED run/head/job: `34046819180` / `61c409cebfc6b3bd320bb41a4ea766ce6d21e9e3` / `101523226967`.
- RED failure shape: all non-pytest gates passed, then both PMORRAT-004 assertions failed for the intended pre-fix behavior: new `job_log_cleanup.last_error` stored raw `PermissionError` text containing host-path/Docker/SSH-shaped detail, and maintenance health republished a legacy raw durable `last_error`; final count `2 failed, 1187 passed in 621.84s (0:10:21)`, coverage `92%` with TOTAL `6522` statements / `540` misses.
- Implementation summary: job-log cleanup now persists a bounded classification plus exception class and optional errno (for example `cleanup_failed: PermissionError: errno=13`) instead of `str(exc)`; path-bearing exception text is also removed from the affected operational log messages. Durable job-log debt reads accept only the bounded safe format and map legacy/free-form values fail-closed to `cleanup_failure_recorded`, preventing legacy text from propagating into maintenance health, inventory, prune planning, or audit surfaces. SSH-agent cleanup already used the safer reason + class/errno pattern and was retained; PMORRAT-004 locks that behavior in alongside the job-log fix. Retry tombstones, timestamps, and byte accounting remain unchanged.
- Migration/schema decision: no SQLite schema change is required. Both tables retain their existing nullable `last_error TEXT` columns. New job-log writes are bounded safe classifications; backward compatibility is handled at the durable read boundary so old free-form rows remain retryable but are represented externally as `cleanup_failure_recorded`. Therefore no DB migration, public MCP schema change, or `contract_version` bump is required.
- Focused retention/SSH/inventory/maintenance evidence: covered inside the GREEN full suite by `tests/jobs/test_retention.py`, PMORRAT-004, `tests/tools/test_resource_inventory.py`, prune execution/partial-failure tests, `tests/workspace/test_gap3_job_log_cleanup_debt.py`, `tests/workspace/test_post_mvp_o11_conservative_maintenance.py`, and SSH-agent cleanup/lifecycle tests.
- GREEN run/head/job/test count/coverage: `34049300413` / `27e4c81fd8415313ed576dcdfd4cd39321e7c56d` / `101529902230`; Docker build, Ruff format (`229 files already formatted`), Ruff lint, mypy (`72 source files`), MCP contract, contract-version, docs drift, and pytest all passed; pytest `1189 passed in 628.54s (0:10:28)`, coverage `92%` with TOTAL `6532` statements / `540` misses.
- Revert verification: the clean acceptance-only RED run `34046819180` at `61c409cebfc6b3bd320bb41a4ea766ce6d21e9e3` is the revert proof: with PMORRAT-004 present and R3 sanitization absent, both the new-write durable storage and legacy-health republishing failures reproduced exactly.

## Phase 2 — Acceptance and documentation hardening

### R4 — Enforce PMOAT and PMORRAT acceptance coverage

**Satisfies:** PMORR-003  
**Verified by:** PMORRAT-003

- [x] Extend `tests/test_acceptance_test_coverage.py` or its backing metadata to enforce `PMOAT-001`..`PMOAT-012`
- [x] Register `PMORRAT-001`..`PMORRAT-007` as this workstream's acceptance series
- [x] Ensure missing required IDs fail the meta-test
- [x] Ensure unknown/nonexistent claimed IDs fail the meta-test
- [x] Distinguish executable acceptance tests from incidental docs-only mentions
- [x] Document the process for adding future acceptance series
- [x] Add PMORRAT-003 proving the meta-test catches both missing and unknown IDs
- [x] Prove PMORRAT-003 RED against current lack of PMOAT coverage enforcement
- [x] Run focused meta-test and representative acceptance tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify PMORRAT-003

**R4 completion evidence:**

- RED run/head/job: `34052582734` / `8aa42963c15cb186692aedcc4ad1471cbab33f71` / `101538714269`.
- RED failure shape: Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift all passed; pytest failed only `tests/test_post_mvp_review_remediation_acceptance_coverage.py::test_r4_missing_and_unknown_acceptance_ids_fail_closed` because `PMOAT` was absent from `_BY_PREFIX`; final count `1 failed, 1189 passed in 615.68s (0:10:15)`, coverage `92%` with TOTAL `6532` statements / `540` misses.
- Implementation summary: `tests/test_acceptance_test_coverage.py` now registers the complete `PMOAT-001..012` and `PMORRAT-001..007` series, binds requirement-named criteria to exact live pytest nodes, rejects unknown IDs claimed by the authoritative operations/remediation spec+TODO pairs, and makes acceptance-family matching prefix-safe so longer families such as `PMOAT`/`PMORRAT` cannot accidentally satisfy `AT`/`RAT`. The stricter matcher exposed latent `AT-006` and `AT-010` dependencies on suffix matches, so those criteria are now bound explicitly to their real executable tests. PMORRAT-005..007 are formally deferred to R5..R7 until their executable proofs are added, and `docs/GAP3_ACCEPTANCE_COVERAGE_SPEC.md` documents how future acceptance families must be registered. No public MCP schema/output changed; schema, contract-version, and docs-drift gates remained green.
- Focused evidence: the acceptance/meta-audit focused simulation passed `14/14` before publication; the exact implementation CI then passed `tests/test_acceptance_test_coverage.py`, PMORRAT-003, all existing PMOAT tests, and the representative exact-node targets under the complete Docker-backed suite.
- GREEN run/head/job/test count/coverage: `34054232951` / `95f937b414242fe3ae485c4c939f48688c981e04` / `101543066829`; Docker build, Ruff format (`230 files already formatted`), Ruff lint, mypy (`72 source files`), MCP contract, contract-version, docs drift, and pytest all passed; pytest `1191 passed in 628.76s (0:10:28)`, coverage `92%` with TOTAL `6532` statements / `540` misses.
- Revert verification: the clean acceptance-only RED run `34052582734` at `8aa42963c15cb186692aedcc4ad1471cbab33f71` is the revert proof: with PMORRAT-003 present and the acceptance-series enforcement absent, the suite failed exactly because `PMOAT` was not registered.

### R5 — Align tool descriptions, exported schemas, and docs

**Satisfies:** PMORR-005  
**Verified by:** PMORRAT-005

- [ ] Audit registered descriptions for `resource_inventory`, `resource_prune_plan`, `resource_prune_execute`, and `server_info`
- [ ] Update descriptions to reflect current operations scope without overstating behavior
- [ ] Keep `docs/TOOLS.md` aligned with actual public behavior and maintenance boundaries
- [ ] Determine whether description changes affect exported `schemas/tools.json`
- [ ] If exported schema changes, regenerate schemas and bump `contract_version`
- [ ] Strengthen docs/schema drift checks if they only compare tool names and miss relevant descriptions
- [ ] Add PMORRAT-005 for description/docs drift
- [ ] Prove PMORRAT-005 RED against current stale/narrow description behavior, if feasible
- [ ] Run schema export, contract-version, and docs drift checks
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-005

**R5 completion evidence:**

- RED run/head/job: TODO
- RED failure shape or justified non-behavioral disposition: TODO
- Implementation summary: TODO
- Schema/contract decision: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

## Phase 3 — Cleanup and explicit bounds

### R6 — Remove obsolete SSH cleanup helper code

**Satisfies:** PMORR-006  
**Verified by:** PMORRAT-006

- [ ] Confirm current callers for `operations/ssh_agent_cleanup.py` helpers
- [ ] Remove obsolete unused inventory augmentation helper code if no caller exists
- [ ] Remove now-unused imports/types introduced only for obsolete helper code
- [ ] Preserve inventory, maintenance, prune planning, and prune execution behavior
- [ ] Avoid public schema/output changes
- [ ] Add or strengthen focused tests only if needed to protect behavior after removal
- [ ] Add PMORRAT-006 no-regression/dead-code-removal proof
- [ ] Run focused SSH cleanup, maintenance, inventory, and prune tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify or document why dead-code removal has no behavioral RED

**R6 completion evidence:**

- Caller audit: TODO
- Removal summary: TODO
- GREEN run/head/job/test count/coverage: TODO
- No-regression/revert rationale: TODO

### R7 — Close Docker-volume inventory bound documentation/test gap

**Satisfies:** PMORR-007  
**Verified by:** PMORRAT-007

- [ ] Document the Docker SDK/API limitation: volume listing lacks container-style server-side limit
- [ ] Confirm volume discovery remains ownership-filtered and read-only
- [ ] Confirm per-volume inspection/output/diagnostic work is locally bounded
- [ ] Add fake-Docker oversized volume-list test for post-list bounded inspection/result work
- [ ] Verify truncated/incomplete state is reported when bounds are exceeded
- [ ] Verify unavailable/over-limit accounting is not converted to zero
- [ ] Update `docs/TOOLS.md` or operations docs with the explicit limitation
- [ ] Add PMORRAT-007 acceptance coverage
- [ ] Prove PMORRAT-007 RED if current tests do not catch oversized post-list behavior
- [ ] Run focused inventory tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-007

**R7 completion evidence:**

- RED run/head/job: TODO
- RED failure shape or justified disposition: TODO
- Implementation/documentation summary: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

## Phase 4 — Final remediation closure

### R8 — Final remediation audit and closure

**Satisfies:** PMORR-001..PMORR-007  
**Verified by:** PMORRAT-001..PMORRAT-007

- [ ] Audit that PMORR-001..PMORR-007 are implemented or explicitly dispositioned
- [ ] Audit that PMORRAT-001..PMORRAT-007 are automated and passing
- [ ] Confirm PMOAT-001..PMOAT-012 and PMORRAT-001..PMORRAT-007 are enforced by acceptance coverage metadata/tests
- [ ] Confirm public prune execution output and audit output do not expose hostile failure detail
- [ ] Confirm reclamation audit includes bounded request attribution
- [ ] Confirm durable cleanup error storage avoids arbitrary raw exception detail for covered cleanup classes
- [ ] Confirm tool descriptions, exported schemas, `contract_version`, and `docs/TOOLS.md` are aligned
- [ ] Confirm obsolete helper code is removed or justified
- [ ] Confirm Docker-volume inventory scaling limitation is tested/documented
- [ ] Re-scan source/docs for broad prune fallback: `docker system prune`, `containers.prune`, `volumes.prune`, `images.prune`, label-wide, prefix-wide, path-wide, system-wide
- [ ] Run complete Docker-backed CI with all gates green
- [ ] Mark this remediation TODO complete only after exact-head CI passes
- [ ] Merge with an exact-head guard
- [ ] Verify post-merge `master` CI is green

**R8 completion evidence:**

- Final closure run/head/job/test count/coverage: TODO
- Broad-prune scan result: TODO
- Exact merge SHA: TODO
- Post-merge master CI run/head/job/test count/coverage: TODO

## Definition of done

- [ ] R1-R8 complete
- [ ] PMORR-001..PMORR-007 implemented or explicitly dispositioned
- [ ] PMORRAT-001..PMORRAT-007 automated and passing
- [ ] Reclamation audit records include bounded request/caller attribution
- [ ] Audit output remains redacted and path-safe
- [ ] Public `resource_prune_execute` output does not expose arbitrary raw failure detail
- [ ] Durable cleanup error storage avoids arbitrary raw exception text for covered cleanup classes
- [ ] PMOAT and PMORRAT acceptance coverage is enforced by meta-tests
- [ ] Tool descriptions, schemas, `contract_version`, and `docs/TOOLS.md` are aligned
- [ ] Dead operational helper code is removed or justified
- [ ] Docker-volume inventory scaling limitation is documented and tested where feasible
- [ ] No broad prune fallback exists in source, comments, or docs
- [ ] Full exact-head CI is green
- [ ] PR is merged with an exact-head guard
