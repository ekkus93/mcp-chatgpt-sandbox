# Setup Wizard Hardening TODO

**Status:** Proposed / implementation not started  
**Spec:** `docs/SETUP_WIZARD_HARDENING_SPEC.md`  
**Baseline reviewed:** `340bf1bdef4cc9d4f7aa37ec97bcb13734fcdf8d`  
**Task series:** `SWH-001` through `SWH-007`  
**Validation series:** `SWHV-*`

This TODO implements the focused hardening pass defined by `docs/SETUP_WIZARD_HARDENING_SPEC.md`. It fixes concrete issues found during the post-merge setup wizard code review and captures two follow-up maintainability/UX improvements.

## Global rules

- [ ] Work from an exact inspected `master` SHA.
- [ ] Use `ralph/...` source branches and exact-head guarded merges.
- [ ] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [ ] Do not create a second weaker setup-only configuration schema.
- [ ] Do not change the public MCP tool/schema contract unless a gate proves it is unavoidable.
- [ ] Do not bump `contract_version` unless the MCP contract actually changes.
- [ ] Never persist raw secrets in setup state.
- [ ] Never echo raw secrets in diagnostics, exceptions, logs, UI snapshots, docs examples, or tests.
- [ ] Preserve reviewed/CAS-bound/atomic/byte-verified/production-reloaded config-write semantics.
- [ ] Preserve valid advanced config fields the wizard does not own.
- [ ] Preserve manual YAML configuration and existing CLI workflows.
- [ ] Preserve destructive-safety and repository-isolation invariants.
- [ ] Add focused regression tests for every correctness fix.
- [ ] Run focused validation plus complete Docker-backed CI before merge.
- [ ] Record exact commit/run/job evidence as tasks close.

---

## Phase 1 — Correctness hardening

### SWH-001 — Stop chmodding existing arbitrary parent directories

**Goal:** prevent setup writes from silently changing permissions on operator-owned parent directories.

- [ ] Audit every caller of `ensure_private_directory()`, `atomic_write_text()`, and related setup filesystem helpers.
- [ ] Split setup-owned private directory creation from generic writable-parent validation.
- [ ] Ensure setup-owned missing directories can still be created with restrictive permissions.
- [ ] Ensure existing arbitrary parent directories are not chmodded merely because a file is written inside them.
- [ ] Keep config-file and setup-state file modes restrictive where appropriate.
- [ ] Preserve symlink refusal for the target file.
- [ ] Preserve backup and failure-recovery behavior.
- [ ] Update docs if operator-visible behavior changes.

**Validation — SWHV-001**

- [ ] Regression test: writing under an existing `0755` parent leaves the parent mode unchanged.
- [ ] Regression test: creating a missing setup-owned parent uses restrictive permissions.
- [ ] Regression test: setup state save still creates a private setup-owned parent when needed.
- [ ] Regression test: config write still rejects symlink targets.
- [ ] Regression test: existing config replacement still creates a backup and reloads through production config.
- [ ] Focused filesystem/config-writer tests pass.

### SWH-002 — Make setup diagnostics symlink-safe with `lstat()`

**Goal:** prevent broken symlinks or unsafe special files from passing final diagnostics.

- [ ] Audit `_config_safety()`, `_audit_check()`, `_state_db_check()`, and any shared path diagnostic helpers.
- [ ] Introduce or reuse a symlink-safe target inspection helper based on `lstat()`.
- [ ] Reject broken symlink audit targets.
- [ ] Reject symlink audit targets even when they point to regular files.
- [ ] Reject directory, device, FIFO, socket, or otherwise non-regular audit targets.
- [ ] Tighten state database diagnostics for unsafe existing target shapes where production semantics allow.
- [ ] Preserve parent-writable checks for absent audit/state targets.
- [ ] Preserve diagnostic status semantics: mandatory unsafe local target fails or requires action; optional skip stays skipped; unavailable remote verification stays unverified.
- [ ] Ensure remediation text remains non-secret and does not include file contents.

**Validation — SWHV-002**

