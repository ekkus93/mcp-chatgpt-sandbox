# Interactive Setup Wizard TODO

**Status:** Complete  
**Spec:** `docs/SETUP_WIZARD_SPEC.md`  
**Task series:** `WIZ-001` through `WIZ-020`  
**Validation series:** `WIZV-*`

This TODO implements the interactive configuration/setup wizard defined by `docs/SETUP_WIZARD_SPEC.md`. Tasks are ordered so safety primitives, typed state, and production-code reuse land before the wizard is allowed to write real operator configuration.

## Global rules

- [x] Work from an exact inspected `master` SHA for every implementation branch.
- [x] Use `ralph/...` source branches and exact-head guarded merges.
- [x] Keep `ServerConfig` / `load_config()` authoritative.
- [x] Do not create a second weaker setup-only configuration schema.
- [x] Never persist raw secrets in resumable wizard state.
- [x] Never echo raw secrets in diagnostics, exceptions, logs, or tests.
- [x] Add RED behavioral tests before each consequential write/safety behavior where feasible.
- [x] Preserve all existing destructive-safety and repository-isolation invariants.
- [x] Preserve existing manual configuration and CLI workflows.
- [x] Preserve valid advanced config fields the wizard does not own.
- [x] Make every persistent wizard mutation reviewable before commit.
- [x] Require production reload after config writes.
- [x] Run focused tests plus complete Docker-backed CI for each implementation milestone.
- [x] Record exact commit/run/job evidence as tasks close.

---

## Phase 0 — Planning and dependency boundary

### WIZ-001 — Freeze setup-wizard architecture and dependency contract

**Goal:** establish the implementation boundary before adding behavior.

- [x] Add Textual as a pinned compatible runtime dependency.
- [x] Confirm the installed console script remains `dev-workspace-mcp`.
- [x] Add `src/dev_workspace_mcp/setup/__init__.py`.
- [x] Define setup package ownership boundaries: presentation, state, filesystem, writer, diagnostics.
- [x] Document that production config models remain authoritative.
- [x] Define wizard-owned config fields explicitly.
- [x] Add an exhaustiveness test that forces an explicit decision when new top-level `ServerConfig` fields appear.
- [x] Confirm no public MCP tool/schema changes are introduced by merely adding the operator TUI.
- [x] Update dependency lockfile.

**Validation — WIZV-001**

- [x] `uv sync --group dev` succeeds from a clean checkout.
- [x] importing the setup package succeeds.
- [x] Ruff format/lint and mypy pass.
- [x] current exported MCP schema remains byte-for-byte unchanged.

---

## Phase 1 — Typed setup state and filesystem safety

### WIZ-002 — Implement typed non-secret setup state

**Goal:** make setup resumable without turning state into a credential store.

- [x] Define stable `SetupStep` identifiers.
- [x] Define `SetupStepStatus` values.
- [x] Define versioned `SetupState`.
- [x] Persist current step and non-secret operator choices only.
- [x] Validate repository IDs and canonical GitHub identities in state.
- [x] Reject unknown state keys.
- [x] Reject secret-bearing state field names outside explicitly controlled enum/status keys.
- [x] Add clone/round-trip helpers that revalidate state.
- [x] Add schema-version handling.
- [x] Fail clearly on unknown future state schema versions.
- [x] Add state-save/load functions with restrictive permissions.
- [x] Add resume rewind support for process-local secrets that were intentionally not persisted.

**Validation — WIZV-002**

- [x] round-trip valid state.
- [x] reject token/password/private-key-like fields.
- [x] reject malformed status and step values.
- [x] reject duplicate/conflicting repository state.
- [x] prove a simulated runtime secret never reaches serialized state.
- [x] prove resume rewinds to the correct prerequisite step when required process-local material is absent.

### WIZ-003 — Implement setup filesystem safety primitives

**Goal:** make later configuration writes safe before any TUI screen can invoke them.

