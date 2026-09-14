# Setup Wizard Hardening TODO

**Status:** Complete for the original SWH-001–SWH-007 scope; superseded for later second-order findings by `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md`  
**Spec:** `docs/SETUP_WIZARD_HARDENING_SPEC.md`  
**Baseline reviewed:** `340bf1bdef4cc9d4f7aa37ec97bcb13734fcdf8d`  
**Implementation replay base:** `53f9518ba206efa2aae537983601f196da3374c4`  
**Implementation PR:** #64 — `fix: harden setup wizard paths and git policy UI`  
**Implementation source head:** `c3b2ea67a299aa90bcae7b47b523343ae0a3dfa4`  
**Merged master:** `ce0b291c316dc6b654916cd49f463bae4f60c2c5`  
**Task series:** `SWH-001` through `SWH-007`  
**Validation series:** `SWHV-*`

This TODO implements the focused hardening pass defined by
`docs/SETUP_WIZARD_HARDENING_SPEC.md`. It fixes concrete issues found during the
post-merge setup wizard code review and captures the setup TUI maintainability
and UX follow-ups.

> **Historical closure note (2026-09-14):** the checked items below accurately
> record what PR #64 and its acceptance suite proved at that time; they are not a
> claim that later, stricter review found no second-order gaps. The post-hardening
> review subsequently identified and tracks additional requirements for
> cwd-independent persistent tunnel invocation, Textual Docker error boundaries,
> argv-faithful Docker previews, independent Git-policy exhaustiveness proof,
> persisted Custom-policy recovery, serialized config commits, complete
> presentation-module guards, and explicit recursive permission semantics. Those
> findings are remediated/qualified under
> `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md`. In particular,
> SWHV-006's original exhaustiveness checkbox describes the original test intent;
> SWPH-004 replaces its self-derived proof with an independent support manifest.

## Final evidence

- [x] PR #64 merged through the exact-head guarded Ralph Bridge path.
- [x] PR #64 source head: `c3b2ea67a299aa90bcae7b47b523343ae0a3dfa4`.
- [x] PR #64 merge commit on `master`: `ce0b291c316dc6b654916cd49f463bae4f60c2c5`.
- [x] Pre-merge CI run `34804404962`, job `103853298626`: success.
- [x] Pre-merge pytest: `1356 passed, 4 warnings in 665.76s`.
- [x] Pre-merge coverage: `88%`, TOTAL `9038` statements / `1120` misses.
- [x] Post-merge `master` CI run `34805286398`, job `103855873454`: success.
- [x] Post-merge pytest: `1356 passed, 4 warnings in 649.87s`.
- [x] Post-merge coverage: `88%`, TOTAL `9038` statements / `1120` misses.
- [x] Ruff format: passed before merge and after merge.
- [x] Ruff lint: passed before merge and after merge.
- [x] Strict mypy: passed before merge and after merge.
- [x] MCP contract check: passed before merge and after merge.
- [x] `contract_version` history gate: passed before merge and after merge.
- [x] `docs/TOOLS.md` drift gate: passed before merge and after merge.
- [x] Contract decision: no `contract_version` bump required because the public MCP tool schema did not change.

## Global rules

- [x] Work from an exact inspected `master` SHA.
- [x] Use `ralph/...` source branches and exact-head guarded merges.
- [x] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [x] Do not create a second weaker setup-only configuration schema.
- [x] Do not change the public MCP tool/schema contract unless a gate proves it is unavoidable.
- [x] Do not bump `contract_version` unless the MCP contract actually changes.
- [x] Never persist raw secrets in setup state.
- [x] Never echo raw secrets in diagnostics, exceptions, logs, UI snapshots, docs examples, or tests.
- [x] Preserve reviewed/CAS-bound/atomic/byte-verified/production-reloaded config-write semantics.
- [x] Preserve valid advanced config fields the wizard does not own.
- [x] Preserve manual YAML configuration and existing CLI workflows.
- [x] Preserve destructive-safety and repository-isolation invariants.
- [x] Add focused regression tests for every correctness fix.
- [x] Run focused validation plus complete Docker-backed CI before merge.
- [x] Record exact commit/run/job evidence as tasks close.

---

## Phase 1 — Correctness hardening

### SWH-001 — Stop chmodding existing arbitrary parent directories

**Goal:** prevent setup writes from silently changing permissions on operator-owned parent directories.

- [x] Audit every caller of `ensure_private_directory()`, `atomic_write_text()`, and related setup filesystem helpers.
- [x] Split setup-owned private directory creation from generic writable-parent validation.
- [x] Ensure setup-owned missing directories can still be created with restrictive permissions.
- [x] Ensure existing arbitrary parent directories are not chmodded merely because a file is written inside them.
- [x] Keep config-file and setup-state file modes restrictive where appropriate.
- [x] Preserve symlink refusal for the target file.
- [x] Preserve backup and failure-recovery behavior.
- [x] Update docs if operator-visible behavior changes.

