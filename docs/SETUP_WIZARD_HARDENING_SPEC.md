# Setup Wizard Hardening Specification

**Status:** Proposed  
**Baseline reviewed:** `340bf1bdef4cc9d4f7aa37ec97bcb13734fcdf8d`  
**Primary prior workstream:** `docs/SETUP_WIZARD_SPEC.md` and `docs/SETUP_WIZARD_TODO.md`  
**Follow-up series:** `SWH-*`

This specification defines a focused hardening pass for the interactive setup wizard. It addresses concrete issues found during the post-merge code review of the setup wizard implementation and captures two maintainability/UX improvements that should be made without weakening the safety guarantees already delivered by the original wizard workstream.

## 1. Purpose

The setup wizard is now implemented and merged. The review found that the core architecture is sound, but there are several follow-up defects and robustness gaps that should be fixed before treating the wizard as production-polished:

1. arbitrary existing parent directories can be chmodded by the setup atomic writer;
2. diagnostics can incorrectly pass broken symlink audit targets;
3. generated tunnel profile commands are not shell-quoted;
4. Docker default-image build context resolution depends on the process current working directory;
5. the setup TUI orchestration file is too large and should be split before more behavior is added;
6. the Git policy custom editor technically works but remains JSON-heavy and should become a guided editor.

This pass must remain a hardening/refinement pass. It must not reopen the wizard architecture, change the MCP public tool surface, or weaken any repository/write safety controls.

## 2. Scope

### 2.1 In scope

- Fix filesystem permission behavior for existing, operator-selected directories.
- Make setup diagnostics use symlink-safe inspection for audit and state database targets.
- Quote generated tunnel profile commands safely for spaces and shell metacharacters in paths.
- Resolve the default Docker build context deterministically instead of relying on `Path.cwd()`.
- Split `src/dev_workspace_mcp/setup/app.py` into smaller, testable modules without behavior drift.
- Replace the Git-policy custom JSON editor with guided controls over every current `GitPolicyConfig` field.
- Add regression tests and acceptance checks for each change.
- Update documentation where behavior or operator guidance changes.
- Confirm the MCP schema and `contract_version` remain unchanged unless a gate proves otherwise.

### 2.2 Out of scope

- New MCP tools or public schema changes.
- New GitHub write capabilities.
- New secret storage or credential ingestion mechanisms.
- Replacing Textual or changing the installed console entry point.
- Changing production configuration semantics outside the minimum needed to fix the reviewed issues.
- Changing repository destructive-safety policy defaults without a separate task and acceptance evidence.

## 3. Non-negotiable invariants

The following guarantees from the original setup wizard workstream must remain true:

- `ServerConfig`, `GitPolicyConfig`, `load_config()`, and production validators remain authoritative.
- The wizard must not introduce a second weaker setup-only configuration schema.
- Raw secrets must not be persisted in setup state.
- Raw secrets must not be echoed in diagnostics, exceptions, logs, UI snapshots, docs examples, or test artifacts.
- Every persistent config mutation remains explicit, reviewable, CAS-bound, atomic, backed up when replacing an existing config, byte-verified, and production-reloaded.
- Existing manual YAML configuration and CLI workflows remain supported.
- Valid advanced config fields the wizard does not own remain preserved.
- Existing destructive-safety and repository-isolation invariants remain at least as strict as before.
- Changes must be made on `ralph/...` branches and merged only through exact-head guarded PRs.
- Complete CI must pass before merge.
- The public MCP contract is expected to remain byte-for-byte unchanged.

## 4. Issue inventory and required fixes

### 4.1 Existing parent directory permissions

#### Current problem

`atomic_write_text()` calls `ensure_private_directory(expanded.parent)`. That helper creates private setup-owned directories, but it also chmods an existing parent directory to `0700`.

That behavior is acceptable for wizard-owned private state directories, but it is too aggressive for an operator-selected config path whose parent might be intentionally shared, versioned, group-readable, or otherwise managed outside the wizard.

#### Required behavior

- The writer must distinguish between wizard-created private directories and existing operator-owned parent directories.
- Existing arbitrary parent directories must not be chmodded merely because a config file is written inside them.
- Setup-owned default state/config directories may still be created with restrictive permissions.
- If an existing parent is unsafe or unwritable, report a safe error instead of mutating its mode.
- Config files and setup state files should still use restrictive file modes where appropriate.
- Existing tests that depend on private file modes should continue to pass or be narrowed to the file target rather than parent directory mutation.

