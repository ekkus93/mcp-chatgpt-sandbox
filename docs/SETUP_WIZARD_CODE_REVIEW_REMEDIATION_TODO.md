# Setup Wizard Code-Review Remediation TODO

**Status:** Active — implementation complete; merge/post-merge closeout pending  
**Baseline:** `master` at `49d48611e392e11bceb7dd4ea81967733e86eb78`  
**Spec:** `docs/SETUP_WIZARD_CODE_REVIEW_REMEDIATION_SPEC.md`  
**Evidence:** `docs/SETUP_WIZARD_CODE_REVIEW_REMEDIATION_EVIDENCE.md`  
**Task series:** `WCR-001` through `WCR-014`

A checkbox closes only from source/tests/docs/workflow evidence. Do not weaken production validation, destructive-safety rules, repository isolation, security gates, or CI to obtain green.

## WCR-001 — Freeze package boundaries and configuration ownership

- [x] Keep production config models authoritative.
- [x] Maintain explicit top-level `ServerConfig` ownership coverage.
- [x] Add explicit dispositions for safety-sensitive nested production fields.
- [x] Add exhaustiveness tests for nested models, including all `GitPolicyConfig` fields.
- [x] Keep setup package import free of network/process/UI startup side effects.
- [x] Record preserved versus managed nested fields truthfully.

## WCR-002 — Keep the Textual application shell thin

- [x] Domain validation exists in reusable setup modules outside Textual.
- [x] Split the oversized Textual coordinator into shell/lifecycle and step-action modules.
- [x] Keep YAML parsing/writing out of presentation modules.
- [x] Keep production config writes behind the reviewed writer only.
- [x] Add static architecture contracts preventing writer/YAML bypass from presentation code.
- [x] Add coordinator-growth regression guards.

## WCR-003 — Explicit dependency invalidation

- [x] Upstream edits invalidate config review/final validation.
- [x] Repository edits invalidate downstream credential/review state.
- [x] Add parameterized tests for every dependency edge and unrelated-step preservation.
- [x] Resume rewinds for missing process-local material.

## WCR-004 — Non-secret state and secret lifecycle

- [x] Strict versioned state.
- [x] Unknown/future keys and versions fail closed.
- [x] Secret-bearing state names/content are rejected by contract.
- [x] Owner-only state persistence and safe file handling.
- [x] Process-local tunnel material is not persisted.
- [x] Resume reacquires missing process-local material.
- [x] Add a static/behavioral regression test scanning serialized state and TUI input definitions for secret leakage. The current wizard intentionally has no raw-secret TUI field, which is stronger than masking a persisted/rendered secret value.

## WCR-005 — Reviewed configuration transaction

- [x] Duplicate-key-safe parsing.
- [x] Existing valid config preservation.
- [x] Explicit repair requirement for invalid config.
- [x] Candidate production validation and serialization round-trip.
- [x] Source identity/digest binding.
- [x] Structured review summary.
- [x] Atomic write and timestamped backup.
- [x] Exact persisted-byte verification.
- [x] Production reload before success.
- [x] Add/retain regression coverage proving stale review cannot be silently regenerated-and-written in one action.

## WCR-006 — Existing-installation semantics

- [x] Resume is distinct.
- [x] Repair is distinct.
- [x] Edit configuration seeds from production-valid config.
- [x] Start fresh resets wizard progress only.
- [x] Cancel preserves operational configuration/resources.
- [x] Corrupt state does not silently overwrite production config.

## WCR-007 — Repository CRUD and verification isolation

- [x] Guided list/add/edit/remove/verify flow.
- [x] Stable ID, absolute path, canonical GitHub identity validation.
- [x] Duplicate ID/path behavior defined.
- [x] Optional repository SSH key support.
- [x] `repository_roots` support.
- [x] Verification is read-only.
- [x] Add a permanent side-effect test proving verification does not change Git config/remotes/HEAD/worktree.

## WCR-008 — Git policy presets remain production projections

- [x] Recommended preset materializes real `GitPolicyConfig`.
- [x] Conservative preset is no less restrictive on mutations.
- [x] Custom mode covers current production fields.
- [x] Add nested ownership/exhaustiveness contract that forces a decision for every future Git policy field.
- [x] Merely viewing controls does not commit policy changes.

