# Post-SWPH Code Review TODO

**Status:** Open — `PSCR-001` through `PSCR-008` not started  
**Date:** 2026-09-17  
**Spec:** `docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`  
**Reviewed baseline:** `ba79759a1ed9287105d9891bbb1cefccdc396d78`  
**Task series:** `PSCR-001` through `PSCR-008`  
**Validation series:** `PSCRV-*`

This TODO implements `docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`. It addresses one functional
defect and four fidelity gaps found by re-reviewing the closed
`docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md` (SWPH-001→010) against its merged
implementation. All ten SWPH tasks were confirmed substantively implemented; these are the residue.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Use `ralph/...` branches for source work.
- [ ] Use exact-head compare-and-swap guards for writes and merges.
- [ ] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [ ] Preserve manual YAML configuration and existing CLI workflows.
- [ ] Preserve reviewed config-write semantics, serialization, and production reload.
- [ ] Preserve the SWPH-006 lock design: one non-blocking attempt, persistent inode, no retry/sleep.
- [ ] Preserve leaf-private directory semantics as documented in `docs/CONFIGURATION.md`.
- [ ] Preserve explicit operator approval and two-step reviewed Docker apply.
- [ ] Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- [ ] Add a focused regression test for every correctness defect.
- [ ] Do not weaken existing tests or architecture guards to make CI green.
- [ ] Do not bump `contract_version` unless the public MCP contract actually changes.
- [ ] Never run the full suite concurrently with a CI run on the self-hosted runner.
- [ ] Record exact SHA/PR/run/job evidence as each phase closes.

---

## Phase 1 — Installed-distribution Docker correctness

### PSCR-001 — Decide and record the installed-distribution Docker build policy

**Goal:** the Docker build-context contract must be an explicit recorded decision, not an accident of
repository layout.

- [ ] Confirm the defect on the current baseline: build a wheel and show `docker/default/` is absent.
- [ ] Confirm `parents[3]` resolves outside the package root for an installed layout.
- [ ] Confirm the development install is editable and therefore masks the condition.
- [ ] Choose Option A (support installed Docker builds) or Option B (declare them checkout-only).
- [ ] Record the chosen option and its rationale in this file.
- [ ] Record the choice in `setup/docker_setup.py` where the context is resolved.
- [ ] If Option B, remove the docstring claim that the context comes from an "installed source tree".
- [ ] Confirm the choice does not alter Docker-unavailable degraded behavior.

**Validation — PSCRV-001**

- [ ] Test or recorded evidence: the packaging outcome matches the chosen option.
- [ ] Documentation states the chosen option in operator-facing terms.

### PSCR-002 — Make bundled build-context resolution correct for the selected policy

**Goal:** resolution must not depend on repository layout.

- [ ] Remove `Path(__file__).resolve().parents[3]` arithmetic from context resolution.
- [ ] If Option A, package `docker/default/` into the distribution and resolve via
      `importlib.resources`.
- [ ] If Option B, resolve from an explicitly checkout-scoped location and fail with a distinct,
      actionable error when absent.
- [ ] Keep `validate_default_image_build_context()` as the single normalization/refusal point.
- [ ] Keep the resolved context identical between preview and execution.
- [ ] Ensure the failure message names the operator remedy and contains no secrets.

**Validation — PSCRV-002**

- [ ] Regression test proves resolution succeeds from a source checkout.
- [ ] Regression test proves the installed-layout outcome **without** depending on the development
      layout (for example by resolving against a constructed package root, not the real one).
- [ ] Test: preview argv and executed argv still resolve the same context.
- [ ] Existing `tests/setup/test_guided_domain.py` bundled-context test remains green or is replaced
      by a strictly stronger one.
- [ ] Existing SWPH Docker boundary regressions remain green unmodified.

### PSCR-003 — Decouple Docker step validation from build-context availability

**Goal:** a present configured image must be sufficient to validate the Docker step.

- [ ] Audit `validate_docker()` ordering: plan construction currently precedes `image_exists()`.
- [ ] Resolve image presence before requiring a build context.
- [ ] Make the build command preview best-effort in the validation path, not a precondition.
- [ ] Keep build-context failure actionable in `prepare_docker_build()` and `build_docker_image()`,
      where a context is genuinely required.
- [ ] Ensure the `#docker-defer-image` deferral remains reachable when the image is missing.
- [ ] Ensure no Docker client or build call occurs after a context resolution failure.
- [ ] Preserve mutation-free screen rendering and explicit approval before build.

**Validation — PSCRV-003**

- [ ] Test: image present + build context unavailable ⇒ Docker step validates.
- [ ] Test: image missing + context unavailable ⇒ actionable, deferral still reachable.
- [ ] Test: build path with unavailable context remains action-required and invokes no build.
- [ ] Test: wizard is not blocked from advancing in the image-present/context-absent case.
- [ ] Existing Docker-unavailable degraded-behavior tests remain green.

---

## Phase 2 — Review-surfaced correctness and hygiene

### PSCR-004 — Remove the vestigial tunnel `project_root` parameter

**Goal:** no dead parameter, and no `Path.cwd()` anywhere in the setup package.

- [ ] Remove `project_root` from `build_tunnel_profile_plan()`.
- [ ] Remove the `Path.cwd()` call site in `setup/step_actions.py`.
- [ ] Remove the now-obsolete "source compatibility" docstring paragraph.
- [ ] Confirm no remaining caller passes a project root.
- [ ] Keep the persistent profile bound to the active Python environment.

**Validation — PSCRV-004**