- [ ] Regression test: broken symlink audit path does not return PASS.
- [ ] Regression test: valid symlink audit path does not return PASS.
- [ ] Regression test: directory audit path does not return PASS.
- [ ] Regression test: unsafe state DB target does not return PASS.
- [ ] Regression test: absent audit/state file with writable parent still behaves as intended.
- [ ] Regression test: unexpected diagnostic exception still fails closed.
- [ ] Headless `doctor` / `setup --check` and TUI final diagnostics use the same result model.

### SWH-003 — Quote tunnel profile server commands safely

**Goal:** make generated tunnel profiles robust when paths contain spaces or shell-sensitive characters.

- [ ] Replace f-string command assembly in tunnel profile generation with a shell-safe mechanism such as `shlex.join()`.
- [ ] Preserve the intended argv shape: `uv run --project <project> dev-workspace-mcp serve --config <config>`.
- [ ] Ensure project paths with spaces are represented safely.
- [ ] Ensure config paths with spaces are represented safely.
- [ ] Ensure generated profile plans do not include raw runtime secrets.
- [ ] Preserve `--no-browser` behavior.
- [ ] Update ChatGPT/tunnel documentation if wording implies unsafe command interpolation.

**Validation — SWHV-003**

- [ ] Regression test: generated command with spaces round-trips through shell parsing to the intended argv.
- [ ] Regression test: generated command for normal paths remains equivalent to the previous intended invocation.
- [ ] Regression test: profile plan contains no runtime secret material.
- [ ] Regression test: `--no-browser` mode still avoids browser launches.
- [ ] Focused tunnel tests pass.

### SWH-004 — Resolve Docker default build context independently of cwd

**Goal:** make the default Docker image build action reliable for installed or non-repo-root usage.

- [ ] Audit current Docker build-context resolution in setup TUI/domain code.
- [ ] Add a deterministic resolver for the repository-bundled default Docker build context.
- [ ] Avoid relying on `Path.cwd()` for the default build context.
- [ ] If the bundled context is unavailable, report a clear action-required state rather than invoking Docker.
- [ ] Preserve explicit operator approval before any Docker build.
- [ ] Preserve the exact build command/action preview before execution.
- [ ] Preserve degraded-but-configurable Docker-unavailable behavior.
- [ ] Ensure merely viewing the Docker screen performs no Docker mutation.
- [ ] Update README/Quickstart if the operator guidance changes.

**Validation — SWHV-004**

- [ ] Regression test: resolver finds the intended context when called from a non-repo current working directory.
- [ ] Regression test: absent context returns an actionable error/result without invoking Docker.
- [ ] Regression test: Docker unavailable remains action-required/degraded, not fatal.
- [ ] Regression test: build action remains explicit and is not triggered by rendering the screen.
- [ ] Focused Docker setup tests pass.

---

## Phase 2 — Maintainability and UX refinement

### SWH-005 — Split oversized setup TUI orchestration surface

**Goal:** reduce review risk by separating Textual app concerns without changing operator behavior.

- [ ] Audit responsibilities currently concentrated in `src/dev_workspace_mcp/setup/app.py`.
- [ ] Define module boundaries for lifecycle, rendering, validation, actions, runtime forms, and Git-policy forms.
- [ ] Move code in small reviewable slices.
- [ ] Preserve public CLI behavior and existing internal imports unless explicitly migrated.
- [ ] Preserve keyboard navigation, status rendering, exit codes, state semantics, review/write flow, and diagnostics behavior.
- [ ] Keep pure domain logic out of Textual-specific modules where practical.
- [ ] Avoid broad rewrites that obscure safety-critical behavior.
- [ ] Update development documentation with the new setup TUI module layout.

**Validation — SWHV-005**

- [ ] Existing Textual pilot tests pass without weakening assertions.
- [ ] Existing setup lifecycle tests pass.
- [ ] Existing review/write and diagnostics tests pass.
- [ ] `src/dev_workspace_mcp/setup/app.py` is materially smaller and primarily composes behavior.
- [ ] Ruff, strict mypy, and full pytest pass.
- [ ] MCP contract remains unchanged.

### SWH-006 — Improve Git-policy custom editor ergonomics

