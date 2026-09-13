# Setup Wizard Code-Review Remediation TODO

**Status:** Active  
**Baseline:** `master` at `49d48611e392e11bceb7dd4ea81967733e86eb78`  
**Spec:** `docs/SETUP_WIZARD_CODE_REVIEW_REMEDIATION_SPEC.md`  
**Task series:** `WCR-001` through `WCR-014`

A checkbox closes only from source/tests/docs/workflow evidence. Do not weaken production validation, destructive-safety rules, repository isolation, security gates, or CI to obtain green.

## WCR-001 — Freeze package boundaries and configuration ownership

- [x] Keep production config models authoritative.
- [x] Maintain explicit top-level `ServerConfig` ownership coverage.
- [ ] Add explicit dispositions for safety-sensitive nested production fields.
- [ ] Add exhaustiveness tests for nested models, including all `GitPolicyConfig` fields.
- [x] Keep setup package import free of network/process/UI startup side effects.
- [ ] Record preserved versus managed nested fields truthfully.

## WCR-002 — Keep the Textual application shell thin

- [x] Domain validation exists in reusable setup modules outside Textual.
- [ ] Split the oversized Textual coordinator into shell/lifecycle and step-action modules.
- [ ] Keep YAML parsing/writing out of presentation modules.
- [ ] Keep production config writes behind the reviewed writer only.
- [ ] Add static architecture contracts preventing writer/YAML bypass from presentation code.
- [ ] Add coordinator-growth regression guards.

## WCR-003 — Explicit dependency invalidation

- [x] Upstream edits invalidate config review/final validation.
- [x] Repository edits invalidate downstream credential/review state.
- [ ] Add parameterized tests for every dependency edge and unrelated-step preservation.
- [x] Resume rewinds for missing process-local material.

## WCR-004 — Non-secret state and secret lifecycle

- [x] Strict versioned state.
- [x] Unknown/future keys and versions fail closed.
- [x] Secret-bearing state names/content are rejected by contract.
- [x] Owner-only state persistence and safe file handling.
- [x] Process-local tunnel material is not persisted.
- [x] Resume reacquires missing process-local material.
- [ ] Add a static/behavioral regression test scanning serialized state and TUI snapshots for secret leakage.

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
- [ ] Add/retain regression coverage proving stale review cannot be silently regenerated-and-written in one action.

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
- [ ] Add a permanent side-effect test proving verification does not change Git config/remotes/HEAD/worktree.

## WCR-008 — Git policy presets remain production projections

- [x] Recommended preset materializes real `GitPolicyConfig`.
- [x] Conservative preset is no less restrictive on mutations.
- [x] Custom mode covers current production fields.
- [ ] Add nested ownership/exhaustiveness contract that forces a decision for every future Git policy field.
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
- [ ] Add explicit masked-secret snapshot/value regression coverage if not already present.

## WCR-013 — Unsafe-fallback and silent-failure audit

- [ ] Audit setup package for broad catches/bare catches/`pass` and ignored consequential results.
- [ ] Audit malformed-config/default fallbacks.
- [ ] Audit bounded subprocess/network operations.
- [ ] Audit automatic retry around writes.
- [ ] Audit secret-bearing logs/errors/snapshots.
- [ ] Audit stale reviewed-plan/invalidation handling.
- [ ] Audit user attestation versus machine PASS.
- [ ] Add permanent contracts for material conclusions.
- [ ] Record findings in durable evidence.

## WCR-014 — Exact candidate, merge, and final evidence

- [ ] Reconcile WIZ-001 through WIZ-020 against merged source/tests/docs.
- [ ] Reconcile WCR-001 through WCR-013 against evidence.
- [ ] Update README/development/configuration docs only where remediation changes behavior/architecture.
- [ ] Record exact final candidate SHA.
- [ ] Ruff format/check green.
- [ ] strict mypy green.
- [ ] setup unit/Textual/integration tests green.
- [ ] full Docker-backed pytest green.
- [ ] MCP/schema/contract/docs drift gates green.
- [ ] guarded merge through policy-approved path.
- [ ] fresh exact merged-`master` CI green.
- [ ] durable final remediation evidence committed.

## Definition of done

- [ ] WCR-001 through WCR-014 are evidence-complete.
- [ ] Textual remains presentation/orchestration rather than a second domain/config layer.
- [ ] Production config models remain authoritative.
- [ ] No raw secrets persist or render.
- [ ] Config writes remain reviewed, source-bound, backed up, atomic, verified and production-reloaded.
- [ ] Repository verification remains read-only.
- [ ] No unsafe fallback or silent-success path remains unreviewed.
- [ ] Exact candidate and exact merged-master validation are green.
