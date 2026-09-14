# Setup Wizard Post-Hardening Review TODO

**Status:** In progress — SWPH-001 complete  
**Date:** 2026-09-14  
**Spec:** `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_SPEC_2026-09-14.md`  
**Reviewed baseline:** `dc47ba58cd8a4e9eb7f32c5b5938a172bcb53d68`  
**Task series:** `SWPH-001` through `SWPH-010`  
**Validation series:** `SWPHV-*`

This TODO implements the post-hardening remediation defined by `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_SPEC_2026-09-14.md`. It addresses second-order defects found after `docs/SETUP_WIZARD_HARDENING_TODO.md` had been closed on green CI.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Use `ralph/...` branches for source work.
- [ ] Use exact-head compare-and-swap guards for writes and merges.
- [ ] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [ ] Do not introduce a weaker setup-only production schema.
- [ ] Preserve manual YAML configuration and existing CLI workflows.
- [ ] Preserve repository isolation and destructive-safety semantics.
- [ ] Preserve reviewed config-write semantics and production reload.
- [ ] Never persist raw secrets in setup state.
- [ ] Never print raw secrets in UI, diagnostics, logs, exceptions, docs, or tests.
- [ ] Add a focused regression test for every correctness defect.
- [ ] Do not weaken existing tests or architecture guards to make CI green.
- [ ] Do not bump `contract_version` unless the public MCP contract actually changes.
- [ ] Record exact SHA/PR/run/job evidence as each phase closes.

---

## Phase 1 — Invocation and Docker integration correctness

### SWPH-001 — Make tunnel server invocation independent of caller cwd

**Goal:** persistent Secure MCP Tunnel profiles must launch the intended server regardless of the directory from which setup was invoked.

- [x] Audit every call to `build_tunnel_profile_plan()` and every source of its project/server invocation.
- [x] Remove `Path.cwd()` as the persistent tunnel project's identity.
- [x] Define one deterministic installed/source-checkout server invocation strategy.
- [x] Source checkout mode does not persist a project root: the tunnel command reuses the exact active Python environment and invokes `python -m dev_workspace_mcp`.
- [x] Ensure installed-distribution invocation works without a repository checkout where supported.
- [x] Keep command construction argv-first.
- [x] Render persistent shell command text using `shlex.join()` or equivalent.
- [x] Preserve config-path quoting for spaces and shell-sensitive characters.
- [x] Preserve `--no-browser` behavior.
- [x] Ensure unresolved required project/server location is ACTION REQUIRED rather than a persisted broken profile.
- [x] Ensure tunnel plan/profile content contains no secrets.
- [x] Update `docs/CONNECTING_CHATGPT.md` to match the final invocation model.

**Validation — SWPHV-001**

- [x] Unit test: normal server invocation has the intended argv.
- [x] Unit test: spaces/shell-sensitive paths round-trip through shell parsing.
- [x] TUI/action test: wizard invoked from an unrelated cwd still generates the correct server command.
- [x] Test: unresolved required runtime produces an actionable setup error and does not persist a broken profile; no source-project identity is persisted by the final strategy.
- [x] Test: `--no-browser` still suppresses browser launch.
- [x] Test: profile plan remains secret-free.

**Closure evidence (2026-09-14)**

- Inspected baseline: `c1c1c8f1c0074499bf06eebd6ab51a9e776037fa` (`master`).
- Implementation head: `f14184f2febc90346e1a3802acaa7cd179d17adf`.
- Pull request: #68, `fix: make tunnel profiles independent of caller cwd`.
- Exact-head PR CI: run `34822382893`, job `103906671761` — Ruff format/lint PASS; strict mypy PASS; MCP contract PASS; contract-version gate PASS; docs/TOOLS drift PASS; `1383 passed, 4 warnings`; coverage `88%` (`9129` statements / `1105` misses).
- Focused SWPHV-001 regression file `tests/setup/test_swph_tunnel_invocation.py`: 4/4 PASS, covering unrelated cwd, module invocation from unrelated cwd, unavailable interpreter fail-closed behavior, shell-sensitive paths, and secret-free plans. Existing no-browser regression remained green in `tests/setup/test_tunnel_and_diagnostics.py`.
- Guarded merge: PR #68 merged at exact head `f14184f2febc90346e1a3802acaa7cd179d17adf` to merge commit `80aa7d8375f870f0710b70b6c9e858a642e7038e`.
- Public MCP tool/schema contract unchanged; no `contract_version` bump required.
- Post-merge `master` CI: run `34849793193`, job `103994533298` on merge SHA `80aa7d8375f870f0710b70b6c9e858a642e7038e` — all gates PASS; `1383 passed, 4 warnings` in `661.07s`; coverage `88%` (`9129` statements / `1105` misses).