**Validation — SWHV-001**

- [x] Regression test: writing under an existing `0755` parent leaves the parent mode unchanged.
- [x] Regression test: creating a missing setup-owned parent uses restrictive permissions.
- [x] Regression test: setup state save still creates a private setup-owned parent when needed.
- [x] Regression test: config write still rejects symlink targets.
- [x] Regression test: existing config replacement still creates a backup and reloads through production config.
- [x] Focused filesystem/config-writer coverage included in full CI.

### SWH-002 — Make setup diagnostics symlink-safe with `lstat()`

**Goal:** prevent broken symlinks or unsafe special files from passing final diagnostics.

- [x] Audit `_config_safety()`, `_audit_check()`, `_state_db_check()`, and any shared path diagnostic helpers.
- [x] Introduce or reuse a symlink-safe target inspection helper based on `lstat()`.
- [x] Reject broken symlink audit targets.
- [x] Reject symlink audit targets even when they point to regular files.
- [x] Reject directory, device, FIFO, socket, or otherwise non-regular audit targets.
- [x] Tighten state database diagnostics for unsafe existing target shapes where production semantics allow.
- [x] Preserve parent-writable checks for absent audit/state targets.
- [x] Preserve diagnostic status semantics: mandatory unsafe local target fails or requires action; optional skip stays skipped; unavailable remote verification stays unverified.
- [x] Ensure remediation text remains non-secret and does not include file contents.

**Validation — SWHV-002**

- [x] Regression test: broken symlink audit path does not return PASS.
- [x] Regression test: valid symlink audit path does not return PASS.
- [x] Regression test: directory audit path does not return PASS.
- [x] Regression test: unsafe state DB target does not return PASS.
- [x] Regression test: absent audit/state file with writable parent still behaves as intended.
- [x] Regression test: unexpected diagnostic exception still fails closed.
- [x] Headless `doctor` / `setup --check` and TUI final diagnostics use the same result model.

### SWH-003 — Quote tunnel profile server commands safely

**Goal:** make generated tunnel profiles robust when paths contain spaces or shell-sensitive characters.

- [x] Replace f-string command assembly in tunnel profile generation with `shlex.join()`.
- [x] Preserve the intended argv shape: `uv run --project <project> dev-workspace-mcp serve --config <config>`.
- [x] Ensure project paths with spaces are represented safely.
- [x] Ensure config paths with spaces are represented safely.
- [x] Ensure generated profile plans do not include raw runtime secrets.
- [x] Preserve `--no-browser` behavior.
- [x] Update ChatGPT/tunnel documentation if wording implies unsafe command interpolation.

**Validation — SWHV-003**

- [x] Regression test: generated command with spaces round-trips through shell parsing to the intended argv.
- [x] Regression test: generated command for normal paths remains equivalent to the previous intended invocation.
- [x] Regression test: profile plan contains no runtime secret material.
- [x] Regression test: `--no-browser` mode still avoids browser launches.
- [x] Focused tunnel coverage included in full CI.

### SWH-004 — Resolve Docker default build context independently of cwd

**Goal:** make the default Docker image build action reliable for installed or non-repo-root usage.

- [x] Audit current Docker build-context resolution in setup TUI/domain code.
- [x] Add a deterministic resolver for the repository-bundled default Docker build context.
- [x] Avoid relying on `Path.cwd()` for the default build context.
- [x] If the bundled context is unavailable, report a clear action-required state rather than invoking Docker.
- [x] Preserve explicit operator approval before any Docker build.
- [x] Preserve the exact build command/action preview before execution.
- [x] Preserve degraded-but-configurable Docker-unavailable behavior.
- [x] Ensure merely viewing the Docker screen performs no Docker mutation.
- [x] Update README/Quickstart if the operator guidance changes.

**Validation — SWHV-004**

- [x] Regression test: resolver finds the intended context when called from a non-repo current working directory.
- [x] Regression test: absent context returns an actionable error/result without invoking Docker.
- [x] Regression test: Docker unavailable remains action-required/degraded, not fatal.
- [x] Regression test: build action remains explicit and is not triggered by rendering the screen.
- [x] Focused Docker setup coverage included in full CI.

---

## Phase 2 — Maintainability and UX refinement

### SWH-005 — Split oversized setup TUI orchestration surface

**Goal:** reduce review risk by separating Textual app concerns without changing operator behavior.

- [x] Audit responsibilities currently concentrated in `src/dev_workspace_mcp/setup/app.py`.
- [x] Define module boundaries for lifecycle, rendering, validation, actions, runtime forms, and Git-policy forms.
- [x] Move code in small reviewable slices.
- [x] Preserve public CLI behavior and existing internal imports unless explicitly migrated.
- [x] Preserve keyboard navigation, status rendering, exit codes, state semantics, review/write flow, and diagnostics behavior.
- [x] Keep pure domain logic out of Textual-specific modules where practical.
- [x] Avoid broad rewrites that obscure safety-critical behavior.
- [x] Update development documentation with the new setup TUI module layout.

