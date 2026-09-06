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

- [ ] Identify whether the MCP framework exposes a safe request/session/caller identifier at tool invocation time
- [ ] If no safe framework value exists, generate an opaque per-tool-call request correlation ID
- [ ] Thread bounded request attribution into `write_audit_event` without exposing unsafe metadata
- [ ] Ensure `resource_prune_plan` audit records include non-null request attribution
- [ ] Ensure `resource_prune_execute` audit records include non-null request attribution
- [ ] Preserve existing PMOR-012 plan/candidate/outcome/reclaimed-byte audit metadata
- [ ] Ensure attribution fields do not contain host paths, secrets, prompt text, raw authorization data, Docker credentials, SSH material, or raw environment values
- [ ] Preserve audit health behavior when audit writes fail
- [ ] Add PMORRAT-001 through the real MCP tool surface
- [ ] Prove PMORRAT-001 RED against current null `request_id`/`session_id` behavior
- [ ] Run focused audit/prune tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-001

**R1 completion evidence:**

- RED run/head/job: TODO
- RED failure shape: TODO
- Implementation summary: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

## Phase 1 — Public and durable redaction

### R2 — Redact public `resource_prune_execute` failure detail

**Satisfies:** PMORR-002  
**Verified by:** PMORRAT-002

- [ ] Define the safe public failure-classification allowlist/fallback policy
- [ ] Decide whether to constrain existing `reason` values or add a public `failure_classification` field
- [ ] If schema/output shape changes, bump `contract_version` and regenerate `schemas/tools.json`
- [ ] Update `docs/TOOLS.md` for the public failure-detail policy
- [ ] Apply the policy to public per-candidate `resource_prune_execute` output
- [ ] Preserve useful known classifications such as `identity_changed`, `secondary_cleanup_failed`, and `deletion_unproven`
- [ ] Convert arbitrary raw failure detail to `failure_detail_redacted` or an equivalent safe fallback
- [ ] Add PMORRAT-002 with hostile secret/path/environment/Docker/SSH-shaped failure detail
- [ ] Prove PMORRAT-002 RED against current public raw-reason behavior
- [ ] Verify audit redaction remains intact
- [ ] Run focused prune execute/audit tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-002

**R2 completion evidence:**

- RED run/head/job: TODO
- RED failure shape: TODO
- Implementation summary: TODO
- Schema/contract decision: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

### R3 — Sanitize durable cleanup error storage

**Satisfies:** PMORR-004  
**Verified by:** PMORRAT-004

- [ ] Inventory durable cleanup error fields for job-log cleanup and SSH-agent cleanup
- [ ] Define allowed durable error classification/category/errno fields
- [ ] Replace arbitrary job-log cleanup raw exception persistence with safe bounded classification data where feasible
- [ ] Confirm SSH-agent cleanup persistence already follows the safer path or align it if needed
- [ ] Preserve retry visibility and operator-useful failure distinction
- [ ] Add migration/backward-compatibility handling if schema changes
- [ ] Ensure legacy raw durable error text is not exposed through inventory, audit, health, or logs
- [ ] Add PMORRAT-004 for secret/path-shaped cleanup exceptions
- [ ] Prove PMORRAT-004 RED against current raw job-log durable error storage, if applicable
- [ ] Run focused job retention, SSH cleanup, inventory, and maintenance tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-004

**R3 completion evidence:**

- RED run/head/job: TODO
- RED failure shape: TODO
- Implementation summary: TODO
- Migration/schema decision: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

## Phase 2 — Acceptance and documentation hardening

### R4 — Enforce PMOAT and PMORRAT acceptance coverage

**Satisfies:** PMORR-003  
**Verified by:** PMORRAT-003

- [ ] Extend `tests/test_acceptance_test_coverage.py` or its backing metadata to enforce `PMOAT-001`..`PMOAT-012`
- [ ] Register `PMORRAT-001`..`PMORRAT-007` as this workstream's acceptance series
- [ ] Ensure missing required IDs fail the meta-test
- [ ] Ensure unknown/nonexistent claimed IDs fail the meta-test
- [ ] Distinguish executable acceptance tests from incidental docs-only mentions
- [ ] Document the process for adding future acceptance series
- [ ] Add PMORRAT-003 proving the meta-test catches both missing and unknown IDs
- [ ] Prove PMORRAT-003 RED against current lack of PMOAT coverage enforcement
- [ ] Run focused meta-test and representative acceptance tests
- [ ] Run full CI and record exact GREEN evidence
- [ ] Revert-verify PMORRAT-003

**R4 completion evidence:**

- RED run/head/job: TODO
- RED failure shape: TODO
- Implementation summary: TODO
- GREEN run/head/job/test count/coverage: TODO
- Revert verification: TODO

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
- [ ] Post-merge `master` CI is green