- [x] Add sensitive-target inspection without following unsafe links.
- [x] Add bounded UTF-8 reads.
- [x] Add secure parent-directory creation helpers.
- [x] Add atomic text write helper.
- [x] Apply restrictive file modes where appropriate.
- [x] Add timestamped backup creation.
- [x] Preserve backup after post-write validation failure.
- [x] Reject unsafe symlink targets.
- [x] Define explicit failure types safe for presentation.
- [x] Ensure error text does not expose file contents or secrets.

**Validation — WIZV-003**

- [x] atomic replacement test.
- [x] backup creation test.
- [x] existing-file mode test.
- [x] new-file mode test.
- [x] symlink refusal test.
- [x] oversized-file refusal test.
- [x] invalid-UTF-8 refusal test.
- [x] interrupted/failed write leaves original usable.

---

## Phase 2 — Safe config planner/writer

### WIZ-004 — Implement reviewed config update plans

**Goal:** produce a candidate that is valid, reviewable, preserves advanced settings, and is bound to the source bytes inspected.

- [x] Parse YAML with duplicate mapping-key rejection.
- [x] Require mapping root.
- [x] Inspect existing config through production validation.
- [x] Distinguish absent, valid, and invalid existing config.
- [x] Deep-copy valid existing config before applying wizard-owned fields.
- [x] Preserve valid non-wizard-owned values.
- [x] Refuse invalid existing config unless repair is explicitly approved.
- [x] Materialize wizard choices into production field shapes.
- [x] Validate candidate with `ServerConfig`/`load_config()` semantics.
- [x] Serialize candidate.
- [x] Reparse/revalidate serialized candidate.
- [x] Compute source digest and existence identity.
- [x] Generate structured before/after summary.
- [x] Include repository additions/removals/changes in summary.
- [x] Include safety-sensitive SSH/Git policy changes in summary.

**Validation — WIZV-004**

- [x] new minimal config plan is valid.
- [x] existing advanced values are preserved.
- [x] unknown invalid fields remain rejected by production config.
- [x] duplicate YAML keys are rejected.
- [x] repair requires explicit approval.
- [x] summary accurately describes every wizard-owned difference.
- [x] candidate round-trip remains production-valid.

### WIZ-005 — Implement CAS-bound atomic config commit

**Goal:** only write exactly what the operator reviewed.

- [x] Require a reviewed `ConfigUpdatePlan`.
- [x] Re-read source immediately before write.
- [x] Compare current existence + digest with reviewed source.
- [x] Abort on concurrent/source change.
- [x] Require fresh review after mismatch.
- [x] Create timestamped backup when replacing an existing config.
- [x] Atomically write exact reviewed candidate.
- [x] Re-read and byte-verify persisted candidate.
- [x] Reload through production `load_config()`.
- [x] Report success only after production reload passes.
- [x] Return path/backup/non-secret result metadata.

**Validation — WIZV-005**

- [x] successful new-file commit.
- [x] successful existing-file update with backup.
- [x] simulated concurrent edit blocks commit.
- [x] post-write mismatch fails closed.
- [x] production-reload failure is not reported as success.
- [x] original/backup remains recoverable on failure.

---

## Phase 3 — TUI shell and lifecycle

### WIZ-006 — Add `dev-workspace-mcp setup` shell

**Goal:** establish the reusable Textual navigation/state shell before domain screens.

- [x] Add Click `setup` command.
- [x] Add `--config`.
- [x] Add `--resume`.
- [x] Add `--check` plumbing to the shared diagnostics interface.
- [x] Add `--no-browser`.
- [x] Add `--advanced`.
- [x] Implement left progress panel.
- [x] Implement title/instructions area.
- [x] Implement step-content area.
- [x] Implement persistent validation-message area.
- [x] Implement Back / Cancel / Next / Finish controls.
- [x] Disable Next when step invalid.
- [x] Add cancel confirmation.
- [x] Return documented exit codes.
- [x] Make status distinguishable without color alone.
- [x] Support terminal scrolling/narrow layouts.

**Validation — WIZV-006**

- [x] Textual pilot test for keyboard-only navigation.
- [x] Back disabled on first step.
- [x] Next disabled for invalid step.
- [x] Finish only appears on final step.
- [x] cancel decline preserves session.
- [x] cancel confirm returns cancellation code.
- [x] fatal setup error returns fatal code.

