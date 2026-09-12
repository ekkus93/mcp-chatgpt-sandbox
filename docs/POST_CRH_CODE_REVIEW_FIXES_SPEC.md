# Post-CRH Code Review Fixes Spec

**Status:** implementation workstream  
**Date:** 2026-09-12  
**Base:** `86440755c84a817a534de60293cacb5c35795a65` (`docs: close CRH-005 and CRH-006 hardening workstream`)  
**Requirement series:** `PCRF-001`..`PCRF-004`  
**Validation series:** `PCRFV-001`..`PCRFV-004`

This specification defines the follow-up workstream for issues found in the review of the completed Post-R8 Code Review Hardening work. It does not reopen CRH-001 through CRH-006. It fixes the concrete audit-identifier sanitizer gap, corrects stale final closure evidence, restores high-value implementation rationale that was lost during redaction refactoring, and revalidates that the hardening invariants still hold.

## Background

The CRH workstream closed with `master` CI green and the Ralph Bridge Phase 4 default-branch write policy restored. A subsequent code review found that the runtime changes were generally narrow and correct, but identified three follow-up issues:

1. The audit identifier sanitizer in `audit.py` is syntax-only. It rejects path, shell, authorization-header, and environment-assignment shapes, but can still accept a pure credential-shaped string when that string uses only the allowed audit-ID alphabet.
2. `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md` records the final CRH-006 evidence using the pre-bookkeeping merge head rather than the actual final post-merge `master` head and CI run.
3. The final `errors.py` redaction refactor removed important explanatory comments around unexpected exception behavior, fail-closed redaction, JSON-safe fallback handling, and false-positive guards.

## Goals

1. Ensure top-level audit identifiers reject values that look like credentials or high-entropy tokens even when they are syntactically valid opaque IDs.
2. Share the generic secret-shape predicate with the audit boundary without weakening generic redaction behavior.
3. Preserve valid workspace, repository, request, session, prune-plan, and prune-candidate identifiers.
4. Correct the stale CRH-006 final evidence so the TODO records the actual final `master` head and CI run.
5. Restore concise, high-value implementation rationale in `errors.py` without reintroducing stale or noisy commentary.
6. Verify the full Docker-backed CI gate on the exact final head.

## Non-goals

- Do not redesign the audit schema or add queryable audit storage.
- Do not change public MCP response schemas, registered tool descriptions, or `contract_version` unless implementation proves a public contract change is unavoidable.
- Do not introduce broad prune, label-wide cleanup, prefix-wide deletion, path-wide deletion, system-wide deletion, or `docker system prune`.
- Do not expose raw private operator configuration, tokens, host paths, SSH material, Docker credentials, raw environment values, prompt text, or arbitrary client metadata.
- Do not make direct writes to `master`; use the normal Ralph Bridge source-branch, CI, PR, and exact-head guarded merge flow.
- Do not add a dependency on literal real-looking secrets in tests or docs.

## Global implementation rules

- Work from an exact inspected `master` SHA.
- Use a `ralph/...` source branch and compare-and-swap writes.
- Add failing behavioral tests before fixing `PCRF-001` when feasible.
- Assemble synthetic credential-shaped test values at runtime so repository scanning does not see literal real-looking secrets.
- Keep public schema and `contract_version` unchanged unless an intentional public contract change is made and documented.
- Run focused audit/redaction tests before relying on full CI.
- Run complete Docker-backed CI on the final branch head before merge.
- Merge only through Ralph Bridge's exact-head guarded PR merge path.
- Verify post-merge `master` CI before marking this workstream closed.

## Requirements

### PCRF-001 — Reject token-shaped top-level audit identifiers

Top-level audit `request_id`, `session_id`, `workspace_id`, and `repository_id` values must pass both a conservative syntax check and a credential-shape check before persistence. A string that looks like a credential or high-entropy token must fail closed to `None` even when every character is otherwise allowed by the audit identifier regex.

The implementation shall:

