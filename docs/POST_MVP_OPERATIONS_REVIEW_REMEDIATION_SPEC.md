# Post-MVP Operations Review Remediation Spec

**Status:** proposed follow-up workstream  
**Date:** 2026-09-06  
**Base:** completed Post-MVP Operations workstream at or after `97ff037b6a2ce731340c89d2a08375f7531eac5b`  
**Requirement series:** `PMORR-001`..`PMORR-007`  
**Acceptance series:** `PMORRAT-001`..`PMORRAT-007`

This workstream remediates issues found during the post-merge code review of the completed Post-MVP Operations workstream. It does not reopen or rewrite O1-O12. Instead, it adds targeted follow-up requirements for audit completeness, public-output redaction, acceptance-coverage enforcement, durable error hygiene, tool-description alignment, dead-code cleanup, and explicit Docker-volume inventory bounds.

The central rule is:

> A completed reclamation system is not fully operationally reviewable unless its audit trail identifies the bounded request context, its public failure surfaces do not leak arbitrary host detail, and its acceptance coverage cannot silently drift away from the requirements it claims to verify.

## Goals

1. Add bounded request/caller attribution to reclamation audit events without weakening redaction guarantees.
2. Prevent public `resource_prune_execute` output from exposing arbitrary raw internal failure detail.
3. Extend acceptance coverage enforcement to the `PMOAT` and `PMORRAT` series.
4. Ensure durable cleanup error state does not persist arbitrary raw exception text that could later become public or audit-visible.
5. Keep public tool descriptions, exported schemas, `contract_version`, and `docs/TOOLS.md` aligned.
6. Remove obsolete operational helper code without changing runtime semantics.
7. Make Docker-volume inventory scaling limits explicit, tested where feasible, and documented as intentional.

## Non-goals

- Do not introduce automatic age- or pressure-based reclamation.
- Do not add broad Docker cleanup, `docker system prune`, collection prune, label-wide prune, prefix-wide prune, or path-wide deletion.
- Do not weaken exact positive ownership, immutable prune plans, explicit confirmation, immediate revalidation, or candidate-scoped execution.
- Do not expose host filesystem paths, SSH key material, raw environment values, Docker credentials, raw authorization headers, arbitrary client metadata, prompt text, or arbitrary raw failure strings in audit or public output.
- Do not use this workstream to redesign unrelated tool surfaces.

## Definitions

### Request attribution

A request attribution is a bounded, non-secret correlation identity attached to a tool invocation. It MAY include a server-generated request ID, a framework-provided safe request ID, a safe session ID, or a sanitized caller classification. It MUST NOT include raw authorization headers, OAuth tokens, GitHub tokens, API keys, SSH material, raw environment variables, arbitrary client metadata, user prompt text, or host filesystem paths.

If no external request/session identity is available, the server SHALL generate an opaque per-tool-call request correlation ID so audit records can identify the invocation that produced them.

### Public failure detail

Public failure detail means any field returned by an MCP tool response that describes why a candidate failed, became stale, or was not reclaimed. It includes structured output such as `resource_prune_execute.outcomes[].reason`.

### Sanitized failure classification

A sanitized failure classification is a short allowlisted code or fail-closed fallback. Examples include `action_failed`, `deletion_probe_failed`, `deletion_unproven`, `identity_changed`, `identity_probe_failed`, `secondary_cleanup_failed`, `unsupported_action`, `unsupported_candidate_kind`, and `failure_detail_redacted`. Arbitrary raw exception text SHALL NOT become a sanitized failure classification.

## Requirements

### PMORR-001 — Reclamation audit events SHALL include bounded request attribution

`resource_prune_plan` and `resource_prune_execute` audit records SHALL include enough bounded request attribution to reconstruct which MCP tool invocation created or executed a plan.

The implementation SHALL:

- emit a non-null request correlation value for every reclamation plan and execution audit event;
- preserve existing PMOR-012 plan identifier and candidate metadata;
- keep request/caller fields bounded and non-secret;
- avoid copying raw authorization headers, environment values, prompt text, arbitrary client metadata, host paths, Docker credentials, SSH material, or tokens;
- allow plan and execute events to be correlated through safe audit fields and the immutable plan ID;
- preserve existing audit-health behavior when audit-file writing fails.

If the MCP framework exposes a safe request/session identifier, the implementation MAY use it. If not, it SHALL generate an opaque per-tool-call ID.

**Acceptance:** `PMORRAT-001`

### PMORR-002 — Public prune-execution output SHALL NOT expose arbitrary raw failure detail

`resource_prune_execute` public structured output SHALL NOT expose arbitrary raw failure strings from internal execution outcomes.

The implementation SHALL:

- apply an allowlist/fallback classification policy to public candidate failure/stale reason fields;
- preserve useful known classifications such as `identity_changed`, `secondary_cleanup_failed`, and `deletion_unproven`;
- convert arbitrary free-form detail to `failure_detail_redacted` or an equivalent safe fallback;
- redact or omit secret-shaped, path-shaped, Docker-credential-shaped, SSH-material-shaped, and environment-shaped detail;
- keep public output deterministic and schema-valid;
- bump `contract_version` and regenerate schemas if public shape changes.

A compatible implementation MAY keep the existing `reason` field but restrict its values to safe classifications. A clearer implementation MAY add `failure_classification`, but that requires the normal schema/docs/contract update.

**Acceptance:** `PMORRAT-002`

### PMORR-003 — Acceptance coverage enforcement SHALL include PMOAT and PMORRAT

The acceptance coverage meta-test SHALL enforce both the completed Post-MVP Operations acceptance series and this remediation series.