### WIZ-007 — Existing setup: Resume / Repair / Edit / Start fresh

**Goal:** make the wizard safe for real existing installations, not only clean machines.

- [x] Detect existing config and setup state safely.
- [x] Show explicit existing-installation dialog.
- [x] Implement Resume.
- [x] Implement Repair.
- [x] Implement Edit configuration.
- [x] Implement Start fresh wizard-state reset.
- [x] Ensure Start fresh does not delete DB, repositories, Docker resources, keys, or operational state.
- [x] Backup setup-state/config files before later replacement where applicable.
- [x] Handle corrupt setup-state file with repair/start-fresh path.
- [x] Preserve existing valid config until a reviewed config-write step commits a replacement.

**Validation — WIZV-007**

- [x] fresh machine enters first step.
- [x] resumable state enables Resume.
- [x] completed/non-resumable state does not falsely enable Resume.
- [x] corrupt state does not destroy config.
- [x] Start fresh leaves operational resources untouched.
- [x] Edit begins from production-valid existing values.

---

## Phase 4 — Guided core server configuration

### WIZ-008 — Local prerequisites and server-storage steps

**Goal:** validate the machine and required host paths before configuration review.

- [x] Check supported Python runtime.
- [x] Check Docker reachability.
- [x] Distinguish degraded-but-configurable Docker state from fatal setup errors.
- [x] Resolve config path.
- [x] Guide `state_db_path`.
- [x] Guide audit path.
- [x] Guide SSH keys directory.
- [x] Guide known-hosts path.
- [x] Offer documented operator defaults where appropriate.
- [x] Validate absolute-path requirements using production semantics.
- [x] Validate/create parent directories only after explicit review where mutation is required.
- [x] Invalidate downstream review if a host path changes.

**Validation — WIZV-008**

- [x] valid host-path set passes.
- [x] relative required paths fail.
- [x] unwritable parents remain action-required.
- [x] Docker unavailable is accurately reported.
- [x] no secret material is stored in state.

### WIZ-009 — Docker workspace step

**Goal:** configure the normal workspace runtime without exposing every advanced knob by default.

- [x] Guide default image selection.
- [x] Detect whether selected image exists.
- [x] Offer explicit default-image build action.
- [x] Show exact build command/action before running it.
- [x] Configure workspace path.
- [x] Configure container user mode.
- [x] Configure Git-dir visibility.
- [x] Configure idle-stop behavior.
- [x] Put nonstandard/root settings behind clear warning/advanced controls.
- [x] Validate values via production models.
- [x] Invalidate final Docker diagnostics after changes.

**Validation — WIZV-009**

- [x] project default configuration maps correctly.
- [x] missing image is action-required unless explicitly deferred according to spec.
- [x] invalid duration rejected through production parser.
- [x] invalid workspace path rejected.
- [x] advanced controls do not change values merely by being viewed.

### WIZ-010 — Repository manager

**Goal:** replace hand-authored `repositories:` YAML with a guided CRUD/verification UI.

- [x] Build repository list/table view.
- [x] Add repository flow.
- [x] Edit repository flow.
- [x] Remove repository flow with explicit confirmation.
- [x] Validate stable repository ID syntax.
- [x] Validate absolute local path.
- [x] Detect Git repository.
- [x] Collect canonical GitHub `owner/repo`.
- [x] Normalize/inspect local Git remote identity using production helpers.
- [x] Detect mismatch between configured and local remote identity.
- [x] Handle optional repository-specific SSH key name.
- [x] Reject duplicate IDs.
- [x] Define duplicate-path behavior explicitly.
- [x] Add `repository_roots` editor/discovery explanation.
- [x] Ensure verification is read-only.
- [x] Changing repository identity invalidates downstream SSH/GitHub checks.

**Validation — WIZV-010**

- [x] temporary Git repo verifies successfully.
- [x] non-Git directory rejected/action-required.
- [x] invalid `owner/repo` rejected.
- [x] remote mismatch is explicit.
- [x] duplicate IDs rejected.
- [x] CRUD state round-trips without YAML editing.

