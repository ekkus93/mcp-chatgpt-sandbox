# Setup Wizard Code-Review Remediation Evidence

**Status:** Complete  
**Original wizard baseline:** `49d48611e392e11bceb7dd4ea81967733e86eb78`  
**Remediation implementation candidate:** `37436f7ee009939729543ebd9e3123c612943cf7`  
**Evidence/merge head:** `f957803d4c3ceb3722e93e9eb8c9ffd150553e90`  
**Pull request:** #59  
**Implementation qualification run:** `34776868015`  
**Merge-head qualification run:** `34778439164`  
**Merged master:** `3dcb00e8696a6dcd41954fc8f4e9069a230e65d4`  
**Post-merge qualification run:** `34779008735`

## Scope

This evidence closes the code-review remediation layer defined by:

- `docs/SETUP_WIZARD_CODE_REVIEW_REMEDIATION_SPEC.md`
- `docs/SETUP_WIZARD_CODE_REVIEW_REMEDIATION_TODO.md`

The original WIZ-001 through WIZ-020 implementation was already merged and post-merge qualified before this remediation. Current `master` records the original wizard's guarded merge and green post-merge CI in `docs/SETUP_WIZARD_TODO.md`.

## Architecture findings and remediation

The main review finding was coordinator growth in the Textual application. The wizard had correct domain modules, but `setup/app.py` still accumulated step-specific actions. The remediation splits step-specific event/action orchestration into `setup/step_actions.py`, leaving `SetupWizardApp` responsible for shell/lifecycle/navigation/rendering concerns.

Permanent architecture contracts now enforce:

- presentation modules do not own YAML parsing/writing;
- presentation modules do not perform atomic config writes directly;
- reviewed config writes continue through the production setup transaction path;
- coordinator modules have explicit growth tripwires;
- production configuration models remain authoritative.

## Configuration ownership

The remediation expands ownership accounting from top-level `ServerConfig` fields to safety-sensitive nested production models. Every current `GitPolicyConfig` field has an explicit disposition, and exhaustiveness tests fail when future model fields appear without an explicit setup decision.

The audit also corrected ownership descriptions so preserved production fields are not mislabeled as wizard-managed. In particular, nested fields such as `container.sudo`, `github.repositories`, and `audit.enabled` are treated according to actual wizard behavior rather than inferred ownership.

## Dependency invalidation

Wizard invalidation is represented as explicit dependencies rather than a purely positional "everything later" rule. Permanent tests cover each dependency edge and verify that unrelated completed steps remain completed.

Repository changes invalidate credential/review/final evidence; ChatGPT-only changes do not unnecessarily invalidate independent setup steps; missing process-local material rewinds dependent completion truthfully.

## Secret lifecycle and rendering

No raw secret is stored in resumable setup state. The Secure MCP Tunnel runtime credential remains process-local/external.

The current TUI intentionally defines no raw-secret input control. This is stronger than relying on visual password masking: a permanent AST contract scans Textual `Input` definitions for secret-bearing field names, and another contract scans resumable setup choices for secret-bearing keys. User-attested ChatGPT UI completion remains separate from machine diagnostic PASS.

## Reviewed configuration transaction

The remediation retained and re-audited the existing transaction guarantees:

1. bounded/safe source inspection;
2. duplicate-key-safe YAML parsing;
3. preservation of valid non-wizard-owned fields;
4. production candidate validation and serialization round trip;
5. explicit structured review;
6. source digest/CAS binding;
7. timestamped backup;
8. atomic write;
9. exact persisted-byte verification;
10. production reload before success.

A permanent source contract verifies that the Textual write action cannot silently call `prepare_config_update()` when no reviewed plan exists. Stale review must return to review rather than regenerate-and-write in one action.

## Repository verification isolation

Repository verification is read-only. A permanent behavioral test snapshots local Git configuration, remotes, symbolic HEAD, commit HEAD, and worktree status before verification and proves all remain unchanged afterward.

## Unsafe-fallback and silent-failure audit

The setup package is guarded by executable audit contracts covering the review concerns that motivated this work.

The contracts verify:

- no bare `except`;
- every broad `except Exception` is an explicit WCR-013 fail-closed boundary with nearby rationale;
- no `pass` statement silently swallows setup failures;
- every setup `subprocess.run` call has a timeout;
- no retry/sleep framework exists around setup mutations;
- presentation modules do not import YAML or own file-write primitives;
- machine diagnostics do not consume user-attestation state;
- setup-state errors do not embed serialized raw payloads;
- reviewed-plan invalidation cannot be bypassed by the write action;
- repository verification remains side-effect-free.

Two broad exception boundaries intentionally survive in diagnostics: MCP server construction and the outer diagnostic matrix. Both convert unexpected library/runtime failures into secret-safe FAIL results; neither returns success or exposes arbitrary exception text.

No automatic mutation retry was introduced. No malformed configuration silently becomes an empty/default configuration. No failed optional external verification is promoted to machine PASS.

## Original WIZ reconciliation

The original `docs/SETUP_WIZARD_TODO.md` on current `master` records WIZ-001 through WIZ-020 complete, including:

- fresh-install acceptance;
- existing-config preservation;
- repair/failure-path acceptance;
- wizard-first documentation;
- full final code-review audit;
- guarded merge of PR #56;
- green post-merge `master` CI.

The remediation changes architecture/safety contracts but does not change the documented setup command, config format, exit codes, or first-run workflow. README, quickstart, configuration, development, repository-connection, and ChatGPT-connection documentation therefore remain current.

## Exact implementation qualification

Implementation candidate:

`37436f7ee009939729543ebd9e3123c612943cf7`

GitHub Actions CI:

`34776868015` — **PASS**

Qualified gates:

- Ruff format — PASS
- Ruff lint — PASS
- strict mypy — PASS
- MCP contract check — PASS
- `contract_version` history gate — PASS
- `docs/TOOLS.md` drift gate — PASS
- full Docker-backed pytest + coverage — PASS

The immediately preceding source generation `088479a40879e8df9aea791bd9c6ac0d5d47040f` completed with 1344 passed and one failing remediation-audit test. The sole failure was an off-by-window issue in the audit helper that searched for the documented fail-closed rationale. Candidate `37436f7e...` changes only that helper and is the authoritative green implementation generation.

## Merge and post-merge evidence

Evidence/merge head:

`f957803d4c3ceb3722e93e9eb8c9ffd150553e90`

Pre-merge GitHub Actions CI:

`34778439164` — **PASS**

PR #59 was squash-merged through the policy-approved guarded Phase 4 merge path from exact source head `f957803d4c3ceb3722e93e9eb8c9ffd150553e90`.

Resulting `master` SHA:

`3dcb00e8696a6dcd41954fc8f4e9069a230e65d4`

Fresh post-merge push CI:

`34779008735` — **PASS**

The post-merge run passed the same permanent workflow on the exact merged generation, including Ruff format/lint, strict mypy, MCP contract checks, contract-version history gate, docs drift gate, and the full Docker-backed pytest/coverage phase.

## Final conclusion

WCR-001 through WCR-014 are evidence-complete. The reviewed wizard architecture, transaction semantics, secret lifecycle, repository isolation, shared diagnostics, and unsafe-fallback conclusions are all protected by permanent tests/contracts. The exact implementation candidate, the exact merge head, and the exact merged `master` generation each passed the repository's authoritative CI path.