The implementation SHALL:

- enforce `PMOAT-001`..`PMOAT-012`;
- enforce `PMORRAT-001`..`PMORRAT-007` as this workstream introduces them;
- fail when a required acceptance ID is missing from executable tests or acceptance metadata;
- fail when docs/TODO claim an unknown acceptance ID;
- distinguish executable acceptance evidence from incidental prose mentions.

**Acceptance:** `PMORRAT-003`

### PMORR-004 — Durable cleanup error storage SHALL avoid arbitrary raw exception text

Durable cleanup debt records SHALL preserve retry visibility and useful failure classification without storing arbitrary raw exception strings that could contain host paths or secrets.

The implementation SHALL:

- inventory durable cleanup error fields for job-log cleanup and SSH-agent cleanup;
- replace arbitrary job-log cleanup raw exception persistence with bounded classification data where feasible;
- preserve retryability and operator-useful distinction among failure classes;
- keep legacy values from leaking through inventory, audit, health, or public tool output;
- add migration/backward-compatibility handling if the persisted schema changes.

**Acceptance:** `PMORRAT-004`

### PMORR-005 — Tool descriptions, exported schemas, and docs SHALL stay aligned

Registered MCP tool descriptions, exported schemas, `contract_version`, and `docs/TOOLS.md` SHALL accurately reflect the implemented behavior.

The implementation SHALL:

- audit `resource_inventory`, `resource_prune_plan`, `resource_prune_execute`, and `server_info` descriptions;
- update stale or narrow descriptions without overstating behavior;
- regenerate `schemas/tools.json` when exported schema content changes;
- bump `contract_version` when required by existing contract rules;
- strengthen drift checks if they only compare tool names and miss description drift.

**Acceptance:** `PMORRAT-005`

### PMORR-006 — Obsolete cleanup helpers SHALL be removed or explicitly justified

Dead operational helper code SHALL not remain silently in the reclamation/cleanup path.

The implementation SHALL:

- audit current callers for SSH-agent cleanup helper functions;
- remove obsolete inventory augmentation helpers if no caller exists;
- remove imports/types used only by dead helpers;
- preserve inventory, maintenance, prune planning, and prune execution behavior;
- avoid public schema/output changes unless explicitly justified.

**Acceptance:** `PMORRAT-006`

### PMORR-007 — Docker-volume inventory bounds SHALL be explicit and tested where feasible

The implementation SHALL explicitly document and test the Docker SDK/API limitation that volume listing lacks container-style server-side pagination/limit.

The implementation SHALL:

- document that Docker volume listing may require one ownership-filtered list call;
- keep volume discovery ownership-filtered and read-only;
- bound per-volume inspection, diagnostics, public output, and accounting after listing;
- report truncation/incomplete state when bounds are exceeded;
- avoid converting over-limit/unavailable byte observations to zero.

**Acceptance:** `PMORRAT-007`

## Acceptance tests

### PMORRAT-001 — Reclamation audit request attribution is present and redacted

Through the real MCP tool surface, create a `resource_prune_plan` and call `resource_prune_execute`. The JSONL audit records for both operations SHALL contain non-null bounded request attribution, SHALL preserve plan/candidate metadata, and SHALL not contain host paths, raw authorization data, tokens, SSH material, or raw environment values.

### PMORRAT-002 — Public prune-execution failure detail is classified/redacted

Inject a mixed-result execution containing secret-shaped, host-path-shaped, Docker-credential-shaped, SSH-material-shaped, and environment-shaped failure text. Both audit output and public MCP output SHALL avoid the raw hostile strings and SHALL expose only safe classifications.

### PMORRAT-003 — PMOAT and PMORRAT coverage drift is caught

Modify a controlled fixture or metadata sample so that a required `PMOAT`/`PMORRAT` ID is missing, and separately so that an unknown ID is claimed. The acceptance coverage meta-test SHALL fail in both cases.

### PMORRAT-004 — Durable cleanup errors are safe to persist

Force job-log cleanup and SSH-agent cleanup failure paths with exception strings containing secrets and host paths. Durable DB state SHALL store only bounded safe classification data and public/audit surfaces SHALL not expose the hostile strings.

### PMORRAT-005 — Tool description/docs drift is detected

Create a controlled schema/docs mismatch involving the operations tool descriptions or documentation rows. CI/meta-tests SHALL fail until the exported schema, contract version, and docs are reconciled according to existing rules.

### PMORRAT-006 — Removing obsolete SSH cleanup helper code is no-regression

After removing obsolete helper code, focused SSH cleanup, inventory, maintenance, and prune tests SHALL still pass, and no public schema/output drift SHALL occur.

### PMORRAT-007 — Docker-volume inventory post-list work is bounded

Using fake Docker volume data larger than configured inventory limits, prove that per-volume inspection/output/diagnostic work is locally bounded, truncation/incomplete state is reported, and unavailable/over-limit byte observations are not converted to zero.

## Required workflow

For every behavioral remediation task:

1. Read the matching `PMORR` and `PMORRAT` sections.
2. Add or strengthen the acceptance test first.
3. Prove the test fails for the intended missing behavior before implementation.
4. Implement only the narrow remediation.
5. Run focused tests.
6. Run full CI.
7. Record RED and GREEN evidence in the TODO, including run ID, job ID, head SHA, failure/pass shape, test count, and coverage.
8. Revert-verify the behavioral acceptance coverage.

## Completion rule

This workstream is complete only after `R1`..`R8` are complete, exact-head CI is green, the remediation PR is merged with an exact-head guard, and post-merge `master` CI is green.
