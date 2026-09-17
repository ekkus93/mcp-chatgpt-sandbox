# Setup Wizard Post-Hardening Review TODO

**Status:** Complete — SWPH-001 through SWPH-010 closed  
**Date:** 2026-09-14  
**Spec:** `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_SPEC_2026-09-14.md`  
**Reviewed baseline:** `dc47ba58cd8a4e9eb7f32c5b5938a172bcb53d68`  
**Task series:** `SWPH-001` through `SWPH-010`  
**Validation series:** `SWPHV-*`

This TODO implements the post-hardening remediation defined by `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_SPEC_2026-09-14.md`. It addresses second-order defects found after `docs/SETUP_WIZARD_HARDENING_TODO.md` had been closed on green CI.

## Global rules

- [x] Start from an exact inspected `master` SHA.
- [x] Use `ralph/...` branches for source work.
- [x] Use exact-head compare-and-swap guards for writes and merges.
- [x] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [x] Do not introduce a weaker setup-only production schema.
- [x] Preserve manual YAML configuration and existing CLI workflows.
- [x] Preserve repository isolation and destructive-safety semantics.
- [x] Preserve reviewed config-write semantics and production reload.
- [x] Never persist raw secrets in setup state.
- [x] Never print raw secrets in UI, diagnostics, logs, exceptions, docs, or tests.
- [x] Add a focused regression test for every correctness defect.
- [x] Do not weaken existing tests or architecture guards to make CI green.
- [x] Do not bump `contract_version` unless the public MCP contract actually changes.
- [x] Record exact SHA/PR/run/job evidence as each phase closes.

---

## Phase 1 — Invocation and Docker integration correctness

### SWPH-001 — Make tunnel server invocation independent of caller cwd

**Goal:** persistent Secure MCP Tunnel profiles must launch the intended server regardless of the directory from which setup was invoked.

- [x] Audit every call to `build_tunnel_profile_plan()` and every source of its project/server invocation.
- [x] Remove `Path.cwd()` as the persistent tunnel project's identity.
- [x] Define one deterministic installed/source-checkout server invocation strategy.
- [x] Source checkout mode does not persist a project root: the tunnel command reuses the exact active Python environment and invokes `python -m mcp_chatgpt_sandbox`.
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

- [x] Audit `validate_docker()` for operations that can raise `SetupDockerError` outside its handler boundary.
- [x] Audit `build_docker_image()` for the same condition.
- [x] Move build-context/preview resolution inside explicit error handling.
- [x] Convert missing bundled context into a clear action-required validation message.
- [x] Ensure no Docker client/build call occurs after context resolution fails.
- [x] Preserve explicit operator approval before build execution.
- [x] Preserve Docker-unavailable degraded behavior.
- [x] Preserve mutation-free screen rendering.

**Validation — SWPHV-002**

- [x] TUI/action test: missing bundled context during validation does not raise out of the handler.
- [x] TUI/action test: missing bundled context during build does not raise out of the handler.
- [x] Test: Docker build is not invoked when context resolution fails.
- [x] Test: validation message is actionable and non-secret.
- [x] Existing Docker-unavailable tests remain green.

**Closure evidence (2026-09-14)**

- Implementation shared with SWPH-003: PR #70, exact head `8423346f95bdaebc141cd762f779b46e2e6318d8`.
- Exact-head PR CI: run `34862584254`, job `104038324557` — all quality/contract/docs gates PASS; `1388 passed, 4 warnings`; coverage `88%` (`9213` statements / `1140` misses).
- Focused regressions in `tests/setup/test_swph_docker_boundaries.py` prove validation/build context failures stay in the Textual boundary, no Docker client/build is reached after context failure, and daemon-unavailable behavior remains degraded/actionable.
- Guarded merge: PR #70 merged to `master` as `edf903164c726ed2caf7149fb3c6666710617df8`.
- Post-merge `master` CI: run `34866181687`, job `104050482204` — PASS.

### SWPH-003 — Make Docker build previews shell-safe and argv-faithful

**Goal:** the reviewed command shown to the operator must represent the exact approved build argv safely.

