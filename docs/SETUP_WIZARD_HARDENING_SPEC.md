# Setup Wizard Hardening Specification

**Status:** Implementation in progress  
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

The first four items are correctness fixes. The final two are maintainability and operator-UX refinements that should be implemented in the same hardening pass because they reduce future regression risk around the same setup wizard surface.

## 2. Non-goals

This hardening pass must not expand the setup wizard into a broader product redesign. Specifically, it must not:

- change public MCP tool names, inputs, outputs, or `schemas/tools.json` unless a contract gate proves an unavoidable change;
- weaken any destructive-operation safety gate;
- weaken repository isolation;
- persist or display raw secrets;
- remove manual YAML configuration support;
- replace production configuration models with setup-specific validation;
- change GitHub branch-protection or ruleset behavior;
- silently create, delete, or modify repositories, Docker volumes, workspace containers, SSH private keys, or databases.

## 3. Preserved invariants

The following invariants from `docs/SETUP_WIZARD_SPEC.md` remain binding:

- `ServerConfig`, `GitPolicyConfig`, and `load_config()` remain authoritative.
- Setup code may collect, review, and safely persist choices, but must not invent a second weaker configuration schema.
- Config writes remain reviewed, CAS-bound, atomic, backed up when replacing an existing config, byte-verified, and production-reloaded before success is reported.
- Setup state remains non-secret and versioned.
- Diagnostics remain fail-closed for unexpected exceptions.
- Optional ChatGPT/Secure MCP Tunnel status remains separate from mandatory local machine validation.
- Manual CLI/config workflows remain supported.
- MCP contract drift remains gated by CI.

## 4. Workstream structure

The hardening pass is divided into seven implementation tasks:

- `SWH-001` — stop chmodding existing arbitrary parent directories;
- `SWH-002` — make setup diagnostics symlink-safe with `lstat()`;
- `SWH-003` — quote tunnel profile server commands safely;
- `SWH-004` — resolve Docker default build context independently of `Path.cwd()`;
- `SWH-005` — split oversized setup TUI orchestration surface;
- `SWH-006` — improve Git-policy custom editor ergonomics;
- `SWH-007` — final regression, documentation, and closure audit.

## 5. SWH-001: filesystem parent-directory hardening

### Problem

`atomic_write_text()` currently prepares the target parent by calling `ensure_private_directory(expanded.parent)`. The helper applies owner-only permissions even if the directory already existed. That behavior is safe for wizard-owned state directories, but it is too aggressive for an operator-selected config path such as `/some/shared/admin/path/config.yml`.

The setup wizard must not silently change permissions on existing directories it did not create.

### Required behavior

- Existing arbitrary parent directories must be inspected for safety but not chmodded merely because a file is written inside them.
- Missing setup-owned directories must still be created with restrictive permissions.
- File targets must still reject symlinks and non-regular existing targets.
- Config/state file modes must remain restrictive.
- Existing backup behavior must remain intact.
- Failure recovery must remain intact.

### Design direction

Split the current directory helper behavior conceptually into:

1. setup-owned private directory creation for paths the wizard owns, and
2. generic parent validation for writing a file inside an already-existing operator-owned directory.

The implementation can use one helper if it has explicit semantics, but the behavior must be clear and tested.

### Acceptance

A regression test must prove that writing a setup/config file under an existing `0755` parent leaves that parent mode unchanged. Separate tests must prove that newly created setup-owned parents are still restrictive and that symlink refusal remains intact.

## 6. SWH-002: symlink-safe diagnostics

### Problem

Some diagnostics inspect paths with `Path.exists()` before checking whether a target is a symlink or a regular file. Broken symlinks return false for `exists()`, so an unsafe configured target can be incorrectly treated as absent and pass parent-writable checks.

### Required behavior

- Diagnostics for audit files, state databases, config paths, and shared helpers must use symlink-safe inspection based on `lstat()` or an equivalent non-following operation.
- Broken symlinks must not pass diagnostics.
- Symlinks to regular files must not pass diagnostics for sensitive setup targets.
- Directories, devices, FIFOs, sockets, and other non-regular existing targets must not pass diagnostics.
- Absent targets with writable parents must preserve current intended behavior.
- Remediation text must remain non-secret and must not include file contents.

### Acceptance

Tests must cover broken symlinks, valid symlinks, directories, unsafe state DB targets, absent valid targets, and unexpected diagnostic exceptions.

## 7. SWH-003: tunnel command quoting

### Problem

The tunnel profile generator constructs `server_command` with direct f-string interpolation. Project/config paths containing spaces or shell-sensitive characters can produce a command string that does not parse back to the intended argv.