## WCR-009 — Prevent advanced-default drift

- [x] Advanced controls are progressive disclosure.
- [x] Normal flow preserves production defaults.
- [x] Existing custom advanced values survive unrelated edits.
- [x] Production Pydantic models validate advanced values.
- [x] Unbounded resource semantics are preserved.

## WCR-010 — Optional ChatGPT/tunnel hardening

- [x] Optional/skippable flow.
- [x] `--no-browser` support.
- [x] Runtime credential is process-local.
- [x] Tunnel profile mutation is explicitly reviewed.
- [x] Bounded doctor/profile checks.
- [x] User attestation is distinct from machine PASS.
- [x] No durable subscription-plan claim.

## WCR-011 — Shared diagnostics

- [x] One result/status model.
- [x] TUI and headless checks share the diagnostics engine.
- [x] Production config, filesystem, Docker, repositories, SSH, GitHub, server construction and optional tunnel checks are covered.
- [x] Unexpected failures fail closed.
- [x] Mandatory unresolved checks block Finish.

## WCR-012 — Keyboard/narrow-terminal/snapshot safety

- [x] Keyboard-only navigation tests.
- [x] Invalid-step and Finish gating tests.
- [x] Cancel confirmation tests.
- [x] Narrow-terminal/scroll support.
- [x] Secret rendering is covered by a stronger contract: the current TUI defines no raw-secret input field, and resumable choices expose no secret-bearing field name.

## WCR-013 — Unsafe-fallback and silent-failure audit

- [x] Audit setup package for broad catches/bare catches/`pass` and ignored consequential results.
- [x] Audit malformed-config/default fallbacks.
- [x] Audit bounded subprocess/network operations.
- [x] Audit automatic retry around writes.
- [x] Audit secret-bearing logs/errors/snapshots.
- [x] Audit stale reviewed-plan/invalidation handling.
- [x] Audit user attestation versus machine PASS.
- [x] Add permanent contracts for material conclusions.
- [x] Record findings in durable evidence.

## WCR-014 — Exact candidate, merge, and final evidence

- [x] Reconcile WIZ-001 through WIZ-020 against merged source/tests/docs. The original WIZ track is fully evidence-complete on current `master`.
- [x] Reconcile WCR-001 through WCR-013 against evidence.
- [x] Update README/development/configuration docs only where remediation changes behavior/architecture. No user-facing command/config rewrite was required; current wizard-first documentation remains accurate.
- [x] Record exact implementation candidate SHA: `37436f7ee009939729543ebd9e3123c612943cf7`.
- [x] Ruff format/check green on implementation candidate.
- [x] strict mypy green on implementation candidate.
- [x] setup unit/Textual/integration tests green on implementation candidate.
- [x] full Docker-backed pytest green on implementation candidate.
- [x] MCP/schema/contract/docs drift gates green on implementation candidate.
- [ ] guarded merge through policy-approved path.
- [ ] fresh exact merged-`master` CI green.
- [ ] durable final remediation evidence updated with merge/post-merge SHA and CI.

### Implementation qualification evidence

Implementation candidate `37436f7ee009939729543ebd9e3123c612943cf7` passed GitHub Actions CI run `34776868015`:

- Ruff format: PASS.
- Ruff lint: PASS.
- strict mypy: PASS.
- MCP contract check: PASS.
- `contract_version` history gate: PASS.
- `docs/TOOLS.md` drift gate: PASS.
- Docker-backed pytest + coverage: PASS.

The immediately preceding source generation `088479a40879e8df9aea791bd9c6ac0d5d47040f` reached **1344 passed, 1 failed**; the sole failure was the remediation audit helper's source-window calculation. `37436f7e...` changes only that audit helper and is the authoritative green implementation candidate.

## Definition of done

- [ ] WCR-001 through WCR-014 are evidence-complete.
- [x] Textual remains presentation/orchestration rather than a second domain/config layer.
- [x] Production config models remain authoritative.
- [x] No raw secrets persist or render.
- [x] Config writes remain reviewed, source-bound, backed up, atomic, verified and production-reloaded.
- [x] Repository verification remains read-only.
- [x] No unsafe fallback or silent-success path remains unreviewed.
- [ ] Exact candidate and exact merged-master validation are green.