- [ ] Static or unit test: no `Path.cwd()` / `os.getcwd` in `src/mcp_chatgpt_sandbox/setup/`.
- [ ] Existing `tests/setup/test_swph_tunnel_invocation.py` remains green after signature change.
- [ ] Test: profile plan still resolves the interpreter and stays secret-free.

### PSCR-005 — Fix the `fcntl` fail-closed descriptor leak

**Goal:** the unsupported-platform path must leak nothing.

- [ ] Resolve `_fcntl_module()` before opening the lock file.
- [ ] Ensure no lock file is created on a platform that cannot lock it.
- [ ] Preserve the existing fail-closed error and its message.
- [ ] Preserve all other lock ordering and semantics unchanged.

**Validation — PSCRV-005**

- [ ] Test: simulated missing `fcntl` raises the fail-closed error, creates no lock file, and leaks
      no descriptor.
- [ ] Existing `tests/setup/test_swph_config_commit_lock.py` remains green unmodified.

---

## Phase 3 — Test and guard fidelity

### PSCR-006 — Replace the tautological Git-policy mutation tests

**Goal:** the mutation proofs must exercise the real contract.

- [ ] Replace the future-production-field test so it runs the real exhaustiveness check against a
      mutated model rather than asserting local set inequality.
- [ ] Replace the stale-guided-field test on the same basis.
- [ ] Keep the existing bidirectional production/editor/UI/manifest equality assertions.
- [ ] Keep `GIT_POLICY_GUIDED_CONTROLS` independent of `GitPolicyConfig.model_fields`.

**Validation — PSCRV-006**

- [ ] Test: the replacement fails when a field is added to the model under test and no control exists.
- [ ] Test: the replacement fails for a manifest entry absent from the model under test.
- [ ] Demonstrate the replacement is strictly stronger than what it replaces.
- [ ] Existing guided-control pilots, round-trip, and preset tests remain green.

### PSCR-007 — Strengthen the presentation persistence-ownership guard

**Goal:** the guard must not be defeatable by aliasing or trippable by a comment.

- [ ] Replace raw substring matching with parsed-source analysis (AST imports and call targets).
- [ ] Ensure a comment mentioning a forbidden token no longer fails the guard.
- [ ] Ensure an aliased YAML or atomic-write import is still caught.
- [ ] Ensure `Path.open("w")`-style persistence in a presentation module is caught.
- [ ] Keep the discovery-versus-manifest equality check unchanged.

**Validation — PSCRV-007**

- [ ] Test: benign comment mentioning a forbidden token passes.
- [ ] Test: aliased import of a config-write primitive fails.
- [ ] Test: direct file-write call in a presentation module fails.
- [ ] Existing setup static-audit suite remains green.

---

## Phase 4 — Final acceptance and closure

### PSCR-008 — Final regression, code-review, contract, and merge closure

**Goal:** close only after every touched boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [ ] Bundled Docker context resolves per the recorded policy in both layouts.
- [ ] No repository-layout-dependent path arithmetic remains in context resolution.
- [ ] A present configured image validates the Docker step without a build context.
- [ ] Build-context failures remain actionable and invoke no Docker build.
- [ ] No `Path.cwd()` remains in the setup package; no dead parameter carries one.
- [ ] Lock fail-closed path creates no file and leaks no descriptor.
- [ ] Lock ordering, non-blocking acquisition, and no-retry properties unchanged.
- [ ] Git-policy mutation tests exercise the real contract.
- [ ] Presentation ownership guard is parse-based and still enforces discovery equality.
- [ ] Leaf-private directory semantics unchanged and still matching `docs/CONFIGURATION.md`.
- [ ] Setup state remains secret-free; diagnostics remain fail-closed and non-secret.
- [ ] Manual YAML and CLI workflows remain supported.
- [ ] Documentation matches implementation for the Docker context policy.

#### Contract/version decision

- [ ] Re-export schemas and diff against `schemas/tools.json`.
- [ ] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCRV-008

- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy src` passes in strict mode.
- [ ] MCP exported-schema contract check passes.
- [ ] `contract_version` history gate passes.
- [ ] `docs/TOOLS.md` drift gate passes.
- [ ] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [ ] Confirm the run was not concurrent with a self-hosted CI run.
- [ ] Record exact pytest count.
- [ ] Record exact warning count.
- [ ] Record exact coverage total/statements/misses.
- [ ] Record exact implementation head SHA.
- [ ] Record exact CI run ID and job ID.
- [ ] Merge through exact-head guard.
- [ ] Record merge commit SHA.
- [ ] Post-merge `master` CI passes on the exact merge SHA.
- [ ] Record post-merge run/job/test/coverage evidence.
- [ ] Update this TODO status to complete only after post-merge evidence is green.

---

## Definition of done

- [ ] Bundled Docker build context resolves correctly under the recorded policy, in a source checkout
      and an installed distribution, or is documented as checkout-only with a non-blocking degraded
      path.
- [ ] A present configured image is sufficient to validate the Docker step.
- [ ] A regression test covers the installed-layout case without depending on the development layout.
- [ ] No `Path.cwd()` remains in the setup package.
- [ ] The lock fail-closed path leaks no descriptor and creates no lock file.
- [ ] Both Git-policy mutation tests exercise the real contract.
- [ ] The presentation ownership guard is parse-based.
- [ ] Existing setup/config safety invariants remain intact.
- [ ] Public MCP contract remains unchanged unless deliberately versioned.
- [ ] Exact-head implementation CI is green.
- [ ] Exact-head guarded merge is complete.
- [ ] Post-merge `master` CI is green.
- [ ] Exact closure evidence is recorded in this file.
