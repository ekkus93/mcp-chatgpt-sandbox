# Setup Wizard Post-Hardening Review Specification

**Status:** Proposed  
**Date:** 2026-09-14  
**Reviewed baseline:** `dc47ba58cd8a4e9eb7f32c5b5938a172bcb53d68`  
**Source review:** post-hardening code review of current `master` and `docs/SETUP_WIZARD_HARDENING_TODO.md`  
**Task series:** `SWPH-001` through `SWPH-010`

## 1. Purpose

The setup wizard hardening work fixed the original permission, symlink, quoting, Docker-context, TUI-structure, and Git-policy UX defects and passed complete CI. A subsequent line-by-line review of the merged implementation found a smaller second-order set of integration, concurrency, architecture-guard, persistence-recovery, and documentation gaps that the previous closure did not catch.

This specification defines the remediation required to close those remaining issues without weakening the safety properties already established by the setup wizard and hardening work.

The goal is not a rewrite. The goal is to make the existing implementation truthful at its boundaries: the command that is previewed must be the command that can actually run, fail-closed states must remain inside the wizard rather than escaping its event handlers, concurrency checks must protect the write they claim to protect, and tests must prove the behavior they claim to prove.

## 2. Review baseline and evidence

The reviewed `master` is:

```text
dc47ba58cd8a4e9eb7f32c5b5938a172bcb53d68
```

The final post-closure CI for that baseline was green:

- workflow run `34811216132`;
- job `103872786458`;
- Ruff format: PASS;
- Ruff lint: PASS;
- strict mypy: PASS;
- MCP contract: PASS;
- `contract_version` history gate: PASS;
- `docs/TOOLS.md` drift gate: PASS;
- pytest: `1356 passed, 4 warnings`;
- coverage: `88%`, `9038` statements / `1120` misses.

The issues in this specification are therefore not failures to run the existing suite. They are gaps in the behavioral contract and in what the suite currently proves.

## 3. Findings to remediate

### 3.1 Tunnel profile generation still depends on the caller's current directory

The tunnel command itself is shell-quoted correctly, but the TUI supplies `Path.cwd()` as the `--project` path when it builds the profile plan. Running the installed CLI from an unrelated directory can therefore produce a safely quoted but semantically wrong command such as:

```text
uv run --project /tmp mcp-chatgpt-sandbox serve --config ...
```

The generated profile must not depend on where the operator happened to invoke the wizard.

### 3.2 Missing Docker build context can escape the Textual handler

The Docker domain helper correctly raises `SetupDockerError` when the bundled default build context is unavailable. The TUI currently computes the build preview outside the handler's existing `SetupDockerError` boundary in relevant paths. In the exact condition the hardening work intended to classify as actionable, an exception can escape the Textual event path instead of being rendered as an action-required state.

The build preview also uses plain string joining instead of shell-safe argv rendering.

### 3.3 Git-policy exhaustiveness testing is tautological

The current supported-field set is derived directly from `GitPolicyConfig.model_fields`, and the exhaustiveness test compares that derived set back to `GitPolicyConfig.model_fields`. A newly added production policy field therefore automatically appears “supported” even if no widget, form snapshot field, population logic, or action extraction exists for it.

Current field coverage is complete today. Future-drift protection is not.

### 3.4 Config replacement has a verification/write concurrency window

The config writer verifies the reviewed source digest and then performs backup/replacement in a later operation. Another process can modify the source after verification and before replacement. The external change can then be overwritten despite the writer being described as CAS-bound.

The remediation must make the concurrency semantics precise and truthful. Either the write must be serialized across the verification/replacement critical section, or the documentation/API language must be changed to accurately describe a weaker precondition check. This specification requires the stronger behavior.

### 3.5 Persisted custom Git policy can break TUI activation

Setup-state deserialization intentionally accepts a mapping for `git_policy_custom`. Widget population later converts that mapping through the guided form model. A malformed, stale, or future-incompatible mapping can therefore load successfully and then raise during presentation activation.

Persisted state incompatibility must enter an explicit repair/action-required flow instead of crashing wizard activation.

### 3.6 Static architecture guards do not cover all newly split presentation modules