**Goal:** make Custom Git policy editing guided rather than JSON-first while retaining complete production validation.

- [ ] Audit all current `GitPolicyConfig` fields.
- [ ] Add guided controls for every current `GitPolicyConfig` field or record an explicit support disposition.
- [ ] Keep Recommended preset unchanged unless deliberately updated with rationale.
- [ ] Keep Conservative preset no less restrictive on mutating operations.
- [ ] Ensure opening the Custom editor does not mutate state until the operator applies changes.
- [ ] Explain consequences for direct default-branch writes.
- [ ] Explain consequences for force push and force-with-lease.
- [ ] Explain consequences for branch deletion.
- [ ] Explain consequences for published amend.
- [ ] Explain pull strategy behavior.
- [ ] Explain remote-protection modes.
- [ ] Explain required-check behavior.
- [ ] Validate guided custom values through `GitPolicyConfig`.
- [ ] Keep any raw JSON escape hatch behind advanced/debug affordances only, if retained.

**Validation — SWHV-006**

- [ ] Textual pilot test edits at least one boolean Git-policy field through guided controls.
- [ ] Textual pilot test edits at least one enum Git-policy field through guided controls.
- [ ] Textual pilot test edits at least one sequence/list Git-policy field through guided controls.
- [ ] Custom guided values round-trip through `GitPolicyConfig`.
- [ ] Exhaustiveness test fails if a new `GitPolicyConfig` field lacks a UI/support decision.
- [ ] Recommended and Conservative preset tests still pass.
- [ ] Existing setup materialization tests still pass.

---

## Phase 3 — Final acceptance, docs, and closure

### SWH-007 — End-to-end regression and closure audit

**Goal:** prove the hardening pass fixed the reviewed issues without regressing the completed setup wizard.

- [ ] Run a final code review focused on filesystem mutation boundaries.
- [ ] Run a final code review focused on diagnostics fail-closed behavior.
- [ ] Run a final code review focused on tunnel profile command generation.
- [ ] Run a final code review focused on Docker build-context resolution.
- [ ] Run a final code review focused on setup state secret exposure.
- [ ] Run a final code review focused on config write review/CAS/atomic/reload semantics.
- [ ] Run a final code review focused on Git-policy field exhaustiveness.
- [ ] Confirm docs accurately describe the hardened behavior.
- [ ] Confirm no public MCP tool/schema changes.
- [ ] Record `contract_version` decision explicitly.
- [ ] Record exact implementation commit evidence.
- [ ] Record exact CI run/job evidence.
- [ ] Exact-head guarded merge.
- [ ] Post-merge `master` CI green.

**Validation — SWHV-007**

- [ ] Full Ruff format/lint passes.
- [ ] Full strict mypy passes.
- [ ] MCP contract check passes.
- [ ] `contract_version` history gate passes.
- [ ] `docs/TOOLS.md` drift gate passes.
- [ ] Full Docker-backed pytest + coverage passes.
- [ ] Post-merge `master` CI passes.
- [ ] TODO status is updated with exact SHA/run/job/test/coverage evidence.

---

## Definition of done

- [ ] Existing arbitrary parent directories are not chmodded by setup file writes.
- [ ] Setup-owned missing directories still receive restrictive permissions.
- [ ] Audit and state diagnostics reject unsafe symlink/special-file targets.
- [ ] Tunnel profile commands are shell-safe for paths with spaces.
- [ ] Docker default build context resolution no longer depends on `Path.cwd()`.
- [ ] Docker build actions remain explicit and non-automatic.
- [ ] Setup TUI code is split into clearer modules without behavior drift.
- [ ] Git-policy Custom mode has guided controls for every current production field or an explicit support disposition.
- [ ] Raw secrets remain absent from setup state/logs/diagnostics/tests/docs.
- [ ] Config writes remain reviewed, CAS-bound, atomic, backed up, byte-verified, and production-reloaded.
- [ ] Manual YAML and CLI workflows remain supported.
- [ ] No public MCP contract drift occurs unless explicitly versioned.
- [ ] Complete CI is green on the implementation head and merged `master`.