### SWPH-002 — Contain Docker build-context failures inside Textual actions

**Goal:** expected missing-context conditions must never escape the setup wizard event boundary.

- [ ] Audit `validate_docker()` for operations that can raise `SetupDockerError` outside its handler boundary.
- [ ] Audit `build_docker_image()` for the same condition.
- [ ] Move build-context/preview resolution inside explicit error handling.
- [ ] Convert missing bundled context into a clear action-required validation message.
- [ ] Ensure no Docker client/build call occurs after context resolution fails.
- [ ] Preserve explicit operator approval before build execution.
- [ ] Preserve Docker-unavailable degraded behavior.
- [ ] Preserve mutation-free screen rendering.

**Validation — SWPHV-002**

- [ ] TUI/action test: missing bundled context during validation does not raise out of the handler.
- [ ] TUI/action test: missing bundled context during build does not raise out of the handler.
- [ ] Test: Docker build is not invoked when context resolution fails.
- [ ] Test: validation message is actionable and non-secret.
- [ ] Existing Docker-unavailable tests remain green.

### SWPH-003 — Make Docker build previews shell-safe and argv-faithful

**Goal:** the reviewed command shown to the operator must represent the exact approved build argv safely.

- [ ] Keep the build command as an argv sequence until presentation.
- [ ] Replace raw `" ".join(...)` preview rendering with `shlex.join()` or equivalent.
- [ ] Ensure preview and execution resolve the same context path.
- [ ] Ensure preview and execution use the same image/tag value.
- [ ] Keep preview free of secrets/environment credentials.

**Validation — SWPHV-003**

- [ ] Regression test: context path with spaces renders and round-trips correctly.
- [ ] Regression test: shell-sensitive context path renders and round-trips correctly.
- [ ] Regression test: preview argv equals executed approved argv.
- [ ] Regression test: normal-path preview remains equivalent to intended existing behavior.

---

## Phase 2 — Git-policy and persisted-state correctness

### SWPH-004 — Replace tautological Git-policy exhaustiveness testing

**Goal:** adding a production Git-policy field must require an explicit guided-editor support decision.

- [ ] Replace `GIT_POLICY_GUIDED_FIELDS = frozenset(GitPolicyConfig.model_fields)` or any equivalent self-derived manifest.
- [ ] Define an independent explicit support manifest or derive support from editor implementation fields.
- [ ] Ensure support manifest -> production model equality is checked in both directions.
- [ ] Ensure every supported field maps to a guided representation or an explicit documented disposition.
- [ ] Ensure editor extraction covers every supported field.
- [ ] Ensure widget population covers every supported field where the field is editable.
- [ ] Keep final materialization validation through `GitPolicyConfig`.
- [ ] Preserve Recommended preset semantics.
- [ ] Preserve Conservative preset semantics.

**Validation — SWPHV-004**

- [ ] Mutation-style test or equivalent proof: adding a fake/new production field without UI support fails the exhaustiveness contract.
- [ ] Test: stale supported field not present in production model fails.
- [ ] Existing boolean guided-control pilot remains green.
- [ ] Existing enum guided-control pilot remains green.
- [ ] Existing list/sequence guided-control pilot remains green.
- [ ] Existing round-trip test remains green.
- [ ] Recommended/Conservative preset tests remain green.

### SWPH-005 — Recover from incompatible persisted custom Git-policy state

**Goal:** stale or malformed resumable state must enter repair/action-required flow rather than crashing activation.

- [ ] Identify the expected exceptions raised by guided custom-policy population.
- [ ] Catch those exceptions at the presentation/state boundary, not with a broad silent swallow.
- [ ] Keep the wizard interactive after the incompatibility is detected.
- [ ] Mark the Git-policy step invalid/action-required.
- [ ] Display a clear non-secret remediation message.
- [ ] Do not silently replace incompatible custom values with Recommended defaults.
- [ ] Allow the operator to select a preset and recover.
- [ ] Allow the operator to re-enter Custom values and validate through `GitPolicyConfig`.
- [ ] Preserve valid current custom state behavior.
- [ ] Preserve fresh-state behavior.

**Validation — SWPHV-005**

- [ ] Test: malformed persisted enum value does not crash activation.
- [ ] Test: malformed persisted list/sequence value does not crash activation.
- [ ] Test: legacy missing/extra field shape is handled according to the documented migration policy.
- [ ] Test: remediation state remains non-secret.
- [ ] Test: operator can recover by applying a preset.
- [ ] Test: operator can recover by entering valid Custom values.
- [ ] Test: valid current custom state still populates all guided controls.