---

## Phase 5 — Credentials and Git safety

### WIZ-011 — SSH and optional GitHub API step

**Goal:** make credential isolation understandable and verifiable without placing secret values in config/state.

- [x] Present repository-scoped SSH as recommended default.
- [x] Present shared SSH scope with explicit isolation warning.
- [x] Present agent mode as recommended default.
- [x] Present file mode with private-key exposure warning.
- [x] Guide/select `keys_dir` and `known_hosts` established earlier.
- [x] Resolve per-repository key filenames where repository scope requires them.
- [x] Support shared-key filename when shared scope is selected.
- [x] Verify referenced key files without displaying key contents.
- [x] Verify known-hosts presence/format to the extent production code supports.
- [x] Support current GitHub API auth modes only.
- [x] For token mode, collect/select credential file path, never token contents in persisted state.
- [x] Preserve explicit `unverified` state when remote-protection verification cannot be completed.
- [x] Do not claim GitHub App support beyond current production config capability.

**Validation — WIZV-011**

- [x] repository/agent recommended mapping is correct.
- [x] shared/file warnings are visible and require explicit selection.
- [x] missing referenced key remains action-required when required.
- [x] secret file contents never appear in logs/state/UI snapshots.
- [x] unavailable GitHub verification never becomes success.

### WIZ-012 — Git safety presets and custom policy editor

**Goal:** make `GitPolicyConfig` understandable without creating a new runtime policy layer.

- [x] Define Recommended preset from current documented safe defaults.
- [x] Define Conservative preset.
- [x] Add Custom mode exposing every current `GitPolicyConfig` field.
- [x] Explain force push / force-with-lease / deletion / amend consequences.
- [x] Explain default-branch push/commit behavior.
- [x] Explain pull strategy.
- [x] Explain remote-protection `off` / `warn` / `deny` semantics.
- [x] Materialize presets into actual `GitPolicyConfig` values.
- [x] Ensure opening preset/custom screens does not mutate values until committed.
- [x] Add exhaustiveness test against `GitPolicyConfig` fields.

**Validation — WIZV-012**

- [x] Recommended preset exactly equals intended production values.
- [x] Conservative preset is no less restrictive on mutating operations.
- [x] Custom valid policy round-trips.
- [x] every production Git policy field receives an explicit UI/support disposition.

---

## Phase 6 — Advanced runtime configuration

### WIZ-013 — Runtime, filesystem, resources, and logging editor

**Goal:** support advanced operators without burdening the default setup path.

- [x] Keep defaults collapsed/implicit by default.
- [x] Add filesystem-limit editor.
- [x] Add execution timeout/output/retention editor.
- [x] Add CPU/memory/PID resource editor.
- [x] Add logging-level selector.
- [x] Display byte/time units unambiguously.
- [x] Validate via production Pydantic models.
- [x] Preserve untouched defaults rather than reimplementing them in setup constants where possible.
- [x] Support `--advanced` to expose these controls immediately.

**Validation — WIZV-013**

- [x] normal wizard completes without touching advanced defaults.
- [x] invalid zero/negative bounded values rejected.
- [x] valid custom limits round-trip into production config.
- [x] resource `None`/unbounded semantics preserved.

---

## Phase 7 — Optional ChatGPT connection

### WIZ-014 — Secure MCP Tunnel / ChatGPT guided step

**Goal:** guide the supported private ChatGPT connection without coupling server setup to a fragile UI or storing OpenAI secrets.

- [x] Make the entire step optional/skippable.
- [x] Detect `tunnel-client`.
- [x] Support `--no-browser`.
- [x] Provide current OpenAI documentation links as data/constants that are easy to update.
- [x] Collect non-secret tunnel identity.
- [x] Accept runtime key only through process-local/external secure source; no raw runtime key field is persisted.
- [x] Never persist runtime key in setup state.
- [x] Build/review tunnel-client profile changes.
- [x] Create/repair profile only after explicit approval.
- [x] Validate profile with `tunnel-client doctor` or equivalent supported command.
- [x] Explain the exact `dev-workspace-mcp serve --config ...` command launched by the profile.
- [x] Guide ChatGPT custom MCP app creation/refresh.
- [x] Represent ChatGPT UI completion as user-attested when not machine-verifiable.
- [x] Avoid hardcoded subscription claims that can become stale.

