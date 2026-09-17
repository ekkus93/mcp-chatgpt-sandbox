# Post-R8 Code Review Hardening TODO

**Status:** CRH-001 through CRH-006 complete  
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
- [x] Run full Docker-backed CI before marking this workstream complete
- [x] Record exact RED/GREEN/revert evidence for each task where feasible
- [x] Restore Ralph Bridge default-branch write protection unless there is a new explicit operator-approved exception

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
- Implementation-shape cleanup commit: `ad0bf2b480937f2fa3e6e109bf8bc4d2851bb9f8`; this moved the bool guard directly into `src/mcp_chatgpt_sandbox/operations/inventory.py` and restored `src/mcp_chatgpt_sandbox/operations/__init__.py` to a plain package marker, eliminating the temporary import-time wrapper used by the initial fix.
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
- Follow-up note: PCRF-001 later tightened this same audit boundary so pure credential/token-shaped values also fail closed even when they otherwise match the audit identifier alphabet.

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
- Implementation commit: `c73d2b9b34e3f131ab93b567b09ee3ce7e62cbdc`; expanded generic redaction patterns in `src/mcp_chatgpt_sandbox/errors.py`.
- Formatting/lint repair commits: `ae110d75ef3d8f4745b304fed822196c1e9b8231`, `ca363d6dbeb1750a79bb9c4e269b621ce166d68d`, `a699f5e89f07df9b8baf553f704b56288803fc37`, `11bd53c9dfa32c9aa2bdec1d180bcecb42980f60`, `1396fca9fda011c89dc9b7a1ac9aee23efbc8ec0`, and `e9384065064d0f1eaf6723f1beaa4858db018f05`; these fixed Ruff formatting/import-order issues in the new test file and source formatting.
- Correctness repair commit: `780442f07d6a7adc57418527d4ed3742e893b20a`; preserved known safe opaque internal IDs from the high-entropy fallback while broadening OpenAI-style token matching for project-token-like values.
- GREEN CI: run `34546959288`, job `103101561803`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed.
- Implementation detail: generic redaction now covers representative OpenAI-style, GitHub-token-style, AWS-access-key-ID-style, bearer-token-style, common assignment-style, and high-entropy token-like values using synthetic fixtures that avoid committing real-looking secrets. False-positive guards preserve commit SHAs, UUIDs, bounded request IDs, safe internal prune-plan/candidate IDs, and public failure classifications.
- Follow-up regression repair: commit `37dd173df34ba7b55626ac3961e3ab50fb4b4af4` preserved public `SHA256:` SSH audit fingerprints that CRH-003's high-entropy fallback had incorrectly redacted. CI run `34558844465`, job `103137187818`, passed Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: clean behavioral RED evidence was not retained for CRH-003 because the test-only attempt failed before pytest at Ruff format, and subsequent commits combined source/test formatting and implementation repairs. The retained evidence is the final exact-head GREEN run plus the earlier failed CRH-003 run documenting that the initial test-only attempt was structurally blocked before behavioral assertion execution.

## Phase 4 — SSH-agent cleanup durable error normalization

### CRH-004 — Normalize SSH-agent cleanup legacy errors at read/model boundary

**Satisfies:** CRH-004  
**Validated by:** CRHV-004

- [x] Locate SSH-agent cleanup durable row-to-model construction
- [x] Compare job-log cleanup legacy error normalization behavior
- [x] Define SSH-agent legacy `last_error` safe classification behavior
- [x] Add focused tests that seed legacy SSH cleanup debt with host-path-shaped raw errors
- [x] Add focused tests that seed legacy SSH cleanup debt with Docker/SSH/environment/token-shaped raw errors
- [x] Confirm resulting internal debt model does not carry raw legacy exception text
- [x] Confirm health output does not expose raw legacy SSH cleanup error text
- [x] Confirm inventory output does not expose raw legacy SSH cleanup error text
- [x] Confirm prune planning output does not expose raw legacy SSH cleanup error text
- [x] Confirm prune execution/audit output does not expose raw legacy SSH cleanup error text
- [x] Preserve retry metadata, timestamps, socket identity, ownership metadata, and safe failure distinctions
- [x] Avoid SQLite schema changes unless strictly necessary
- [x] Preserve active SSH cleanup APIs verified by R6
- [x] Run focused SSH cleanup, maintenance, inventory, and prune tests
- [x] Run full CI and record exact GREEN evidence
- [x] Revert-verify CRHV-004 if feasible

**CRH-004 completion evidence:**

