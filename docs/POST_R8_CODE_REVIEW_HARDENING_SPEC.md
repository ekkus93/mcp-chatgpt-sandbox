# Post-R8 Code Review Hardening Spec

**Status:** proposed follow-up workstream  
**Date:** 2026-09-10  
**Base:** `5d64af1a92bbe83154ab3eebdfd20c1b56495cd9` (`docs: close R8 remediation workstream`)  
**Requirement series:** `CRH-001`..`CRH-005`  
**Validation series:** `CRHV-001`..`CRHV-005`

This specification defines a focused hardening pass for issues found in the post-R8 code review. The R1-R8 Post-MVP Operations Review Remediation workstream is complete and is not reopened by this document. This workstream addresses follow-up risk reduction around type hygiene, audit identifier safety, generic redaction coverage, durable SSH cleanup error normalization, and Ralph Bridge operator policy hygiene.

The workstream is intentionally small. It should not redesign the resource reclamation model, introduce broad cleanup behavior, or change public tool semantics unless a specific requirement below makes that unavoidable.

## Background

R8 closed the Post-MVP Operations Review Remediation workstream with exact-head `master` CI green. The follow-up review found no high-severity bug and no falsely completed R1-R8 task. It did identify five non-blocking issues:

1. Docker volume size parsing accepts `bool` values because Python `bool` is a subclass of `int`.
2. `write_audit_event()` sanitizes request/session identifiers and metadata, but top-level `workspace_id` and `repository_id` are not sanitized at the audit boundary.
3. Generic secret redaction is narrower than the project threat model and should recognize more token/key shapes.
4. SSH-agent cleanup legacy `last_error` values are not normalized at the durable read/model boundary, even though current public surfaces do not expose them.
5. Direct default-branch writes were temporarily enabled to complete an explicitly authorized direct R8 closure after PR #48 was closed; the normal safer posture should be restored.

## Goals

1. Reject boolean Docker volume sizes and preserve unavailable byte observations rather than converting malformed values to zero.
2. Sanitize top-level audit `workspace_id` and `repository_id` values consistently with request/session attribution safety.
3. Expand generic redaction coverage for common API-key, bearer-token, cloud-credential, and high-entropy token-like strings while avoiding obvious false positives such as commit SHAs, bounded request IDs, and benign identifiers.
4. Normalize SSH-agent cleanup legacy error values at the read/model boundary so future internal consumers cannot accidentally republish raw durable exception text.
5. Restore and document the intended Ralph Bridge default-branch write posture after the exceptional R8 direct-publication path.

## Non-goals

- Do not reopen R1-R8 or rewrite their completion evidence.
- Do not introduce broad Docker cleanup, collection-wide prune, `docker system prune`, label-wide deletion, prefix-wide deletion, path-wide deletion, or system-wide fallback cleanup.
- Do not weaken exact positive ownership, immutable prune plans, explicit confirmation, immediate revalidation, candidate-scoped execution, or audit redaction guarantees.
- Do not persist raw authorization values, raw environment values, SSH key material, host filesystem paths, prompt text, arbitrary client metadata, or arbitrary raw failure strings in public or audit-visible output.
- Do not add new MCP tools unless a later implementation task demonstrates that a public contract change is required.
- Do not bump `contract_version` unless exported public schema, registered descriptions, or response semantics change.
- Do not use direct `master` writes as the normal development path after this hardening plan is created.

## Global implementation rules

- Work from an exact inspected base SHA and keep every write guarded by compare-and-swap semantics.
- Prefer a source branch and exact-head guarded merge for implementation work unless the user explicitly authorizes another direct-to-`master` exception.
- Add or strengthen focused tests before implementation where a behavioral gap can be reproduced.
- Record RED/GREEN/revert evidence for each behavioral fix when feasible.
- Run focused tests before full CI.
- Run full Docker-backed CI before marking the workstream complete.
- Keep all new docs aligned with the repository's acceptance-coverage and docs-drift rules.
- Keep new validation labels in the `CRHV-*` series until and unless the repository's acceptance registry is deliberately extended for this workstream.

## Definitions

### Safe audit identifier

A safe audit identifier is a bounded string suitable for durable audit storage and operator-facing audit output. It is either absent or composed only of a conservative identifier character set such as ASCII letters, digits, underscore, hyphen, colon, slash-free dot-separated segments when explicitly accepted, and short opaque prefixes already used by the server. It must not contain path separators that make it look like a host path, whitespace-delimited shell fragments, authorization material, token-shaped substrings, SSH material, or raw environment key/value content.

### Byte observation

A byte observation records resource size information as either a non-negative integer byte count or an unavailable/incomplete state. Malformed, negative, boolean, missing, or unsupported upstream values must remain unavailable rather than being converted to zero.

### Durable cleanup error

A durable cleanup error is retry-supporting state persisted after cleanup fails. It may include a bounded classification, exception class name, errno, timestamp, and retry metadata. It must not persist `str(exc)` or any raw exception detail that may contain host paths, secrets, Docker internals, SSH material, or environment data.

## Requirements

### CRH-001 — Reject boolean Docker volume sizes

Docker volume inventory must treat boolean `UsageData.Size` values as malformed, not as byte counts. This requirement covers both `True` and `False` because Python considers both instances of `int`.

The implementation shall:

- accept only non-negative integer byte sizes that are not booleans;
- leave boolean, negative, missing, non-integer, and otherwise malformed values as unavailable;
- preserve existing `truncated` and `incomplete` behavior for oversized volume discovery;
- preserve the one ownership-filtered Docker volume-list call and local post-list bounding behavior established by R7;
- avoid public schema and contract-version changes unless the implementation discovers an unavoidable response-shape change.