**Validation — WIZV-014**

- [x] optional skip does not block server setup.
- [x] no-browser mode never launches browser.
- [x] process-local secret absent after resume forces reacquisition where needed.
- [x] generated profile references correct config/server command.
- [x] user attestation is not presented as a machine PASS.

---

## Phase 8 — Review UI and final diagnostics

### WIZ-015 — Configuration review/write screen and shared final diagnostics

**Goal:** close the wizard with explicit write authorization and objective setup evidence.

#### Review/write UI

- [x] Show target config path.
- [x] Show existing/new status.
- [x] Show backup behavior.
- [x] Show structured before/after summary.
- [x] Show repository changes.
- [x] Show Docker/SSH/Git policy summary.
- [x] Show advanced runtime changes only when changed.
- [x] Require explicit `Write reviewed configuration` action.
- [x] Invalidate reviewed plan after any upstream input change.
- [x] Surface source-digest mismatch as `review again`, not an overwrite option.
- [x] Display successful backup path without leaking sensitive content.

#### Shared diagnostics engine

- [x] Define stable diagnostic codes/status/result model.
- [x] Production config reload check.
- [x] config path/mode/safety check.
- [x] state DB parent/open check.
- [x] audit path check.
- [x] Docker daemon check.
- [x] Docker default image check.
- [x] repository path/Git check per configured repository.
- [x] repository remote-identity check.
- [x] SSH key-resolution check where required.
- [x] known-hosts check.
- [x] optional GitHub API/protection check.
- [x] `create_server()` construction check without starting long-lived transport.
- [x] optional tunnel profile health check when selected.
- [x] safe remediation text for failures.
- [x] unexpected exceptions fail closed.

#### Final screen

- [x] Render PASS / FAIL / ACTION REQUIRED / SKIPPED matrix.
- [x] Separate machine verification from user attestation.
- [x] Disable Finish while mandatory checks unresolved.
- [x] Allow rerun after remediation.
- [x] Provide copyable non-secret smoke-test guidance.
- [x] Implement `setup --check` with same diagnostics engine.
- [x] Add `doctor` alias for this release.

**Validation — WIZV-015**

- [x] machine-valid setup permits Finish.
- [x] mandatory failed diagnostic blocks Finish.
- [x] optional ChatGPT skip is not a mandatory failure.
- [x] user-attested ChatGPT step stays visibly distinct from PASS.
- [x] `setup --check` returns documented exit status.
- [x] TUI and headless diagnostics use the same shared diagnostics engine/status model.

---

## Phase 9 — End-to-end acceptance and documentation

### WIZ-016 — End-to-end fresh-install acceptance

- [x] Start from no setup state and no config.
- [x] Complete wizard using documented recommended choices.
- [x] Register a real/fixture repository.
- [x] Produce config without hand-editing YAML.
- [x] Reload via production config loader.
- [x] Run final diagnostics.
- [x] Start server with resulting config.
- [x] Open a workspace through MCP/integration harness.
- [x] Verify no raw secret appears in config/state/log artifacts.

### WIZ-017 — Existing-config preservation acceptance

- [x] Begin with production-valid config containing non-default advanced fields.
- [x] Edit one wizard-owned field.
- [x] Review summary shows only intended changes.
- [x] Commit creates backup.
- [x] Non-wizard-owned fields preserved.
- [x] Final config production-valid.
- [x] Concurrent edit after review forces re-review.

### WIZ-018 — Repair and failure-path acceptance

- [x] Invalid YAML cannot be replaced without explicit repair approval.
- [x] Repair creates backup.
- [x] Unsafe symlink target is refused.
- [x] unwritable target fails safely.
- [x] simulated post-write verification failure preserves recovery material.
- [x] corrupt setup-state offers repair/start-fresh without deleting production config.
- [x] Docker unavailable remains accurately degraded/action-required.