- Acceptance-test commit: `d3254dde1e60d55fba124cb4e81a8ccdadf3cf0c`.
- Clean RED CI: run `34548721892`, job `103106877586`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, and `docs/TOOLS.md` drift check all passed; pytest then failed only `tests/operations/test_post_r8_code_review_hardening_ssh_cleanup.py::test_crhv_004_ssh_cleanup_debt_sanitizes_legacy_raw_last_error` because `SSHAgentCleanupDebt.last_error` still carried raw legacy text containing a host path, Docker environment path, SSH socket path, and authorization-shaped text. Final count `1 failed, 1225 passed`; coverage `92%` with TOTAL `6525` statements / `518` misses.
- Initial implementation commit: `62b8cb9d480c3236a56f8b6bf9ce38b665094ef7`; this added read/model-boundary normalization in `src/mcp_chatgpt_sandbox/operations/ssh_agent_cleanup.py`. Its CI run `34548804523`, job `103107131007`, failed one existing audit fingerprint test because the earlier CRH-003 high-entropy fallback incorrectly redacted public `SHA256:` SSH fingerprints; this was a CRH-003 regression, not a CRH-004 sanitizer failure.
- Correctness repair commit: `37dd173df34ba7b55626ac3961e3ab50fb4b4af4`; preserved public `SHA256:` SSH fingerprints while retaining CRH-004's legacy SSH cleanup sanitizer.
- GREEN CI: run `34558844465`, job `103137187818`; Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed.
- Implementation detail: `operations.ssh_agent_cleanup._debt_from_row()` now sanitizes durable `last_error` before constructing `SSHAgentCleanupDebt`. Safe current classifications are preserved, including identity/probe/deletion reason strings and bounded `ClassName: errno=N` forms. Unsafe legacy raw text fails closed to `cleanup_failure_recorded`. Retry metadata, timestamps, socket identity, ownership metadata, and active SSH cleanup APIs are unchanged. No SQLite schema change was required.
- Contract/schema decision: no public MCP schema, registered tool description, or response shape changed; no schema regeneration or `contract_version` bump was required.
- Revert verification: the clean RED run `34548721892` is the revert proof: with CRHV-004 present and the read/model-boundary sanitizer absent, the suite failed exactly on the raw legacy `last_error` assertion after all non-pytest gates passed.

## Phase 5 — Ralph Bridge operator policy hygiene

### CRH-005 — Restore default-branch write protection after R8 direct publication

**Satisfies:** CRH-005  
**Validated by:** CRHV-005

- [x] Identify the active Ralph Bridge operator setting that controls default-branch writes (effective Phase 4 default-branch direct-write policy; private operator config remains external)
- [x] Restore direct default-branch write protection for `ekkus93/mcp-chatgpt-sandbox` unless a new explicit exception is active
- [x] Restart or refresh Ralph Bridge if required by the operator configuration change (effective reload verified by changed enforcement behavior)
- [x] Verify normal source-branch writes remain available if needed for future work
- [x] Verify exact-head guarded PR merges remain available if needed for future work
- [x] Verify direct writes to `master` are rejected by policy after restoration
- [x] Record the verification method without committing private operator config or secrets
- [x] Update repository operator documentation if this repo contains the relevant Ralph Bridge setup guidance (N/A: no repo-local operator setup guidance)
- [x] Run any docs or CI checks affected by documentation updates
- [x] Record exact evidence for CRHV-005

**CRH-005 completion evidence:**

