# Interactive Setup Wizard TODO

**Status:** Proposed / implementation not started  
**Spec:** `docs/SETUP_WIZARD_SPEC.md`  
**Task series:** `WIZ-001` through `WIZ-015`  
**Validation series:** `WIZV-*`

This TODO implements the interactive configuration/setup wizard defined by `docs/SETUP_WIZARD_SPEC.md`. Tasks are ordered so safety primitives, typed state, and production-code reuse land before the wizard is allowed to write real operator configuration.

## Global rules

- [ ] Work from an exact inspected `master` SHA for every implementation branch.
- [ ] Use `ralph/...` source branches and exact-head guarded merges.
- [ ] Keep `ServerConfig` / `load_config()` authoritative.
- [ ] Do not create a second weaker setup-only configuration schema.
- [ ] Never persist raw secrets in resumable wizard state.
- [ ] Never echo raw secrets in diagnostics, exceptions, logs, or tests.
- [ ] Add RED behavioral tests before each consequential write/safety behavior where feasible.
- [ ] Preserve all existing destructive-safety and repository-isolation invariants.
- [ ] Preserve existing manual configuration and CLI workflows.
- [ ] Preserve valid advanced config fields the wizard does not own.
- [ ] Make every persistent wizard mutation reviewable before commit.
- [ ] Require production reload after config writes.
- [ ] Run focused tests plus complete Docker-backed CI for each merged task.
- [ ] Record exact commit/run/job evidence as tasks close.

---

## Phase 0 — Planning and dependency boundary

### WIZ-001 — Freeze setup-wizard architecture and dependency contract

**Goal:** establish the implementation boundary before adding behavior.

- [ ] Add Textual as a pinned compatible runtime dependency.
- [ ] Confirm the installed console script remains `dev-workspace-mcp`.
- [ ] Add `src/dev_workspace_mcp/setup/__init__.py`.
- [ ] Define setup package ownership boundaries: presentation, state, filesystem, writer, diagnostics.
- [ ] Document that production config models remain authoritative.
- [ ] Define wizard-owned config fields explicitly.
- [ ] Add an exhaustiveness test that forces an explicit decision when new top-level `ServerConfig` fields appear.
- [ ] Confirm no public MCP tool/schema changes are introduced by merely adding the operator TUI.
- [ ] Update dependency lockfile.

**Validation — WIZV-001**

- [ ] `uv sync --group dev` succeeds from a clean checkout.
- [ ] importing the setup package succeeds.
- [ ] Ruff format/lint and mypy pass.
- [ ] current exported MCP schema remains byte-for-byte unchanged.

---

## Phase 1 — Typed setup state and filesystem safety

### WIZ-002 — Implement typed non-secret setup state

**Goal:** make setup resumable without turning state into a credential store.

- [ ] Define stable `SetupStep` identifiers.
- [ ] Define `SetupStepStatus` values.
- [ ] Define versioned `SetupState`.
- [ ] Persist current step and non-secret operator choices only.
- [ ] Validate repository IDs and canonical GitHub identities in state.
- [ ] Reject unknown state keys.
- [ ] Reject secret-bearing state field names outside explicitly controlled enum/status keys.
- [ ] Add clone/round-trip helpers that revalidate state.
- [ ] Add schema-version handling.
- [ ] Fail clearly on unknown future state schema versions.
- [ ] Add state-save/load functions with restrictive permissions.
- [ ] Add resume rewind support for process-local secrets that were intentionally not persisted.

**Validation — WIZV-002**

- [ ] round-trip valid state.
- [ ] reject token/password/private-key-like fields.
- [ ] reject malformed status and step values.
- [ ] reject duplicate/conflicting repository state.
- [ ] prove a simulated runtime secret never reaches serialized state.
- [ ] prove resume rewinds to the correct prerequisite step when required process-local material is absent.

### WIZ-003 — Implement setup filesystem safety primitives

**Goal:** make later configuration writes safe before any TUI screen can invoke them.