### WIZ-019 — Documentation refresh

- [x] Make `dev-workspace-mcp setup` the recommended README first-run path.
- [x] Update `docs/QUICKSTART.md` to wizard-first flow.
- [x] Preserve manual setup as fallback/recovery path.
- [x] Update `docs/DEVELOPMENT.md` with setup/TUI test instructions.
- [x] Update `docs/CONFIGURATION.md` with wizard ownership boundaries.
- [x] Update repository-connection docs to point to wizard/repo flow.
- [x] Update ChatGPT connection docs to point to optional wizard step.
- [x] Document exit codes.
- [x] Document `--resume`, `--check`, `--no-browser`, and `--advanced`.

### WIZ-020 — Final code-review and closure audit

- [x] Audit every wizard mutation path for review-before-write.
- [x] Audit every persisted state field for secret exposure.
- [x] Audit diagnostics and errors for redaction/fail-closed behavior.
- [x] Audit ownership inventory against current `ServerConfig`.
- [x] Audit Git-policy UI against current `GitPolicyConfig`.
- [x] Audit that setup does not weaken existing destructive-safety invariants.
- [x] Audit that manual CLI/config workflows remain supported.
- [x] Full Ruff format/lint.
- [x] Full strict mypy target.
- [x] Full Docker-backed pytest + coverage.
- [x] MCP contract check.
- [x] `contract_version` decision explicitly recorded: no bump is required because the exported MCP tool/schema contract is byte-for-byte unchanged.
- [x] docs/TOOLS drift gate.
- [x] exact-head guarded merge.
- [x] post-merge `master` CI green.

### Implementation qualification evidence

Implementation head `a446d1dc0d7a1a577b3b8792828f2e51ad28eed7` was qualified by GitHub Actions CI run `34747415565`, job `103697775858`:

- Ruff format: PASS.
- Ruff lint: PASS.
- strict mypy: PASS.
- MCP contract check: PASS.
- `contract_version` history gate: PASS with no version bump required.
- `docs/TOOLS.md` drift gate: PASS.
- Docker-backed pytest + coverage: **1310 passed, 4 warnings in 638.08s**.
- Coverage: **88%**, TOTAL `8778` statements / `1093` misses.
- `uv.lock` is regenerated from `uv` and pins Textual `8.2.8` under the declared `textual>=8.2,<9` runtime range.

### Merge and post-merge evidence

- PR #56 was exact-head guarded and squash-merged from source head `8272d245ebced9c4e612b66ad30821b3ec086ac7`.
- Merge commit / first post-feature `master` head: `49d48611e392e11bceb7dd4ea81967733e86eb78`.
- Post-merge push CI run `34755280167`, job `103718493462`: PASS.
- Post-merge Docker-backed pytest + coverage: **1310 passed, 4 warnings in 657.35s**.
- Post-merge coverage: **88%**, TOTAL `8778` statements / `1093` misses.

---

## Definition of done

- [x] A new operator can generate a valid normal configuration without writing YAML manually.
- [x] Existing valid configs can be safely edited without losing advanced settings.
- [x] Resume/Repair/Edit/Start fresh are implemented and tested.
- [x] Raw secrets never enter resumable setup state.
- [x] Config writes are reviewed, CAS-bound, atomic, backed up, byte-verified, and production-reloaded.
- [x] Repository registration is usable from the TUI.
- [x] SSH isolation choices are explained and validated.
- [x] Git safety policy is selectable through safe presets or a complete custom editor.
- [x] Advanced runtime/resource settings are available through progressive disclosure.
- [x] ChatGPT tunnel setup is optional, safe, and does not hardcode fragile UI/plan assumptions.
- [x] Final diagnostics objectively gate Finish.
- [x] Headless `--check`/doctor uses the same diagnostics engine.
- [x] All existing project CI gates remain green on the merged implementation.
- [x] README/Quickstart recommend the wizard while retaining manual setup instructions.