- First direct-write probe commit `b30a284eca83d03a4b74afd6c0523e508981e3dd` landed on `master`; this proved default-branch source writes were still allowed at that time.
- Second direct-write probe commit `74c14331622e88c815a6fb3eeef2aa1ca5b20a0c` also landed on `master` through Ralph Bridge `commit_files`; this confirmed that the original policy restoration attempt had not taken effect.
- Repository inspection found no Ralph Bridge operator configuration or setup file that belongs in this repository. `docs/SPEC.md` §21 documents only the responsibility boundary with Ralph Bridge; private operator configuration remains external.
- After the operator configuration was restored, CRHV-005 was repeated on 2026-09-12 against exact `master` SHA `e4e0b179c7b4b1ab9d22740d586456cd62f5dbdc` using Ralph Bridge `commit_files`, the same mutation path that had previously succeeded.
- Ralph Bridge rejected the direct `master` write before creating a commit with `BRANCH_POLICY_VIOLATION: Direct writes to the repository default branch are disabled by Phase 4 policy.` The `master` ref did not move. This is the positive CRHV-005 result.
- Source-branch writes remained available after restoration: `ralph/crh-005-006-close` was created from exact `master` SHA `e4e0b179c7b4b1ab9d22740d586456cd62f5dbdc`, and evidence commit `6f00f0fda2e43d7ee5ccc8a2cc82a8eed34f7aa8` was accepted on that branch.
- PR #51 targeted `master`; exact-head pull-request CI run `34687309482` passed on `6f00f0fda2e43d7ee5ccc8a2cc82a8eed34f7aa8`.
- PR #51 merged successfully through the exact-head guarded Ralph Bridge merge path, producing `master` commit `32551a1214f114b29f824d43887f03220f333df3`. This proves guarded PR merges remain available under the restored policy.
- Post-merge Docker-backed CI run `34687791982`, job `103537728294`, passed on `32551a1214f114b29f824d43887f03220f333df3`: Docker image build, Ruff format, Ruff lint, mypy, MCP contract check, contract-version check, `docs/TOOLS.md` drift check, and pytest all passed; `1226 passed`, `4 warnings` in `615.78s`; coverage `92%` with TOTAL `6536` statements / `518` misses.
- Verification details are also recorded in `docs/CRH_005_DIRECT_WRITE_PROBE.md` without private operator configuration or secrets.

## Final closure

### CRH-006 — Final hardening audit and closure

- [x] Audit that CRH-001..CRH-005 are implemented or explicitly dispositioned
- [x] Audit that CRHV-001..CRHV-005 evidence is recorded
- [x] Confirm no broad-prune or unsafe fallback behavior was introduced
- [x] Confirm public schemas and `contract_version` are correct for the actual changes
- [x] Confirm `docs/TOOLS.md` and exported schemas are drift-free
- [x] Confirm all focused tests pass
- [x] Run complete Docker-backed CI with all gates green
- [x] Verify final exact-head CI is green before marking this workstream complete
- [x] Record final exact run/head/job/test count/coverage

**CRH-006 completion evidence:**

- Audit range: frozen hardening base `5d64af1a92bbe83154ab3eebdfd20c1b56495cd9` through final policy-restored `master` `86440755c84a817a534de60293cacb5c35795a65`.
- The retained runtime changes are limited to `audit.py`, `errors.py`, `operations/inventory.py`, and `operations/ssh_agent_cleanup.py`, with focused tests and hardening documentation. No prune-plan, prune-candidate, prune-execution, Docker cleanup, or broad-deletion source file changed in the audited hardening range.
- CRH-001 through CRH-004 are implemented with recorded RED/GREEN evidence. CRH-005 is complete: the same Ralph Bridge mutation path that previously wrote directly to `master` is now rejected by Phase 4 default-branch policy, while source-branch writes and guarded PR merges remain available.
- Final bookkeeping PR #52 merged through the exact-head guarded Ralph Bridge merge path and produced `master` commit `86440755c84a817a534de60293cacb5c35795a65`.
- Final exact-head policy-restored `master` CI run `34691630548`, job `103547787452`, passed all Docker build, Ruff format/lint, mypy, MCP contract, contract-version, `docs/TOOLS.md` drift, and pytest gates on `86440755c84a817a534de60293cacb5c35795a65`.
- Final test result: `1226 passed`, `4 warnings` in `613.65s`; coverage `92%`, TOTAL `6536` statements / `518` misses.
- Passing contract-version and docs-drift gates confirm no unresolved public schema/contract drift.
- The Post-R8 Code Review Hardening workstream is complete.

## Definition of done

- [x] CRH-001..CRH-005 implemented or explicitly dispositioned
- [x] CRHV-001 evidence recorded
- [x] CRHV-002 evidence recorded
- [x] CRHV-003 evidence recorded
- [x] CRHV-004 evidence recorded
- [x] Boolean Docker volume sizes are unavailable, not byte counts
- [x] Top-level audit `workspace_id` and `repository_id` values are sanitized at the audit boundary
- [x] Generic redaction covers representative modern token/key shapes with false-positive guards
- [x] SSH-agent cleanup legacy durable errors are normalized at the read/model boundary
- [x] Ralph Bridge default-branch write protection is restored or an explicit exception is documented
- [x] O1-O12 destructive-safety invariants remain intact
- [x] PMORR R1-R8 guarantees remain intact
- [x] No public schema or contract-version drift remains unresolved
- [x] Full exact-head Docker-backed CI is green