**Validation — SWHV-005**

- [x] Existing Textual pilot tests pass without weakening assertions.
- [x] Existing setup lifecycle tests pass.
- [x] Existing review/write and diagnostics tests pass.
- [x] `src/dev_workspace_mcp/setup/app.py` is materially smaller and primarily composes behavior.
- [x] `src/dev_workspace_mcp/setup/step_panels.py` remains under its static architecture guard after extracting `git_policy_panel.py`.
- [x] Ruff, strict mypy, and full pytest pass.
- [x] MCP contract remains unchanged.

### SWH-006 — Improve Git-policy custom editor ergonomics

**Goal:** make Custom Git policy editing guided rather than JSON-first while retaining complete production validation.

- [x] Audit all current `GitPolicyConfig` fields.
- [x] Add guided controls for every current `GitPolicyConfig` field or record an explicit support disposition.
- [x] Keep Recommended preset unchanged unless deliberately updated with rationale.
- [x] Keep Conservative preset no less restrictive on mutating operations.
- [x] Ensure opening the Custom editor does not mutate state until the operator applies changes.
- [x] Explain consequences for direct default-branch writes.
- [x] Explain consequences for force push and force-with-lease.
- [x] Explain consequences for branch deletion.
- [x] Explain consequences for published amend.
- [x] Explain pull strategy behavior.
- [x] Explain remote-protection modes.
- [x] Explain required-check behavior.
- [x] Validate guided custom values through `GitPolicyConfig`.
- [x] Keep any raw JSON escape hatch behind advanced/debug affordances only, if retained.

**Validation — SWHV-006**

- [x] Textual pilot test edits at least one boolean Git-policy field through guided controls.
- [x] Textual pilot test edits at least one enum Git-policy field through guided controls.
- [x] Textual pilot test edits at least one sequence/list Git-policy field through guided controls.
- [x] Custom guided values round-trip through `GitPolicyConfig`.
- [x] Exhaustiveness test fails if a new `GitPolicyConfig` field lacks a UI/support decision.
- [x] Recommended and Conservative preset tests still pass.
- [x] Existing setup materialization tests still pass.

---

## Phase 3 — Final acceptance, docs, and closure

### SWH-007 — End-to-end regression and closure audit

**Goal:** prove the hardening pass fixed the reviewed issues without regressing the completed setup wizard.

- [x] Run a final code review focused on filesystem mutation boundaries.
- [x] Run a final code review focused on diagnostics fail-closed behavior.
- [x] Run a final code review focused on tunnel profile command generation.
- [x] Run a final code review focused on Docker build-context resolution.
- [x] Run a final code review focused on setup state secret exposure.
- [x] Run a final code review focused on config write review/CAS/atomic/reload semantics.
- [x] Run a final code review focused on Git-policy field exhaustiveness.
- [x] Confirm docs accurately describe the hardened behavior.
- [x] Confirm no public MCP tool/schema changes.
- [x] Record `contract_version` decision explicitly.
- [x] Record exact implementation commit evidence.
- [x] Record exact CI run/job evidence.
- [x] Exact-head guarded merge.
- [x] Post-merge `master` CI green.

**Validation — SWHV-007**

- [x] Full Ruff format/lint passes.
- [x] Full strict mypy passes.
- [x] MCP contract check passes.
- [x] `contract_version` history gate passes.
- [x] `docs/TOOLS.md` drift gate passes.
- [x] Full Docker-backed pytest + coverage passes.
- [x] Post-merge `master` CI passes.
- [x] TODO status is updated with exact SHA/run/job/test/coverage evidence.

---

## Definition of done

- [x] Existing arbitrary parent directories are not chmodded by setup file writes.
- [x] Setup-owned missing directories still receive restrictive permissions.
- [x] Audit and state diagnostics reject unsafe symlink/special-file targets.
- [x] Tunnel profile commands are shell-safe for paths with spaces.
- [x] Docker default build context resolution no longer depends on `Path.cwd()`.
- [x] Docker build actions remain explicit and non-automatic.
- [x] Setup TUI code is split into clearer modules without behavior drift.
- [x] Git-policy Custom mode has guided controls for every current production field or an explicit support disposition.
- [x] Raw secrets remain absent from setup state/logs/diagnostics/tests/docs.
- [x] Config writes remain reviewed, CAS-bound, atomic, backed up, byte-verified, and production-reloaded.
- [x] Manual YAML and CLI workflows remain supported.
- [x] No public MCP contract drift occurs unless explicitly versioned.
- [x] Complete CI is green on the implementation head and merged `master`.