- [x] Keep the build command as an argv sequence until presentation.
- [x] Replace raw `" ".join(...)` preview rendering with `shlex.join()` or equivalent.
- [x] Ensure preview and execution resolve the same context path.
- [x] Ensure preview and execution use the same image/tag value.
- [x] Keep preview free of secrets/environment credentials.

**Validation — SWPHV-003**

- [x] Regression test: context path with spaces renders and round-trips correctly.
- [x] Regression test: shell-sensitive context path renders and round-trips correctly.
- [x] Regression test: preview argv equals executed approved argv.
- [x] Regression test: normal-path preview remains equivalent to intended existing behavior.

**Closure evidence (2026-09-14)**

- Shared implementation/qualification with SWPH-002: PR #70 at `8423346f95bdaebc141cd762f779b46e2e6318d8`, CI run `34862584254`, job `104038324557` — PASS.
- `tests/setup/test_swph_docker_boundaries.py` uses a context containing spaces and shell metacharacters, verifies `shlex.split(rendered_preview) == list(plan.argv)`, verifies the exact reviewed image/context reaches the build function, and verifies a process environment sentinel does not appear in the preview.
- Guarded merge: `edf903164c726ed2caf7149fb3c6666710617df8`; post-merge CI `34866181687` / job `104050482204` — PASS.

---

## Phase 2 — Git-policy and persisted-state correctness

### SWPH-004 — Replace tautological Git-policy exhaustiveness testing

**Goal:** adding a production Git-policy field must require an explicit guided-editor support decision.

- [x] Replace `GIT_POLICY_GUIDED_FIELDS = frozenset(GitPolicyConfig.model_fields)` or any equivalent self-derived manifest.
- [x] Define an independent explicit support manifest or derive support from editor implementation fields.
- [x] Ensure support manifest -> production model equality is checked in both directions.
- [x] Ensure every supported field maps to a guided representation or an explicit documented disposition.
- [x] Ensure editor extraction covers every supported field.
- [x] Ensure widget population covers every supported field where the field is editable.
- [x] Keep final materialization validation through `GitPolicyConfig`.
- [x] Preserve Recommended preset semantics.
- [x] Preserve Conservative preset semantics.

**Validation — SWPHV-004**

- [x] Mutation-style test or equivalent proof: adding a fake/new production field without UI support fails the exhaustiveness contract.
- [x] Test: stale supported field not present in production model fails.
- [x] Existing boolean guided-control pilot remains green.
- [x] Existing enum guided-control pilot remains green.
- [x] Existing list/sequence guided-control pilot remains green.
- [x] Existing round-trip test remains green.
- [x] Recommended/Conservative preset tests remain green.

**Closure evidence (2026-09-14)**

- Implementation: PR #71, exact head `bd92ea5fa3d627f88607061d5277e421574a3f6e`.
- Exact-head PR CI: run `34866298117`, job `104050868596` — all gates PASS; `1392 passed, 4 warnings`; coverage `88%` (`9215` statements / `1140` misses).
- `GIT_POLICY_GUIDED_CONTROLS` is an explicit independent manifest. `tests/setup/test_swph_git_policy_exhaustiveness.py` checks production/editor/UI equality bidirectionally, future production-field mutation failure, stale guided-field failure, and every field's population/extraction controls.
- Guarded merge: PR #71 merged as `615400e34532ec991941f120a01fa20fbea15630`.
- Post-merge `master` CI: run `34872137270`, job `104070365305` — PASS.

### SWPH-005 — Recover from incompatible persisted custom Git-policy state

**Goal:** stale or malformed resumable state must enter repair/action-required flow rather than crashing activation.

- [x] Identify the expected exceptions raised by guided custom-policy population.
- [x] Catch those exceptions at the presentation/state boundary, not with a broad silent swallow.
- [x] Keep the wizard interactive after the incompatibility is detected.
- [x] Mark the Git-policy step invalid/action-required.
- [x] Display a clear non-secret remediation message.
- [x] Do not silently replace incompatible custom values with Recommended defaults.
- [x] Allow the operator to select a preset and recover.
- [x] Allow the operator to re-enter Custom values and validate through `GitPolicyConfig`.
- [x] Preserve valid current custom state behavior.
- [x] Preserve fresh-state behavior.