#### Acceptance criteria

- Writing to a file under an existing `0755` parent leaves the parent `0755`.
- Creating a missing setup-owned parent still uses restrictive directory permissions.
- Atomic writes still reject symlink targets and preserve recovery material on failure.
- Existing config-write semantics remain CAS-bound and production-reloaded.

### 4.2 Symlink-safe diagnostics for audit and state paths

#### Current problem

The diagnostics engine handles config path safety with low-level inspection, but audit-path checking uses `path.exists()` followed by `path.is_symlink()` and `path.is_file()`. A broken symlink can make `exists()` false and incorrectly pass as if it were an absent file with a usable parent.

State database checks are also parent-focused and should be audited for symlink edge cases.

#### Required behavior

- Audit path diagnostics must use `lstat()` or a shared symlink-safe inspection helper.
- Broken symlinks must fail or become action-required; they must never pass as safe absent targets.
- Existing symlinks, directories, device nodes, FIFOs, or other non-regular file targets must be rejected for audit output.
- State database diagnostics must reject unsafe existing targets or unsafe parent/target combinations to the extent the current production DB open path can detect them.
- Diagnostics must keep status semantics precise:
  - mandatory unsafe local target: `FAIL` or `ACTION_REQUIRED`;
  - optional skipped component: `SKIPPED`;
  - unimplemented remote verification: `UNVERIFIED`, not `PASS`;
  - unexpected exception: fail closed.
- Diagnostic remediation text must not include file contents or secret values.

#### Acceptance criteria

- Broken symlink audit target does not pass.
- Symlink audit target does not pass even if the symlink points to a regular file.
- Existing directory audit target does not pass.
- Unsafe existing state database target does not pass.
- Headless `doctor`/`setup --check` and TUI diagnostics report the same status for the same fixture.

### 4.3 Shell-safe tunnel profile command generation

#### Current problem

The tunnel profile builder constructs one command string using f-string interpolation. Paths containing spaces or shell-sensitive characters can produce a broken or ambiguous command.

#### Required behavior

- The server command written into tunnel profile plans must be generated with a shell-safe quoting mechanism such as `shlex.join()`.
- The command must preserve the current intended invocation:
  - `uv run --project <project> dev-workspace-mcp serve --config <config>`
- The generated command must work when the project path and config path contain spaces.
- The generated command must not include raw runtime secrets.
- Documentation examples should continue to show the conceptual command without implying that users should hand-copy unquoted generated internals.

#### Acceptance criteria

- A test with spaces in both project and config paths proves the command round-trips through shell parsing to the intended argv.
- A test proves no raw runtime key or other secret-like value appears in the profile plan.
- Existing `--no-browser` behavior is unchanged.

### 4.4 Deterministic Docker default build context

#### Current problem

The setup app resolves the default Docker build context from `Path.cwd() / "docker" / "default"`. That works when the operator runs the wizard from the repository root, but fails for installed usage or when launched from another directory.

#### Required behavior

- Docker default-image build context resolution must not depend on the current working directory.
- The wizard should resolve the repository-bundled default Docker build context deterministically when available.
- If the default build context is not available in an installed distribution, the UI must surface a clear action-required message rather than attempting a misleading Docker build.
- The setup step must display the exact build command/action before execution.
- The fix must not make Docker required for all setup flows; degraded-but-configurable Docker state remains supported.
- The Docker image check/build action must remain explicit and not run automatically.

#### Acceptance criteria

- Running the build-context resolver from a non-repo current working directory still finds the intended bundled context when present.
- If the context is absent, the wizard reports an actionable diagnostic and does not invoke Docker.
- Existing Docker-unavailable diagnostics remain action-required/degraded, not fatal.
- No hidden Docker mutation occurs merely by viewing the screen.

### 4.5 TUI orchestration modularity

#### Current problem

`src/dev_workspace_mcp/setup/app.py` is large and owns too many concerns: lifecycle, panel population, widget event handling, state mutation, validation, config review/write, and final diagnostics presentation. The file is covered by tests, but its size increases review and maintenance risk.

#### Required behavior