---

## Phase 3 — Config-write concurrency safety

### SWPH-006 — Make reviewed config replacement genuinely concurrency-safe

**Goal:** a competing post-review config edit must never be overwritten by the setup writer.

- [ ] Document the current verification/write race precisely.
- [ ] Choose and document one serialization mechanism.
- [ ] Define lock identity/location and ownership.
- [ ] Define bounded lock acquisition behavior.
- [ ] Define stale-lock behavior if the chosen mechanism requires it.
- [ ] Acquire serialization before final source verification.
- [ ] Revalidate source digest/identity while serialization is held.
- [ ] Reject any post-review source change.
- [ ] Keep backup creation inside the protected critical section where required.
- [ ] Preserve same-filesystem atomic replacement.
- [ ] Preserve byte-for-byte post-write verification.
- [ ] Preserve production `load_config()` reload after write.
- [ ] Preserve target symlink/non-regular refusal.
- [ ] Preserve valid advanced/unowned config fields.
- [ ] Ensure lock/temporary artifacts contain no secrets beyond what the protected config already contains.
- [ ] Ensure unexpected lock/write errors fail closed without leaving a partial production config.

**Validation — SWPHV-006**

- [ ] Concurrency regression: external modification after review but before commit is rejected.
- [ ] Test: bounded lock contention returns actionable failure.
- [ ] Test: unchanged reviewed source commits normally.
- [ ] Test: backup contains exact prior approved bytes.
- [ ] Test: failed production reload follows existing recovery semantics.
- [ ] Test: symlink target remains rejected under the locked path.
- [ ] Test: concurrent writers cannot both successfully commit against the same reviewed source.

---

## Phase 4 — Architecture guard and permission semantics

### SWPH-007 — Extend presentation architecture/static guards

**Goal:** static architecture tests must cover every module in the current Textual presentation layer.

- [ ] Enumerate the current presentation modules explicitly or define a documented discovery rule.
- [ ] Include `setup/app.py`.
- [ ] Include `setup/step_actions.py`.
- [ ] Include `setup/step_actions_hardened.py` while it exists.
- [ ] Include `setup/step_panels.py`.
- [ ] Include `setup/git_policy_panel.py`.
- [ ] Apply YAML/config-persistence ownership checks to every presentation module.
- [ ] Apply coordinator/module-growth checks to every relevant presentation module.
- [ ] Make introduction of a new presentation module require an explicit architecture disposition.
- [ ] Review whether `step_actions_hardened.py` should remain separate or be folded into the main action layer after hardening stabilizes.
- [ ] Review `app.py` and `step_actions.py` current sizes against explicit limits; refactor only if a real responsibility split is justified.

**Validation — SWPHV-007**

- [ ] Static audit fails when a guarded presentation module imports/uses config-write primitives directly.
- [ ] Static audit fails when an unapproved presentation module exceeds its architecture limit.
- [ ] Static audit or manifest test fails when a new presentation module lacks a support/ownership disposition.
- [ ] Existing setup static-audit suite remains green.

### SWPH-008 — Define and enforce recursive private-directory semantics

**Goal:** implementation, tests, and docs must make the permission guarantee unambiguous.

- [ ] Choose `leaf-private` or `created-suffix-private` policy.
- [ ] Record the choice in code comments/docs.
- [ ] Audit `ensure_private_directory()`.
- [ ] Audit setup-state parent creation.
- [ ] Audit storage-directory creation in `host.py`.
- [ ] Audit any other setup helper claiming to create a private directory.
- [ ] Never chmod pre-existing arbitrary ancestors.
- [ ] If choosing created-suffix-private, explicitly secure every newly created path component.
- [ ] If choosing leaf-private, state clearly that intermediate ancestors follow normal filesystem/umask semantics.
- [ ] Fail closed if a directory the policy requires to be private cannot be secured.

**Validation — SWPHV-008**

- [ ] Test under permissive umask `000`.
- [ ] Test pre-existing `0755` parent remains unchanged.
- [ ] Test selected private leaf receives required owner-only mode.
- [ ] If created-suffix-private: every newly created missing ancestor receives required mode.
- [ ] If leaf-private: test/document that intermediate created ancestors are outside the private guarantee.
- [ ] Setup-state and storage-directory helpers obey the same selected policy.

---

## Phase 5 — Documentation reconciliation

### SWPH-009 — Complete setup-wizard hardening documentation obligations

**Goal:** operator and developer documentation must describe actual behavior after the remediation.