**Validation — SWPHV-005**

- [x] Test: malformed persisted enum value does not crash activation.
- [x] Test: malformed persisted list/sequence value does not crash activation.
- [x] Test: legacy missing/extra field shape is handled according to the documented migration policy.
- [x] Test: remediation state remains non-secret.
- [x] Test: operator can recover by applying a preset.
- [x] Test: operator can recover by entering valid Custom values.
- [x] Test: valid current custom state still populates all guided controls.

**Closure evidence (2026-09-14)**

- Implementation: PR #77, corrected exact head `033f8e3bd5f710e764aa2080c1776039fc07ec2f`.
- Exact-head PR CI: run `34877252403`, job `104087400148` — PASS across format/lint, strict mypy, contract gates, docs drift, and full pytest.
- `tests/setup/test_swph_git_policy_recovery.py` covers malformed enum/list values, non-secret repair state, preset recovery, Custom re-entry, valid current state population, missing legacy fields using production defaults, and legacy extra fields requiring repair.
- Guarded merge: PR #77 merged as `f1a06e8f7c8eea806181cee5a40fdb7ea440d9e8`.
- Post-merge `master` CI: run `34880324736`, job `104097732881` — `1403 passed, 4 warnings`; coverage `88%` (`9234` statements / `1129` misses).

---

## Phase 3 — Config-write concurrency safety

### SWPH-006 — Make reviewed config replacement genuinely concurrency-safe

**Goal:** a competing post-review config edit must never be overwritten by the setup writer.

- [x] Document the current verification/write race precisely.
- [x] Choose and document one serialization mechanism.
- [x] Define lock identity/location and ownership.
- [x] Define bounded lock acquisition behavior.
- [x] Define stale-lock behavior if the chosen mechanism requires it.
- [x] Acquire serialization before final source verification.
- [x] Revalidate source digest/identity while serialization is held.
- [x] Reject any post-review source change.
- [x] Keep backup creation inside the protected critical section where required.
- [x] Preserve same-filesystem atomic replacement.
- [x] Preserve byte-for-byte post-write verification.
- [x] Preserve production `load_config()` reload after write.
- [x] Preserve target symlink/non-regular refusal.
- [x] Preserve valid advanced/unowned config fields.
- [x] Ensure lock/temporary artifacts contain no secrets beyond what the protected config already contains.
- [x] Ensure unexpected lock/write errors fail closed without leaving a partial production config.

**Validation — SWPHV-006**

- [x] Concurrency regression: external modification after review but before commit is rejected.
- [x] Test: bounded lock contention returns actionable failure.
- [x] Test: unchanged reviewed source commits normally.
- [x] Test: backup contains exact prior approved bytes.
- [x] Test: failed production reload follows existing recovery semantics.
- [x] Test: symlink target remains rejected under the locked path.
- [x] Test: concurrent writers cannot both successfully commit against the same reviewed source.

**Closure evidence (2026-09-14)**

- Final implementation: PR #83, exact head `6488c7d86f42481673a04bb997ff11a83ea42096`.
- Serialization uses persistent owner-only sibling `.CONFIG.setup.lock`, a single non-blocking exclusive `flock()` attempt, kernel descriptor lifetime for stale-lock recovery, source revalidation after acquisition, and backup/atomic replacement/byte verification/production reload inside the protected section. Contention fails immediately/actionably; no retry/sleep framework was introduced.
- Exact-head PR CI: run `34889700394`, job `104128985619` — all gates PASS; `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).
- Focused `tests/setup/test_swph_config_commit_lock.py` proves contention does not mutate config, revalidation occurs after lock acquisition, exact backup/reload behavior, unsafe lock-target rejection, concurrent writers cannot both commit the same reviewed source, and the persistent lock file is empty/owner-only.
- Guarded merge: PR #83 merged as `36cb3c46ac55c5bcaa22caba98cb78af5a13c202`.
- Post-merge `master` CI: run `34892078795`, job `104136982502` — `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).