### Required behavior

- The generated command must preserve this intended argv shape:

```text
uv run --project <project> dev-workspace-mcp serve --config <config>
```

- Project paths with spaces must round-trip safely.
- Config paths with spaces must round-trip safely.
- Runtime secrets must not appear in the generated profile plan.
- `--no-browser` behavior must remain unchanged.

### Design direction

Use `shlex.join()` or an equivalent shell-safe argv-to-command mechanism. Keep the stored/reviewed profile command human-readable while making it unambiguous.

### Acceptance

A regression test must use paths containing spaces and verify `shlex.split(plan.server_command)` equals the intended argv exactly.

## 8. SWH-004: Docker build-context resolution

### Problem

The setup wizard build action currently relies on the process current working directory to find `docker/default`. Installed or non-repo-root usage can therefore attempt to build from the wrong path or fail opaquely.

### Required behavior

- Resolve the bundled default Docker build context deterministically from installed/source-tree location, not from `Path.cwd()`.
- If the bundled context is unavailable, report an actionable setup error and do not invoke Docker build.
- Preserve explicit operator approval before any build.
- Preserve command/action preview before execution.
- Preserve Docker-unavailable degraded/action-required behavior.
- Rendering the Docker screen must not mutate Docker state.

### Acceptance

Tests must prove the resolver works from a non-repo current working directory and that absent context reports an actionable error without invoking Docker.

## 9. SWH-005: setup TUI orchestration split

### Problem

`src/dev_workspace_mcp/setup/app.py` is too large and contains too many responsibilities: lifecycle, CSS, dialogs, widget population, domain translation, action handling, and validation gating. That concentration makes future setup changes harder to review safely.

### Required behavior

- Split low-risk presentation/support pieces into separate modules.
- Keep public CLI behavior unchanged.
- Preserve keyboard navigation, status rendering, exit codes, state semantics, review/write flow, and diagnostics behavior.
- Keep pure domain logic outside Textual-specific modules where practical.
- Avoid broad rewrites that obscure safety-critical behavior.
- Update `docs/DEVELOPMENT.md` with the module layout.

### Design direction

The first split should move constants, step definitions, lifecycle dialogs, CSS, and pure form-value models. Deeper controller extraction can follow after CI is green if still needed.

### Acceptance

Existing Textual pilot/lifecycle/review/diagnostic tests must continue to pass. The app module must become materially smaller and mostly compose behavior delegated to smaller support modules.

## 10. SWH-006: guided Git-policy custom editor

### Problem

The wizard technically supports Custom Git policy values, but the UI is JSON-heavy. That is error-prone for operators and inconsistent with the guided runtime editor delivered in the original setup wizard workstream.

### Required behavior

- Audit every current `GitPolicyConfig` field.
- Provide guided controls for every current field or record an explicit support disposition.
- Validate guided values through `GitPolicyConfig`.
- Preserve Recommended preset semantics unless deliberately changed with rationale.
- Preserve Conservative preset semantics as no less restrictive on mutating operations.
- Opening the Custom editor must not mutate state until the operator applies validation.
- Consequence text must cover ordinary/default-branch pushes, force push, force-with-lease, branch deletion, tag deletion, published amend, pull strategy, remote protection, required checks, and allowed URL schemes.
- Any raw JSON escape hatch, if retained, must be advanced/debug-only.

### Acceptance

Textual pilot tests must edit at least one boolean-like, one enum-like, and one sequence/list field through guided controls. Unit tests must prove guided values round-trip through `GitPolicyConfig` and that an exhaustiveness test fails when new production fields lack UI/support coverage.

## 11. SWH-007: closure audit

### Required checks

Before merge, perform a final audit covering:

- filesystem mutation boundaries;
- diagnostics fail-closed behavior;
- tunnel command generation;
- Docker build-context resolution;
- setup-state secret exposure;
- config write review/CAS/atomic/reload semantics;
- Git-policy field exhaustiveness;
- documentation accuracy;
- MCP contract drift;
- `contract_version` decision.

### CI requirements

The implementation head and post-merge `master` must pass:

- Ruff format;
- Ruff lint;
- strict mypy;
- MCP contract check;
- `contract_version` history gate;
- `docs/TOOLS.md` drift gate;
- full Docker-backed pytest + coverage.

## 12. Evidence requirements

The TODO must not be marked complete until it records:

- exact implementation head SHA;
- exact PR number;
- exact CI run and job IDs;
- pytest result and coverage totals;
- MCP contract result;
- `contract_version` decision;
- exact merge commit SHA;
- exact post-merge master CI run and job IDs.