**Validation:** `CRHV-001` shall exercise fake Docker volumes with boolean, negative, missing, and valid integer size metadata. Boolean sizes must produce unavailable byte observations, while valid non-negative integers must remain visible.

### CRH-002 — Sanitize top-level audit `workspace_id` and `repository_id`

Audit writes must sanitize top-level `workspace_id` and `repository_id` at the same boundary where request/session attribution and metadata are made safe. Current call sites are controlled, but future audit call sites should not be able to persist raw path-like or secret-like identifiers by accident.

The implementation shall:

- introduce or reuse a single safe-identifier sanitizer for top-level audit identifiers;
- apply it to `workspace_id` and `repository_id` in `write_audit_event()` before durable persistence;
- fail closed to `None` or another documented safe placeholder when an identifier is unsafe;
- preserve existing safe opaque workspace/repository IDs;
- preserve audit write failure behavior;
- avoid changing public MCP response schemas.

**Validation:** `CRHV-002` shall write audit events with safe IDs and hostile IDs. Hostile IDs containing host-path shape, authorization shape, token-like substrings, SSH material, raw environment key/value shape, or multiline values must not be present in persisted audit JSON. Safe IDs used by existing operations must remain unchanged.

### CRH-003 — Expand generic secret/token redaction

The repository's generic redaction utility should better match the threat model used by the remediation workstreams. R2's dangerous public prune-failure path is already protected by fail-closed classification, but generic redaction should still cover common credential forms that may appear in logs, audit metadata, or defensive diagnostics.

The implementation shall:

- add tests for representative OpenAI-style API key prefixes, GitHub token prefixes, AWS access key ID prefixes, bearer-token header values, common `api_key`/`access_token`/`secret`/`password` assignments, and high-entropy token-like strings;
- redact the sensitive value while preserving enough surrounding context for debugging where safe;
- avoid redacting ordinary bounded request IDs, safe resource IDs, short labels, commit SHAs, UUIDs, and documented public classifications;
- keep redaction deterministic and bounded;
- preserve the stronger allowlist/classification policy for public prune execution failures rather than replacing it with regex-only redaction.

**Validation:** `CRHV-003` shall cover positive redaction cases and false-positive guard cases. The test must prove known safe IDs and commit SHAs are not unnecessarily destroyed while representative credential-shaped values are removed.

### CRH-004 — Normalize SSH-agent cleanup legacy errors at read/model boundary

SSH-agent cleanup new writes already use safe reason/class/errno style values and current public surfaces do not republish raw legacy `last_error` values. This requirement closes the remaining future-footgun by normalizing legacy `last_error` at the durable row-to-model boundary, matching the safer job-log cleanup pattern.

The implementation shall:

- sanitize or classify SSH-agent cleanup `last_error` values when constructing internal debt records from durable rows;
- preserve retry behavior, timestamps, socket identity fields, ownership metadata, and operator-useful safe failure distinctions;
- avoid changing the SQLite schema unless implementation proves a migration is necessary;
- avoid exposing raw legacy exception text through health, inventory, prune planning, prune execution, audit, or future internal consumers that read the debt model;
- preserve existing tests for active SSH cleanup APIs.

**Validation:** `CRHV-004` shall seed legacy SSH-agent cleanup debt rows containing host-path, Docker, SSH, environment, and token-shaped raw errors. The resulting model and all public/audit-visible paths must expose only safe classifications or bounded safe details.

### CRH-005 — Restore default-branch write protection after R8 direct publication

Direct default-branch writes were enabled as an exceptional operational override to finish R8 after PR #48 was closed by explicit user direction. The safer normal posture is to keep default-branch writes disabled and use source branches plus exact-head guarded merges.

The implementation or operator procedure shall:

- restore Ralph Bridge default-branch write protection for `ekkus93/mcp-chatgpt-sandbox` unless a new explicit exception is active;
- document the setting and expected value in the appropriate operator/setup documentation if that documentation belongs in this repository;
- verify that ordinary source-branch writes and guarded PR merges remain available under the restored policy;
- verify that direct writes to `master` are rejected by policy after the setting is restored;
- record evidence without committing secrets or private operator configuration.

**Validation:** `CRHV-005` shall be satisfied by documented operator evidence or an automated policy check, depending on what Ralph Bridge exposes. At minimum, the TODO must record the exact verification method, timestamp, and result.

## Suggested implementation order

1. `CRH-001` first because it is a small isolated runtime/test fix.
2. `CRH-002` next because it centralizes audit identifier safety and may be reused by later tests.
3. `CRH-003` after the audit sanitizer work so redaction false-positive guards can include known safe identifiers.
4. `CRH-004` after redaction/classification helpers are stable.
5. `CRH-005` last because it may require an external Ralph Bridge operator setting change rather than a repository source patch.

## Completion criteria

The workstream is complete only when:

- `CRH-001`..`CRH-005` are implemented or explicitly dispositioned;
- `CRHV-001`..`CRHV-005` evidence is recorded;
- focused tests pass for every code change;
- full Docker-backed CI passes on the exact final head;
- public schemas and `contract_version` are unchanged unless a documented contract change required a bump;
- `docs/TOOLS.md` and exported schemas are drift-free;
- no broad-prune or unsafe fallback behavior is introduced;
- default-branch direct-write policy is restored or a documented user-approved exception remains active.