- [ ] Add sensitive-target inspection without following unsafe links.
- [ ] Add bounded UTF-8 reads.
- [ ] Add secure parent-directory creation helpers.
- [ ] Add atomic text write helper.
- [ ] Apply restrictive file modes where appropriate.
- [ ] Add timestamped backup creation.
- [ ] Preserve backup after post-write validation failure.
- [ ] Reject unsafe symlink targets.
- [ ] Define explicit failure types safe for presentation.
- [ ] Ensure error text does not expose file contents or secrets.

**Validation — WIZV-003**

- [ ] atomic replacement test.
- [ ] backup creation test.
- [ ] existing-file mode test.
- [ ] new-file mode test.
- [ ] symlink refusal test.
- [ ] oversized-file refusal test.
- [ ] invalid-UTF-8 refusal test.
- [ ] interrupted/failed write leaves original usable.

---

## Phase 2 — Safe config planner/writer

### WIZ-004 — Implement reviewed config update plans

**Goal:** produce a candidate that is valid, reviewable, preserves advanced settings, and is bound to the source bytes inspected.

- [ ] Parse YAML with duplicate mapping-key rejection.
- [ ] Require mapping root.
- [ ] Inspect existing config through production validation.
- [ ] Distinguish absent, valid, and invalid existing config.
- [ ] Deep-copy valid existing config before applying wizard-owned fields.
- [ ] Preserve valid non-wizard-owned values.
- [ ] Refuse invalid existing config unless repair is explicitly approved.
- [ ] Materialize wizard choices into production field shapes.
- [ ] Validate candidate with `ServerConfig`/`load_config()` semantics.
- [ ] Serialize candidate.
- [ ] Reparse/revalidate serialized candidate.
- [ ] Compute source digest and existence identity.
- [ ] Generate structured before/after summary.
- [ ] Include repository additions/removals/changes in summary.
- [ ] Include safety-sensitive SSH/Git policy changes in summary.

**Validation — WIZV-004**

- [ ] new minimal config plan is valid.
- [ ] existing advanced values are preserved.
- [ ] unknown invalid fields remain rejected by production config.
- [ ] duplicate YAML keys are rejected.
- [ ] repair requires explicit approval.
- [ ] summary accurately describes every wizard-owned difference.
- [ ] candidate round-trip remains production-valid.

### WIZ-005 — Implement CAS-bound atomic config commit

**Goal:** only write exactly what the operator reviewed.

- [ ] Require a reviewed `ConfigUpdatePlan`.
- [ ] Re-read source immediately before write.
- [ ] Compare current existence + digest with reviewed source.
- [ ] Abort on concurrent/source change.
- [ ] Require fresh review after mismatch.
- [ ] Create timestamped backup when replacing an existing config.
- [ ] Atomically write exact reviewed candidate.
- [ ] Re-read and byte-verify persisted candidate.
- [ ] Reload through production `load_config()`.
- [ ] Report success only after production reload passes.
- [ ] Return path/backup/non-secret result metadata.

**Validation — WIZV-005**

- [ ] successful new-file commit.
- [ ] successful existing-file update with backup.
- [ ] simulated concurrent edit blocks commit.
- [ ] post-write mismatch fails closed.
- [ ] production-reload failure is not reported as success.
- [ ] original/backup remains recoverable on failure.

---

## Phase 3 — TUI shell and lifecycle

### WIZ-006 — Add `dev-workspace-mcp setup` shell

**Goal:** establish the reusable Textual navigation/state shell before domain screens.

- [ ] Add Click `setup` command.
- [ ] Add `--config`.
- [ ] Add `--resume`.
- [ ] Add `--check` plumbing placeholder to shared diagnostics interface.
- [ ] Add `--no-browser`.
- [ ] Add `--advanced`.
- [ ] Implement left progress panel.
- [ ] Implement title/instructions area.
- [ ] Implement step-content area.
- [ ] Implement persistent validation-message area.
- [ ] Implement Back / Cancel / Next / Finish controls.
- [ ] Disable Next when step invalid.
- [ ] Add cancel confirmation.
- [ ] Return documented exit codes.
- [ ] Make status distinguishable without color alone.
- [ ] Support terminal scrolling/narrow layouts.