- Split the TUI implementation into smaller modules with clear boundaries.
- Preserve existing operator behavior, keyboard navigation, exit codes, state semantics, and tests.
- Keep Textual framework-specific code separated from pure domain helpers where practical.
- Avoid broad rewrites that obscure safety behavior.
- Preserve current CLI commands and public imports unless an internal-only import path is explicitly migrated with tests.

Suggested module boundaries:

```text
src/dev_workspace_mcp/setup/app.py
src/dev_workspace_mcp/setup/app_actions.py
src/dev_workspace_mcp/setup/app_lifecycle.py
src/dev_workspace_mcp/setup/app_rendering.py
src/dev_workspace_mcp/setup/app_validation.py
src/dev_workspace_mcp/setup/app_runtime_forms.py
src/dev_workspace_mcp/setup/app_git_policy_forms.py
```

The exact module names may differ, but responsibilities must be clear and tests must continue to exercise the public app behavior.

#### Acceptance criteria

- `setup/app.py` becomes materially smaller and primarily composes the app rather than owning every behavior.
- No behavior drift in existing Textual pilot tests.
- No new public MCP schema changes.
- Static typing remains strict.
- New module boundaries are documented in `docs/DEVELOPMENT.md` or equivalent developer documentation.

### 4.6 Guided Git policy custom editor

#### Current problem

The Git policy custom editor currently exposes every field and validates through `GitPolicyConfig`, but the operator experience is JSON-heavy. The original TODO intended a complete custom editor; technically complete does not mean ideal.

#### Required behavior

- Replace or supplement the raw custom JSON field with guided controls for every current `GitPolicyConfig` field.
- The editor must remain exhaustive: every field must have an explicit UI/support disposition.
- Presets must continue to materialize to actual `GitPolicyConfig` values.
- Opening the custom editor must not mutate config choices until the operator explicitly applies the changes.
- The UI must explain consequences for:
  - direct default-branch writes;
  - force push;
  - force-with-lease;
  - branch deletion;
  - published amend;
  - pull strategy;
  - remote-protection modes;
  - required checks behavior.
- Production validation must remain authoritative.
- The raw JSON escape hatch may remain behind advanced/debug affordances only if guided controls are the normal custom path.

#### Acceptance criteria

- A Textual pilot test can edit at least one boolean, one enum, and one sequence/list field through guided controls.
- Custom guided values round-trip through `GitPolicyConfig`.
- The exhaustiveness test fails if a new `GitPolicyConfig` field is added without a UI/support decision.
- Recommended and Conservative presets remain unchanged unless deliberately updated with documented rationale.

## 5. Testing strategy

The hardening work must add focused regression tests rather than relying only on broad CI.

Required test categories:

- unit tests for filesystem helpers;
- unit tests for diagnostics target inspection;
- unit tests for tunnel command generation;
- unit tests for Docker build-context resolution;
- Textual pilot tests for guided Git policy controls;
- no-regression Textual pilot tests for lifecycle/navigation;
- acceptance tests proving config writes remain safe;
- full Docker-backed pytest in CI;
- MCP contract and `docs/TOOLS.md` drift gates.

Any test using Docker must reuse the repository's existing safe Docker fixture boundary and cleanup logic.

## 6. Documentation requirements

Update docs only where behavior changes:

- `docs/CONFIGURATION.md`: note that setup writes do not chmod existing arbitrary config parent directories.
- `docs/QUICKSTART.md` or README: clarify where the default Docker image build context comes from and what to do if it is unavailable.
- `docs/CONNECTING_CHATGPT.md`: ensure tunnel profile command examples do not imply unsafe string interpolation.
- `docs/DEVELOPMENT.md`: document the split TUI module boundaries and focused test commands.

## 7. Release and migration considerations

- No configuration migration should be needed.
- No MCP `contract_version` bump is expected.
- No user-facing command removal is expected.
- Existing setup state should continue to load.
- Existing valid configs must remain valid.
- Existing manual YAML setups must continue to work.
- Any changed error wording must remain safe and non-secret.

## 8. Completion criteria

This hardening pass is complete when:

- all SWH tasks in `docs/SETUP_WIZARD_HARDENING_TODO.md` are checked with exact evidence;
- all four correctness issues have regression tests;
- maintainability/UX tasks are either completed or explicitly deferred with rationale;
- full CI passes on the implementation head;
- PR is exact-head guarded and merged;
- post-merge `master` CI passes;
- no public MCP contract drift occurs unless separately justified and versioned.