The setup static-audit tests still enumerate the older presentation-file set. New presentation modules such as the hardened step-action mixin and the extracted Git-policy panel are not included in all relevant persistence-ownership and coordinator-growth guards.

The architecture tests must track the architecture they claim to protect.

### 3.7 Documentation closure was incomplete

The prior hardening spec called for operator-visible documentation updates covering parent-directory permission behavior, bundled Docker build-context availability, safe tunnel command semantics, and the split TUI architecture. Development documentation was updated, but the operator-facing documents do not fully describe the resulting behavior.

The next closure must reconcile documentation against actual implementation rather than only checking that files exist.

### 3.8 Recursive private-directory semantics are underspecified

`mkdir(parents=True, mode=0o700)` guarantees the requested mode for the leaf directory, but newly created intermediate ancestors are still subject to normal platform/umask semantics. The setup code and docs use language such as “private directory” that can be read more broadly than the implementation guarantees.

The project must explicitly choose and test one policy:

1. only the setup-owned leaf directory is required to be private; or
2. every setup-created ancestor in the missing suffix is required to be private.

The implementation, tests, and docs must agree.

## 4. Governing invariants

The remediation must preserve all of the following:

- `ServerConfig`, `GitPolicyConfig`, and `load_config()` remain authoritative production models/loaders.
- No second weaker setup-only production configuration schema is introduced.
- Raw credentials and secrets never enter resumable setup state.
- Raw credentials and secrets never appear in diagnostics, UI snapshots, logs, exceptions, docs examples, or test fixtures.
- Existing manual YAML configuration remains supported.
- Existing CLI workflows remain supported.
- Destructive operations remain explicit and approval-gated.
- Docker image building remains opt-in and never occurs merely by rendering a screen.
- Repository isolation and remote-identity checks remain intact.
- Config writes remain reviewed before execution.
- Config writes preserve fields outside the wizard's ownership boundary.
- Backups remain available for replacement of an existing valid config.
- The final written bytes are verified and reloaded through the production loader.
- The public MCP schema/tool contract must not change unless an explicit contract change is intentionally introduced and versioned.
- Changes should be incremental and reviewable; do not combine this remediation with unrelated setup-wizard redesign.

## 5. Required remediation

### SWPH-001 — Make tunnel server invocation independent of caller cwd

Introduce one deterministic source of truth for the command used by the tunnel profile to launch the MCP server.

Requirements:

- Do not use `Path.cwd()` as the project identity for a persistent tunnel profile.
- Prefer an invocation that works for an installed distribution without requiring a source checkout.
- If source-checkout execution through `uv run --project` remains supported, resolve its project root deterministically from package/repository assets rather than the caller's cwd.
- The generated command must remain represented from an argv sequence and rendered with `shlex.join()` or equivalent shell-safe semantics.
- Normal paths, spaces, quotes, and shell-sensitive characters must round-trip to the intended argv.
- The plan must not contain secrets.
- `--no-browser` behavior must remain unchanged.
- Failure to resolve a required source project must be rendered as action-required, not as a broken persisted profile.

Acceptance evidence must include an app/TUI-level test that launches the wizard with an unrelated cwd and verifies the generated profile command still targets the correct server invocation.

### SWPH-002 — Keep Docker build-context failures inside the wizard

Move every potentially failing build-context/preview resolution operation inside the same explicit setup-domain error boundary used by the Textual action.

Requirements:

- `validate_docker()` must convert a missing/unavailable bundled build context into a non-crashing validation result.
- `build_docker_image()` must do the same before any Docker mutation is attempted.
- `SetupDockerError` must not escape either Textual handler for expected operator-remediable conditions.
- The displayed build preview must be argv-faithful and shell-safe, using `shlex.join()` or equivalent rather than raw space joining.
- A missing context must never invoke Docker.
- Merely displaying the Docker screen must remain mutation-free.
- Docker-daemon unavailability must remain degraded/action-required rather than fatal to configuration.

### SWPH-003 — Make Docker build previews argv-faithful

Although tightly related to SWPH-002, command-preview correctness is an independently testable contract.

Requirements:

- Treat the build command as an argv sequence until final presentation.
- Render previews shell-safely.
- Add path-with-spaces and shell-sensitive-path regression tests.
- Ensure the preview corresponds to the exact context/image values used for the subsequent approved build.
- Do not expose environment secrets in the preview.

SWPH-002 and SWPH-003 may land in one implementation PR if the diff remains reviewable, but their tests and acceptance evidence must remain distinct.

### SWPH-004 — Replace tautological Git-policy exhaustiveness testing

Create an explicit contract between the production model and the guided editor.

Requirements:

- Define an explicit supported-field manifest, editor dataclass field set, or equivalent artifact that is independent of `GitPolicyConfig.model_fields`.
- The test must fail when a new `GitPolicyConfig` field is added without an explicit editor support decision.
- The test must also fail when the editor claims support for a field that no longer exists in the production model.
- For every supported field, prove the presence of a guided representation and extraction/population path where practical.
- Keep the production `GitPolicyConfig` as the validator for materialized values.
- Preserve Recommended and Conservative preset semantics.
- Preserve existing boolean, enum, and sequence/list pilot coverage.

### SWPH-005 — Make reviewed config replacement concurrency-safe

Turn source verification plus replacement into one coherent critical section.

Required behavior:

- A config modification that occurs after review but before replacement must be detected and must prevent overwrite.
- The implementation must define the lock scope and failure behavior explicitly.
- The lock must be bounded; do not wait forever.
- Lock acquisition failure must produce an actionable error without modifying the config.
- The source digest/identity must be revalidated while the lock is held immediately before backup/replacement.
- Backup creation, replacement, byte verification, and production reload must retain existing failure semantics.
- Temporary files must remain on the same filesystem where required for atomic replace.
- Symlink/non-regular target protections must remain fail-closed.
- Crash recovery must not leave a partially written production config.

Implementation choices may include an advisory lock file, an OS file lock where supported, or another repository-approved serialization mechanism. The design must be documented and tested rather than relying on timing assumptions.

Required tests:

- competing writer modifies the config between review and commit -> setup write is rejected;
- lock contention -> bounded actionable failure;
- unchanged reviewed config -> normal write succeeds;
- backup still captures the previous approved bytes;
- production reload still validates the committed bytes.

### SWPH-006 — Recover safely from incompatible persisted custom Git-policy state

Persisted setup state is resumable state, not a guarantee that every future UI version can render every old payload.

Requirements:

- Guided Git-policy widget population must catch expected form/schema incompatibility.
- The wizard must remain interactive.
- The Git-policy step must be marked action-required/invalid with a clear non-secret remediation message.
- The incompatible persisted custom payload must not silently mutate into defaults.
- The operator must be able to choose a preset or re-enter Custom values and validate them through `GitPolicyConfig`.
- Fresh state and valid current custom state must behave exactly as today.
- State loading must remain secret-safe.

Tests must cover malformed values, missing/extra legacy values where relevant, and a valid current mapping.

### SWPH-007 — Extend presentation architecture/static guards

Update architecture tests to reflect the actual split module graph.

At minimum, presentation ownership/static checks must include:

- `setup/app.py`;
- `setup/step_actions.py`;
- `setup/step_actions_hardened.py` while it exists;
- `setup/step_panels.py`;
- `setup/git_policy_panel.py`;
- future presentation modules through either an explicit manifest or a documented discovery rule.

Requirements:

- Presentation modules must not acquire YAML parsing or config persistence ownership.
- Presentation modules must not call atomic config-write primitives directly.
- Coordinator/module growth limits must cover the new split files.
- The guard should fail when a new presentation module is introduced without an explicit architecture disposition.
- Avoid brittle name matching if a small architecture manifest can express the boundary more clearly.

The remediation should also evaluate whether `app.py` and `step_actions.py` should be split further now or whether their current sizes are acceptable under explicit limits. Do not perform a gratuitous refactor solely to reduce line count.

### SWPH-008 — Complete operator-facing documentation obligations

Reconcile documentation against actual hardened behavior.

Required documents and content:

- `docs/CONFIGURATION.md`: explain that setup writes do not chmod arbitrary pre-existing parent directories; document the chosen recursive-private-directory policy.
- README/Quickstart: explain when the bundled/default Docker build context is available and what action the wizard reports when it is not.
- `docs/CONNECTING_CHATGPT.md`: describe the generated tunnel/server command without implying unsafe manual interpolation; examples containing shell commands with paths must be correctly quoted.
- `docs/DEVELOPMENT.md`: keep the current split architecture documentation and add any new modules/locking design introduced by this remediation.
- hardening/post-hardening TODO/spec files: closure claims must match actual tests and implementation.

Documentation examples must not contain secrets or encourage copying credential material into command lines.

### SWPH-009 — Define and enforce private recursive-directory semantics

Make directory-permission guarantees precise.

The implementation team must choose one of these explicit policies:

**Leaf-private policy**

- only the setup-owned leaf directory is guaranteed owner-only;
- existing and newly created ancestors are not silently chmodded;
- docs explicitly say so;
- tests verify leaf mode and non-mutation of existing parents.

**Created-suffix-private policy**

- every directory newly created by setup in the missing path suffix is explicitly made owner-only;
- existing ancestors are never chmodded;
- creation must fail closed if a created component cannot be secured;
- tests run under a permissive umask to prove every setup-created component receives the required mode.

Whichever policy is selected must be applied consistently to setup state, storage-path preparation, and other helpers claiming to create private directories.

### SWPH-010 — Final regression, review, and closure

Before closure, perform an explicit final review across these boundaries:

- tunnel/server invocation independence from cwd;
- Docker build-context error containment;
- shell-safe build previews;
- Git-policy field support/exhaustiveness;
- config-write concurrency behavior;
- persisted-state repair behavior;
- presentation architecture ownership;
- recursive directory permissions;
- secret exposure;
- reviewed config write/backup/verification/reload behavior;
- operator documentation accuracy;
- MCP contract/version decision.

The final closure must record exact implementation SHAs, PR numbers, CI run/job IDs, test counts, coverage, merge SHA, and post-merge `master` CI.

## 6. Validation strategy

Every correctness fix requires a focused regression test that fails on baseline `dc47ba58...` or otherwise demonstrates the missing property unambiguously.

The complete qualification gate remains:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-report=term-missing
```

CI must additionally retain:

- Docker test-image construction;
- MCP exported-schema contract check;
- `contract_version` history enforcement;
- `docs/TOOLS.md` drift enforcement.

Do not weaken or skip existing tests to make this remediation pass.

## 7. Contract-version decision

These changes are expected to be internal setup/CLI hardening and should not alter the public MCP tool/schema contract.

Expected decision:

```text
No contract_version bump required.
```

The final implementation must still run the contract and history gates. If any public MCP schema change becomes necessary, stop and treat it as an explicit contract change with the required versioning and documentation rather than silently folding it into this work.

## 8. Non-goals

This remediation does not authorize:

- redesigning the entire setup wizard;
- replacing Textual;
- changing repository isolation semantics;
- weakening Git safety defaults;
- adding automatic destructive repair;
- persisting credentials in setup state;
- broad changes to MCP tools unrelated to setup;
- changing CI merely to avoid existing failures;
- adding retries to hide concurrency or Docker lifecycle defects.

## 9. Definition of done

This follow-up is complete only when all of the following are true:

- persistent tunnel profiles work independently of the operator's cwd;
- expected Docker build-context failures remain inside the wizard as actionable state;
- build previews are shell-safe and correspond exactly to execution argv;
- Git-policy future-field drift causes a failing support/exhaustiveness test until explicitly handled;
- reviewed config replacement cannot overwrite a post-review competing edit;
- incompatible persisted custom Git-policy state enters repair/action-required flow without crashing;
- architecture/static guards cover every current presentation module;
- operator docs accurately describe permission, Docker-context, and tunnel behavior;
- recursive private-directory semantics are explicit, tested, and consistently implemented;
- existing secrets, config ownership, destructive-safety, and manual-config invariants remain intact;
- Ruff format/lint, strict mypy, full Docker-backed pytest/coverage, MCP contract, contract-version, and docs-drift gates pass on the exact implementation head;
- the implementation is merged through an exact-head guard;
- post-merge `master` CI is green;
- the companion TODO records exact closure evidence.