**Validation — WIZV-006**

- [ ] Textual pilot test for keyboard-only navigation.
- [ ] Back disabled on first step.
- [ ] Next disabled for invalid step.
- [ ] Finish only appears on final step.
- [ ] cancel decline preserves session.
- [ ] cancel confirm returns cancellation code.
- [ ] fatal setup error returns fatal code.

### WIZ-007 — Existing setup: Resume / Repair / Edit / Start fresh

**Goal:** make the wizard safe for real existing installations, not only clean machines.

- [ ] Detect existing config and setup state safely.
- [ ] Show explicit existing-installation dialog.
- [ ] Implement Resume.
- [ ] Implement Repair.
- [ ] Implement Edit configuration.
- [ ] Implement Start fresh wizard-state reset.
- [ ] Ensure Start fresh does not delete DB, repositories, Docker resources, keys, or operational state.
- [ ] Backup setup-state/config files before later replacement where applicable.
- [ ] Handle corrupt setup-state file with repair/start-fresh path.
- [ ] Preserve existing valid config until a reviewed config-write step commits a replacement.

**Validation — WIZV-007**

- [ ] fresh machine enters first step.
- [ ] resumable state enables Resume.
- [ ] completed/non-resumable state does not falsely enable Resume.
- [ ] corrupt state does not destroy config.
- [ ] Start fresh leaves operational resources untouched.
- [ ] Edit begins from production-valid existing values.

---

## Phase 4 — Guided core server configuration

### WIZ-008 — Local prerequisites and server-storage steps

**Goal:** validate the machine and required host paths before configuration review.

- [ ] Check supported Python runtime.
- [ ] Check Docker reachability.
- [ ] Distinguish degraded-but-configurable Docker state from fatal setup errors.
- [ ] Resolve config path.
- [ ] Guide `state_db_path`.
- [ ] Guide audit path.
- [ ] Guide SSH keys directory.
- [ ] Guide known-hosts path.
- [ ] Offer documented operator defaults where appropriate.
- [ ] Validate absolute-path requirements using production semantics.
- [ ] Validate/create parent directories only after explicit review where mutation is required.
- [ ] Invalidate downstream review if a host path changes.

**Validation — WIZV-008**

- [ ] valid host-path set passes.
- [ ] relative required paths fail.
- [ ] unwritable parents remain action-required.
- [ ] Docker unavailable is accurately reported.
- [ ] no secret material is stored in state.

### WIZ-009 — Docker workspace step

**Goal:** configure the normal workspace runtime without exposing every advanced knob by default.

- [ ] Guide default image selection.
- [ ] Detect whether selected image exists.
- [ ] Offer explicit default-image build action.
- [ ] Show exact build command/action before running it.
- [ ] Configure workspace path.
- [ ] Configure container user mode.
- [ ] Configure Git-dir visibility.
- [ ] Configure idle-stop behavior.
- [ ] Put nonstandard/root settings behind clear warning/advanced controls.
- [ ] Validate values via production models.
- [ ] Invalidate final Docker diagnostics after changes.

**Validation — WIZV-009**

- [ ] project default configuration maps correctly.
- [ ] missing image is action-required unless explicitly deferred according to spec.
- [ ] invalid duration rejected through production parser.
- [ ] invalid workspace path rejected.
- [ ] advanced controls do not change values merely by being viewed.

### WIZ-010 — Repository manager

**Goal:** replace hand-authored `repositories:` YAML with a guided CRUD/verification UI.