- [ ] Update `docs/CONFIGURATION.md` with parent-directory non-mutation behavior.
- [ ] Update `docs/CONFIGURATION.md` with the selected recursive-private-directory policy.
- [ ] Update README/Quickstart with bundled Docker build-context availability/failure behavior.
- [ ] Update `docs/CONNECTING_CHATGPT.md` with the deterministic tunnel/server invocation model.
- [ ] Ensure shell-command examples containing paths use correct quoting.
- [ ] Update `docs/DEVELOPMENT.md` with any new config locking/serialization design.
- [ ] Update `docs/DEVELOPMENT.md` with the final presentation module layout.
- [ ] Reconcile older hardening docs so closure claims no longer overstate what earlier tests proved.
- [ ] Keep documentation examples secret-free.

**Validation — SWPHV-009**

- [ ] Manual docs audit confirms implementation and docs agree on tunnel invocation.
- [ ] Manual docs audit confirms implementation and docs agree on Docker context behavior.
- [ ] Manual docs audit confirms implementation and docs agree on parent-directory permissions.
- [ ] Manual docs audit confirms implementation and docs agree on config concurrency semantics.
- [ ] `docs/TOOLS.md` drift check remains green.

---

## Phase 6 — Final acceptance and closure

### SWPH-010 — Final regression, code-review, contract, and merge closure

**Goal:** close the follow-up only after every reviewed boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [ ] Tunnel profile/server invocation is independent of caller cwd.
- [ ] Tunnel command rendering is argv-faithful and shell-safe.
- [ ] Docker context-resolution errors remain inside TUI validation/action boundaries.
- [ ] Docker preview exactly represents execution argv.
- [ ] Git-policy exhaustiveness test is independent/non-tautological.
- [ ] Every current `GitPolicyConfig` field has a guided support disposition.
- [ ] Persisted incompatible custom policy enters recoverable action-required state.
- [ ] Config replacement is serialized across final verification and replacement.
- [ ] Competing post-review config edit cannot be overwritten.
- [ ] Presentation modules do not own config persistence.
- [ ] Architecture guards cover all current presentation modules.
- [ ] Recursive private-directory semantics are implemented consistently.
- [ ] Existing arbitrary parents are never chmodded as a side effect of setup writes.
- [ ] Setup state remains secret-free.
- [ ] Diagnostics remain fail-closed and non-secret.
- [ ] Config write remains reviewed, backed up, atomically replaced, byte-verified, and production-reloaded.
- [ ] Manual YAML and CLI workflows remain supported.
- [ ] Documentation matches implementation.

#### Contract/version decision

- [ ] Confirm public MCP tool/schema contract is unchanged.
- [ ] Record explicit decision: no `contract_version` bump required, if unchanged.
- [ ] If contract changed, stop and perform the normal explicit contract-version workflow.

#### Complete validation — SWPHV-010

- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy src` passes in strict mode.
- [ ] MCP exported-schema contract check passes.
- [ ] `contract_version` history gate passes.
- [ ] `docs/TOOLS.md` drift gate passes.
- [ ] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [ ] Record exact pytest count.
- [ ] Record exact warning count.
- [ ] Record exact coverage total/statements/misses.
- [ ] Record exact implementation head SHA.
- [ ] Record exact CI run ID.
- [ ] Record exact CI job ID.
- [ ] Merge through exact-head guard.
- [ ] Record merge commit SHA.
- [ ] Post-merge `master` CI passes on the exact merge SHA.
- [ ] Record post-merge run/job/test/coverage evidence.
- [ ] Update this TODO status to complete only after post-merge evidence is green.

---

## Definition of done

- [ ] Persistent tunnel profiles no longer depend on `Path.cwd()`.
- [ ] Missing Docker build context cannot escape Textual action handlers.
- [ ] Docker build previews are shell-safe and argv-faithful.
- [ ] Git-policy support/exhaustiveness testing fails on unhandled future production fields.
- [ ] Reviewed config writes cannot overwrite a post-review competing modification.
- [ ] Incompatible persisted custom Git-policy state is recoverable without crashing activation.
- [ ] Static architecture guards cover the complete presentation layer.
- [ ] Recursive private-directory guarantees are explicit and tested.
- [ ] Operator docs accurately describe tunnel, Docker-context, permission, and concurrency behavior.
- [ ] No raw secrets enter setup state, diagnostics, logs, UI snapshots, docs examples, or tests.
- [ ] Existing setup/config safety invariants remain intact.
- [ ] Public MCP contract remains unchanged unless deliberately versioned.
- [ ] Exact-head implementation CI is green.
- [ ] Exact-head guarded merge is complete.
- [ ] Post-merge `master` CI is green.
- [ ] Exact closure evidence is recorded in this file.