- add a reusable internal predicate for generic secret/token-shaped strings or otherwise safely share the existing redaction predicate;
- keep `redact_secrets()` behavior unchanged for existing callers;
- apply the secret/token-shape check inside the audit identifier boundary;
- reject synthetic GitHub-token-shaped, OpenAI-key-shaped, AWS-access-key-ID-shaped, bearer-token-shaped, assignment-shaped, and high-entropy-token-shaped identifiers;
- cover both `workspace_id` and `repository_id`, and avoid regressing `request_id` / `session_id` handling;
- preserve legitimate bounded opaque IDs such as safe workspace IDs, repository IDs, request IDs, prune-plan IDs, and prune-candidate IDs;
- avoid storing the rejected unsafe values anywhere in the persisted JSONL audit line.

**Validation:** `PCRFV-001` shall add focused tests that set top-level audit IDs directly to synthetic credential-shaped values assembled at runtime. The test must prove those values fail closed to `None` and do not appear in the audit file content. It must also prove valid safe IDs are preserved.

### PCRF-002 — Correct final CRH-006 evidence

The completed CRH TODO must record the actual final post-merge `master` head and CI run that followed the bookkeeping merge.

The implementation shall update `docs/POST_R8_CODE_REVIEW_HARDENING_TODO.md` so CRH-006 evidence includes:

- final `master` head `86440755c84a817a534de60293cacb5c35795a65`;
- final CI run `34691630548`;
- final job `103547787452`;
- final pytest result `1226 passed`, `4 warnings`;
- coverage `92%` with TOTAL `6536` statements / `518` misses;
- test runtime `613.65s`.

The update shall preserve the existing CRH-001 through CRH-005 evidence and shall not overstate private Ralph Bridge operator configuration details.

**Validation:** `PCRFV-002` is satisfied by diff inspection and final CI confirming docs checks still pass.

### PCRF-003 — Restore high-value `errors.py` rationale

`errors.py` should retain the explanatory rationale needed to preserve future security-boundary decisions. The CRH redaction refactor should not leave future maintainers without context for why unexpected exceptions propagate, why redaction is fail-closed, and why selected public identifiers are false-positive guards.

The implementation shall:

- restore concise rationale for allowing non-`WorkspaceError` exceptions to propagate as unexpected crashes rather than being converted into generic tool errors;
- document that `redact_secrets()` is defense-in-depth for tool errors and audit metadata;
- document why nested secret-key hints propagate through dictionaries, lists, tuples, and sets;
- document why non-JSON-native or unknown values fail closed to an unrepresentable placeholder;
- document why commit SHAs, UUIDs, safe opaque internal IDs, and public SSH fingerprints are not treated as high-entropy secrets;
- avoid resurrecting stale comments that refer to removed code shapes or obsolete task decisions.

**Validation:** `PCRFV-003` is satisfied by code review plus full CI. No behavioral change is required beyond the shared predicate from PCRF-001.

### PCRF-004 — Final verification and closure

The workstream must close only after implementation, documentation updates, and final CI evidence are complete.

The implementation shall:

- update `docs/POST_CRH_CODE_REVIEW_FIXES_TODO.md` with exact implementation evidence;
- confirm no public schema, registered description, or `contract_version` drift remains unresolved;
- confirm no prune/destructive-safety runtime file was changed except for inspection-only review where applicable;
- run full Docker-backed CI on the final exact branch head;
- merge through exact-head guarded PR merge;
- verify post-merge `master` state and CI.

**Validation:** `PCRFV-004` is the final exact-head branch CI plus post-merge `master` CI.

## Completion criteria

The workstream is complete only when:

- `PCRF-001` through `PCRF-004` are implemented or explicitly dispositioned;
- `PCRFV-001` through `PCRFV-004` evidence is recorded;
- focused audit/redaction tests pass;
- complete Docker-backed CI passes on the exact final branch head;
- the PR is merged through Ralph Bridge exact-head guarded merge;
- post-merge `master` CI passes;
- public schema and `contract_version` remain correct;
- no new broad cleanup or unsafe fallback behavior is introduced.