- [ ] Build repository list/table view.
- [ ] Add repository flow.
- [ ] Edit repository flow.
- [ ] Remove repository flow with explicit confirmation.
- [ ] Validate stable repository ID syntax.
- [ ] Validate absolute local path.
- [ ] Detect Git repository.
- [ ] Collect canonical GitHub `owner/repo`.
- [ ] Normalize/inspect local Git remote identity using production helpers.
- [ ] Detect mismatch between configured and local remote identity.
- [ ] Handle optional repository-specific SSH key name.
- [ ] Reject duplicate IDs.
- [ ] Define duplicate-path behavior explicitly.
- [ ] Add `repository_roots` editor/discovery explanation.
- [ ] Ensure verification is read-only.
- [ ] Changing repository identity invalidates downstream SSH/GitHub checks.

**Validation — WIZV-010**

- [ ] temporary Git repo verifies successfully.
- [ ] non-Git directory rejected/action-required.
- [ ] invalid `owner/repo` rejected.
- [ ] remote mismatch is explicit.
- [ ] duplicate IDs rejected.
- [ ] CRUD state round-trips without YAML editing.

---

## Phase 5 — Credentials and Git safety

### WIZ-011 — SSH and optional GitHub API step

**Goal:** make credential isolation understandable and verifiable without placing secret values in config/state.

- [ ] Present repository-scoped SSH as recommended default.
- [ ] Present shared SSH scope with explicit isolation warning.
- [ ] Present agent mode as recommended default.
- [ ] Present file mode with private-key exposure warning.
- [ ] Guide/select `keys_dir` and `known_hosts` established earlier.
- [ ] Resolve per-repository key filenames where repository scope requires them.
- [ ] Support shared-key filename when shared scope is selected.
- [ ] Verify referenced key files without displaying key contents.
- [ ] Verify known-hosts presence/format to the extent production code supports.
- [ ] Support current GitHub API auth modes only.
- [ ] For token mode, collect/select credential file path, never token contents in persisted state.
- [ ] Preserve explicit `unverified` state when remote-protection verification cannot be completed.
- [ ] Do not claim GitHub App support beyond current production config capability.

**Validation — WIZV-011**

- [ ] repository/agent recommended mapping is correct.
- [ ] shared/file warnings are visible and require explicit selection.
- [ ] missing referenced key remains action-required when required.
- [ ] secret file contents never appear in logs/state/UI snapshots.
- [ ] unavailable GitHub verification never becomes success.

### WIZ-012 — Git safety presets and custom policy editor

**Goal:** make `GitPolicyConfig` understandable without creating a new runtime policy layer.

- [ ] Define Recommended preset from current documented safe defaults.
- [ ] Define Conservative preset.
- [ ] Add Custom mode exposing every current `GitPolicyConfig` field.
- [ ] Explain force push / force-with-lease / deletion / amend consequences.
- [ ] Explain default-branch push/commit behavior.
- [ ] Explain pull strategy.
- [ ] Explain remote-protection `off` / `warn` / `deny` semantics.
- [ ] Materialize presets into actual `GitPolicyConfig` values.
- [ ] Ensure opening preset/custom screens does not mutate values until committed.
- [ ] Add exhaustiveness test against `GitPolicyConfig` fields.

**Validation — WIZV-012**

- [ ] Recommended preset exactly equals intended production values.
- [ ] Conservative preset is no less restrictive on mutating operations.
- [ ] Custom valid policy round-trips.
- [ ] every production Git policy field receives an explicit UI/support disposition.

---

## Phase 6 — Advanced runtime configuration

### WIZ-013 — Runtime, filesystem, resources, and logging editor

**Goal:** support advanced operators without burdening the default setup path.

- [ ] Keep defaults collapsed/implicit by default.
- [ ] Add filesystem-limit editor.
- [ ] Add execution timeout/output/retention editor.
- [ ] Add CPU/memory/PID resource editor.
- [ ] Add logging-level selector.
- [ ] Display byte/time units unambiguously.
- [ ] Validate via production Pydantic models.
- [ ] Preserve untouched defaults rather than reimplementing them in setup constants where possible.
- [ ] Support `--advanced` to expose these controls immediately.

**Validation — WIZV-013**