---

## Phase 4 — Architecture guard and permission semantics

### SWPH-007 — Extend presentation architecture/static guards

**Goal:** static architecture tests must cover every module in the current Textual presentation layer.

- [x] Enumerate the current presentation modules explicitly or define a documented discovery rule.
- [x] Include `setup/app.py`.
- [x] Include `setup/step_actions.py`.
- [x] Include `setup/step_actions_hardened.py` while it exists.
- [x] Include `setup/step_panels.py`.
- [x] Include `setup/git_policy_panel.py`.
- [x] Apply YAML/config-persistence ownership checks to every presentation module.
- [x] Apply coordinator/module-growth checks to every relevant presentation module.
- [x] Make introduction of a new presentation module require an explicit architecture disposition.
- [x] Review whether `step_actions_hardened.py` should remain separate or be folded into the main action layer after hardening stabilizes.
- [x] Review `app.py` and `step_actions.py` current sizes against explicit limits; refactor only if a real responsibility split is justified.

**Validation — SWPHV-007**

- [x] Static audit fails when a guarded presentation module imports/uses config-write primitives directly.
- [x] Static audit fails when an unapproved presentation module exceeds its architecture limit.
- [x] Static audit or manifest test fails when a new presentation module lacks a support/ownership disposition.
- [x] Existing setup static-audit suite remains green.

**Closure evidence (2026-09-14)**

- Final implementation: PR #82, exact head `744307f56a54fd1330e16b1eab73b02f01b9d8b1`.
- `tests/setup/test_setup_static_audit.py` now independently discovers presentation modules and requires exact equality with an explicit per-module manifest: `app.py`, `step_actions.py`, `step_actions_docker.py`, `step_actions_hardened.py`, `step_panels.py`, and `git_policy_panel.py`. It applies persistence-ownership checks and explicit line budgets to all discovered presentation modules.
- `step_actions_hardened.py` remains separate by documented architecture decision because it is a small guided Git-policy adapter; folding it back would increase coordinator size without clarifying ownership.
- Exact-head PR CI: run `34883712692`, job `104109014884` — `1404 passed, 4 warnings`; coverage `88%` (`9234` statements / `1129` misses).
- Guarded merge: PR #82 merged as `329db8324c978d9d56d5f41439fffdc902cba2f7`.
- Post-merge `master` CI: run `34888389963`, job `104124626899` — PASS.

### SWPH-008 — Define and enforce recursive private-directory semantics

**Goal:** implementation, tests, and docs must make the permission guarantee unambiguous.

- [x] Choose `leaf-private` or `created-suffix-private` policy: `leaf-private` selected.
- [x] Record the choice in code comments/docs.
- [x] Audit `ensure_private_directory()`.
- [x] Audit setup-state parent creation.
- [x] Audit storage-directory creation in `host.py`.
- [x] Audit any other setup helper claiming to create a private directory.
- [x] Never chmod pre-existing arbitrary ancestors.
- [x] Created-suffix-private ancestor hardening is not applicable because `leaf-private` was selected; arbitrary/intermediate ancestors are not chmodded by this policy.
- [x] For `leaf-private`, state clearly that intermediate ancestors follow normal filesystem/umask semantics.
- [x] Fail closed if a directory the policy requires to be private cannot be secured.

**Validation — SWPHV-008**

- [x] Test under permissive umask `000`.
- [x] Test pre-existing `0755` parent remains unchanged.
- [x] Test selected private leaf receives required owner-only mode.
- [x] Created-suffix-private ancestor-mode validation is not applicable because `leaf-private` was selected.
- [x] For `leaf-private`, test/document that intermediate created ancestors are outside the private guarantee.
- [x] Setup-state and storage-directory helpers obey the same selected policy.

**Closure evidence (2026-09-14)**