- [ ] normal wizard completes without touching advanced defaults.
- [ ] invalid zero/negative bounded values rejected.
- [ ] valid custom limits round-trip into production config.
- [ ] resource `None`/unbounded semantics preserved.

---

## Phase 7 — Optional ChatGPT connection

### WIZ-014 — Secure MCP Tunnel / ChatGPT guided step

**Goal:** guide the supported private ChatGPT connection without coupling server setup to a fragile UI or storing OpenAI secrets.

- [ ] Make the entire step optional/skippable.
- [ ] Detect `tunnel-client`.
- [ ] Support `--no-browser`.
- [ ] Provide current OpenAI documentation links as data/constants that are easy to update.
- [ ] Collect non-secret tunnel identity.
- [ ] Accept runtime key only through masked process-local input or external secure source.
- [ ] Never persist runtime key in setup state.
- [ ] Build/review tunnel-client profile changes.
- [ ] Create/repair profile only after explicit approval.
- [ ] Validate profile with `tunnel-client doctor` or equivalent supported command.
- [ ] Explain the exact `dev-workspace-mcp serve --config ...` command launched by the profile.
- [ ] Guide ChatGPT custom MCP app creation/refresh.
- [ ] Represent ChatGPT UI completion as user-attested when not machine-verifiable.
- [ ] Avoid hardcoded subscription claims that can become stale.

**Validation — WIZV-014**

- [ ] optional skip does not block server setup.
- [ ] no-browser mode never launches browser.
- [ ] process-local secret absent after resume forces reacquisition where needed.
- [ ] generated profile references correct config/server command.
- [ ] user attestation is not presented as a machine PASS.

---

## Phase 8 — Review UI and final diagnostics

### WIZ-015 — Configuration review/write screen and shared final diagnostics

**Goal:** close the wizard with explicit write authorization and objective setup evidence.

#### Review/write UI

- [ ] Show target config path.
- [ ] Show existing/new status.
- [ ] Show backup behavior.
- [ ] Show structured before/after summary.
- [ ] Show repository changes.
- [ ] Show Docker/SSH/Git policy summary.
- [ ] Show advanced runtime changes only when changed.
- [ ] Require explicit `Write reviewed configuration` action.
- [ ] Invalidate reviewed plan after any upstream input change.
- [ ] Surface source-digest mismatch as `review again`, not an overwrite option.
- [ ] Display successful backup path without leaking sensitive content.

#### Shared diagnostics engine

- [ ] Define stable diagnostic codes/status/result model.
- [ ] Production config reload check.
- [ ] config path/mode/safety check.
- [ ] state DB parent/open check.
- [ ] audit path check.
- [ ] Docker daemon check.
- [ ] Docker default image check.
- [ ] repository path/Git check per configured repository.
- [ ] repository remote-identity check.
- [ ] SSH key-resolution check where required.
- [ ] known-hosts check.
- [ ] optional GitHub API/protection check.
- [ ] `create_server()` construction check without starting long-lived transport.
- [ ] optional tunnel profile health check when selected.
- [ ] safe remediation text for failures.
- [ ] unexpected exceptions fail closed.

#### Final screen

- [ ] Render PASS / FAIL / ACTION REQUIRED / SKIPPED matrix.
- [ ] Separate machine verification from user attestation.
- [ ] Disable Finish while mandatory checks unresolved.
- [ ] Allow rerun after remediation.
- [ ] Provide copyable non-secret smoke-test guidance.
- [ ] Implement `setup --check` with same diagnostics engine.
- [ ] Add `doctor` alias if accepted for this release.

**Validation — WIZV-015**

- [ ] machine-valid setup permits Finish.
- [ ] mandatory failed diagnostic blocks Finish.
- [ ] optional ChatGPT skip is not a mandatory failure.
- [ ] user-attested ChatGPT step stays visibly distinct from PASS.
- [ ] `setup --check` returns documented exit status.
- [ ] TUI and headless diagnostics produce the same statuses for the same fixture.

---