- Implementation: PR #80, exact head `70257c78e5449de009476e35dcf4b36fe381d9c6`.
- `ensure_private_directory()` explicitly documents leaf-private semantics: the selected leaf is forced to `0700`, pre-existing arbitrary ancestors are untouched, and newly created intermediate ancestors follow normal umask semantics.
- `tests/setup/test_swph_private_directory_semantics.py` exercises umask `000`, a pre-existing `0755` parent, intermediate `0777` paths outside the guarantee, `0700` selected leaves, fail-closed chmod failure, setup-state persistence, and storage-directory helpers.
- Exact-head PR CI: run `34876620679`, job `104085292110` — PASS.
- Guarded merge: PR #80 merged as `25206e921e2aa7b4d22a4fa7b2540fdf72fc1544`.
- Post-merge `master` CI: run `34877861514`, job `104089426171` — `1396 passed, 4 warnings`; coverage `88%` (`9215` statements / `1137` misses).

---

## Phase 5 — Documentation reconciliation

### SWPH-009 — Complete setup-wizard hardening documentation obligations

**Goal:** operator and developer documentation must describe actual behavior after the remediation.

- [x] Update `docs/CONFIGURATION.md` with parent-directory non-mutation behavior.
- [x] Update `docs/CONFIGURATION.md` with the selected recursive-private-directory policy.
- [x] Update README/Quickstart with bundled Docker build-context availability/failure behavior.
- [x] Update `docs/CONNECTING_CHATGPT.md` with the deterministic tunnel/server invocation model.
- [x] Ensure shell-command examples containing paths use correct quoting.
- [x] Update `docs/DEVELOPMENT.md` with any new config locking/serialization design.
- [x] Update `docs/DEVELOPMENT.md` with the final presentation module layout.
- [x] Reconcile older hardening docs so closure claims no longer overstate what earlier tests proved.
- [x] Keep documentation examples secret-free.

**Validation — SWPHV-009**

- [x] Manual docs audit confirms implementation and docs agree on tunnel invocation.
- [x] Manual docs audit confirms implementation and docs agree on Docker context behavior.
- [x] Manual docs audit confirms implementation and docs agree on parent-directory permissions.
- [x] Manual docs audit confirms implementation and docs agree on config concurrency semantics.
- [x] `docs/TOOLS.md` drift check remains green.

**Closure evidence (2026-09-14)**

- Primary docs reconciliation: PR #81, exact head `9a7a00833550788dc68ec73275a214437a1c72df`, CI run `34877669940`, job `104088781600` — PASS; merged as `c95f41d50981fe7ac46448fd56e4f4df8aa47734`.
- Final SWPH-010 audit caught one stale sentence after SWPH-006's no-retry correction: `docs/DEVELOPMENT.md` still described a two-second polling lock wait. PR #84 corrected it to the actual one-attempt non-blocking `flock()` behavior and immediate actionable contention failure.
- Final documentation head: `b1a40da977218b8ecdd66e60ff85b24bc3103860` (PR #84).
- Exact-head PR #84 CI: run `34893168699`, job `104140569019` — all gates PASS; `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).
- Guarded merge: PR #84 merged as `6c6dcebe68978637306b973aecaf235566215eb7`.
- Post-merge `master` CI: run `34895592205`, job `104148640509` — all gates PASS; `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).

---

## Phase 6 — Final acceptance and closure

### SWPH-010 — Final regression, code-review, contract, and merge closure

**Goal:** close the follow-up only after every reviewed boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [x] Tunnel profile/server invocation is independent of caller cwd.
- [x] Tunnel command rendering is argv-faithful and shell-safe.
- [x] Docker context-resolution errors remain inside TUI validation/action boundaries.
- [x] Docker preview exactly represents execution argv.
- [x] Git-policy exhaustiveness test is independent/non-tautological.
- [x] Every current `GitPolicyConfig` field has a guided support disposition.
- [x] Persisted incompatible custom policy enters recoverable action-required state.
- [x] Config replacement is serialized across final verification and replacement.
- [x] Competing post-review config edit cannot be overwritten.
- [x] Presentation modules do not own config persistence.
- [x] Architecture guards cover all current presentation modules.
- [x] Recursive private-directory semantics are implemented consistently.
- [x] Existing arbitrary parents are never chmodded as a side effect of setup writes.
- [x] Setup state remains secret-free.
- [x] Diagnostics remain fail-closed and non-secret.
- [x] Config write remains reviewed, backed up, atomically replaced, byte-verified, and production-reloaded.
- [x] Manual YAML and CLI workflows remain supported.
- [x] Documentation matches implementation.

#### Contract/version decision

- [x] Confirm public MCP tool/schema contract is unchanged.
- [x] Record explicit decision: no `contract_version` bump required because the public MCP contract is unchanged.
- [x] Contract-change workflow is not required because exported tool/schema behavior did not change; the normal workflow would remain mandatory for any future public contract change.

#### Complete validation — SWPHV-010

- [x] `uv run ruff format --check .` passes.
- [x] `uv run ruff check .` passes.
- [x] `uv run mypy src` passes in strict mode.
- [x] MCP exported-schema contract check passes.
- [x] `contract_version` history gate passes.
- [x] `docs/TOOLS.md` drift gate passes.
- [x] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [x] Record exact pytest count: `1410 passed`.
- [x] Record exact warning count: `4 warnings`.
- [x] Record exact coverage total/statements/misses: `88%`, `9285` statements / `1137` misses.
- [x] Record exact implementation head SHA: `b1a40da977218b8ecdd66e60ff85b24bc3103860`.
- [x] Record exact CI run ID: `34893168699`.
- [x] Record exact CI job ID: `104140569019`.
- [x] Merge through exact-head guard: PR #84 merged at exact head `b1a40da977218b8ecdd66e60ff85b24bc3103860`.
- [x] Record merge commit SHA: `6c6dcebe68978637306b973aecaf235566215eb7`.
- [x] Post-merge `master` CI passes on the exact merge SHA.
- [x] Record post-merge run/job/test/coverage evidence: run `34895592205`, job `104148640509`; `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).
- [x] Update this TODO status to complete only after post-merge evidence is green.