## Phase 9 — End-to-end acceptance and documentation

### WIZ-016 — End-to-end fresh-install acceptance

- [ ] Start from no setup state and no config.
- [ ] Complete wizard using documented recommended choices.
- [ ] Register a real/fixture repository.
- [ ] Produce config without hand-editing YAML.
- [ ] Reload via production config loader.
- [ ] Run final diagnostics.
- [ ] Start server with resulting config.
- [ ] Open a workspace through MCP/integration harness.
- [ ] Verify no raw secret appears in config/state/log artifacts.

### WIZ-017 — Existing-config preservation acceptance

- [ ] Begin with production-valid config containing non-default advanced fields.
- [ ] Edit one wizard-owned field.
- [ ] Review summary shows only intended changes.
- [ ] Commit creates backup.
- [ ] Non-wizard-owned fields preserved.
- [ ] Final config production-valid.
- [ ] Concurrent edit after review forces re-review.

### WIZ-018 — Repair and failure-path acceptance

- [ ] Invalid YAML cannot be replaced without explicit repair approval.
- [ ] Repair creates backup.
- [ ] Unsafe symlink target is refused.
- [ ] unwritable target fails safely.
- [ ] simulated post-write verification failure preserves recovery material.
- [ ] corrupt setup-state offers repair/start-fresh without deleting production config.
- [ ] Docker unavailable remains accurately degraded/action-required.

### WIZ-019 — Documentation refresh

- [ ] Make `dev-workspace-mcp setup` the recommended README first-run path.
- [ ] Update `docs/QUICKSTART.md` to wizard-first flow.
- [ ] Preserve manual setup as fallback/recovery path.
- [ ] Update `docs/DEVELOPMENT.md` with setup/TUI test instructions.
- [ ] Update `docs/CONFIGURATION.md` with wizard ownership boundaries.
- [ ] Update repository-connection docs to point to wizard/repo flow.
- [ ] Update ChatGPT connection docs to point to optional wizard step.
- [ ] Document exit codes.
- [ ] Document `--resume`, `--check`, `--no-browser`, and `--advanced`.

### WIZ-020 — Final code-review and closure audit

- [ ] Audit every wizard mutation path for review-before-write.
- [ ] Audit every persisted state field for secret exposure.
- [ ] Audit diagnostics and errors for redaction/fail-closed behavior.
- [ ] Audit ownership inventory against current `ServerConfig`.
- [ ] Audit Git-policy UI against current `GitPolicyConfig`.
- [ ] Audit that setup does not weaken existing destructive-safety invariants.
- [ ] Audit that manual CLI/config workflows remain supported.
- [ ] Full Ruff format/lint.
- [ ] Full strict mypy target.
- [ ] Full Docker-backed pytest + coverage.
- [ ] MCP contract check.
- [ ] `contract_version` decision explicitly recorded.
- [ ] docs/TOOLS drift gate.
- [ ] exact-head guarded merge.
- [ ] post-merge `master` CI green.

---

## Definition of done

- [ ] A new operator can generate a valid normal configuration without writing YAML manually.
- [ ] Existing valid configs can be safely edited without losing advanced settings.
- [ ] Resume/Repair/Edit/Start fresh are implemented and tested.
- [ ] Raw secrets never enter resumable setup state.
- [ ] Config writes are reviewed, CAS-bound, atomic, backed up, byte-verified, and production-reloaded.
- [ ] Repository registration is usable from the TUI.
- [ ] SSH isolation choices are explained and validated.
- [ ] Git safety policy is selectable through safe presets or a complete custom editor.
- [ ] Advanced runtime/resource settings are available through progressive disclosure.
- [ ] ChatGPT tunnel setup is optional, safe, and does not hardcode fragile UI/plan assumptions.
- [ ] Final diagnostics objectively gate Finish.
- [ ] Headless `--check`/doctor uses the same diagnostics engine.
- [ ] All existing project CI gates remain green.
- [ ] README/Quickstart recommend the wizard while retaining manual setup instructions.