**Final acceptance evidence (2026-09-14)**

- Final reviewed implementation/documentation head: `b1a40da977218b8ecdd66e60ff85b24bc3103860` (PR #84), based on the fully integrated SWPH-001→009 implementation.
- Exact-head CI `34893168699` / job `104140569019`: Ruff format PASS; Ruff lint PASS; strict mypy PASS; MCP exported-schema contract PASS; contract-version history PASS; docs/TOOLS drift PASS; full Docker-backed pytest PASS with `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).
- Public MCP tool/schema contract remained unchanged throughout this task series; no `contract_version` bump was required.
- Guarded exact-head merge produced `master` SHA `6c6dcebe68978637306b973aecaf235566215eb7`.
- Exact post-merge `master` CI `34895592205` / job `104148640509` passed every gate with `1410 passed, 4 warnings`; coverage `88%` (`9285` statements / `1137` misses).
- Final code review rechecked tunnel invocation/quoting, Docker action boundaries and argv fidelity, independent Git-policy coverage, persisted-state repair, serialized config replacement, presentation ownership/size guards, leaf-private directory semantics, secret boundaries, diagnostics, reviewed config writing, manual YAML/CLI support, and documentation/implementation agreement.

---

## Definition of done

- [x] Persistent tunnel profiles no longer depend on `Path.cwd()`.
- [x] Missing Docker build context cannot escape Textual action handlers.
- [x] Docker build previews are shell-safe and argv-faithful.
- [x] Git-policy support/exhaustiveness testing fails on unhandled future production fields.
- [x] Reviewed config writes cannot overwrite a post-review competing modification.
- [x] Incompatible persisted custom Git-policy state is recoverable without crashing activation.
- [x] Static architecture guards cover the complete presentation layer.
- [x] Recursive private-directory guarantees are explicit and tested.
- [x] Operator docs accurately describe tunnel, Docker-context, permission, and concurrency behavior.
- [x] No raw secrets enter setup state, diagnostics, logs, UI snapshots, docs examples, or tests.
- [x] Existing setup/config safety invariants remain intact.
- [x] Public MCP contract remains unchanged unless deliberately versioned.
- [x] Exact-head implementation CI is green.
- [x] Exact-head guarded merge is complete.
- [x] Post-merge `master` CI is green.
- [x] Exact closure evidence is recorded in this file.
