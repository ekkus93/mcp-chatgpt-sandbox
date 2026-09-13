# Setup Wizard Hardening Specification

**Status:** Implementation in progress  
**Baseline reviewed:** `340bf1bdef4cc9d4f7aa37ec97bcb13734fcdf8d`  
**Implementation baseline:** `53f9518ba206efa2aae537983601f196da3374c4`  
**Primary prior workstream:** `docs/SETUP_WIZARD_SPEC.md` and `docs/SETUP_WIZARD_TODO.md`  
**Follow-up series:** `SWH-*`

This specification defines a focused hardening pass for the interactive setup wizard. It addresses concrete issues found during the post-merge code review of the setup wizard implementation and captures two maintainability/UX improvements that should be made without weakening the safety guarantees already delivered by the original wizard workstream.

## 1. Purpose

The setup wizard is implemented and merged. A follow-up review found that the core architecture is sound, but several robustness gaps should be fixed before treating the wizard as production-polished:

1. arbitrary existing parent directories can be chmodded by the setup atomic writer;
2. diagnostics can incorrectly pass broken symlink audit targets;
3. generated tunnel profile commands are not shell-quoted;
4. Docker default-image build context resolution depends on the process current working directory;
5. the setup TUI orchestration surface should remain split as behavior grows;
6. the Git policy custom editor technically works but should be guided rather than JSON-first.

## 2. Non-goals

This hardening pass must not change the public MCP tool/schema contract unless a gate proves that a public contract change is unavoidable. No contract change is expected.

This pass must not add a second setup-only configuration schema. `ServerConfig`, `GitPolicyConfig`, and `load_config()` remain authoritative.

This pass must not weaken the existing setup guarantees:

- no raw secrets in resumable setup state;
- no raw secrets in logs, diagnostics, docs examples, UI snapshots, or tests;
- every real config write remains reviewed, CAS-bound, atomic, backed up when replacing an existing file, byte-verified, and production-reloaded;
- manual YAML configuration and existing CLI workflows remain supported;
- destructive-safety and repository-isolation invariants remain intact.

## 3. Required fixes

### SWH-001 — Setup filesystem parent permissions

Setup writes must distinguish setup-owned directory creation from generic writable-parent validation. Creating a missing setup-owned directory may enforce owner-only permissions. Writing a config file under an already existing operator-owned parent directory must not silently chmod that parent directory.

Required behavior:

- missing setup-owned parents are created with restrictive permissions;
- existing arbitrary parents are not chmodded just because a file is written inside them;
- target file symlink refusal remains intact;
- config/state target file modes remain restrictive where appropriate;
- backup and failure-recovery behavior remains intact.

### SWH-002 — Symlink-safe diagnostics

Setup diagnostics must inspect sensitive local targets with `lstat()`-style semantics. Broken symlinks, valid symlinks, directories, devices, FIFOs, sockets, and other non-regular target shapes must not pass as safe audit/state targets.

Required behavior:

- audit target symlinks fail even when the symlink points to a regular file;
- broken audit symlinks fail;
- directory/special-file audit targets fail;
- unsafe state DB targets fail where production semantics allow;
- absent audit/state targets with writable parents retain the intended pass/action-required semantics;
- remediation text remains non-secret and does not echo file contents.

### SWH-003 — Shell-safe tunnel profile commands

Generated tunnel profile server commands must be robust when the project path or config path contains whitespace or shell-sensitive characters.

Required behavior:

- command construction uses a shell-safe mechanism such as `shlex.join()`;
- the intended argv remains `uv run --project <project> dev-workspace-mcp serve --config <config>`;
- profile plans never include raw runtime secrets;
- `--no-browser` behavior remains unchanged.

### SWH-004 — cwd-independent Docker build context

The default Docker image build action must not rely on `Path.cwd()`. The wizard can be run from an installed console script or from any working directory.

Required behavior:

- the bundled default Docker build context resolves from installed source/package location, not cwd;
- if the context is absent, the build action reports a clear action-required error before invoking Docker;
- Docker build still requires explicit operator approval;
- the reviewed build command/action preview remains visible;
- merely rendering the Docker screen performs no Docker mutation.

## 4. Maintainability and UX requirements

### SWH-005 — TUI orchestration split

The setup TUI must remain split into reviewable modules. The application shell should own lifecycle, navigation, validation gating, and state activation. Step-specific event handlers should call domain modules without owning production config semantics. Step panels should remain presentation controls. Guided form models should hold form snapshots and production validation boundaries.

The current implementation baseline already introduced `step_actions.py` and reduced `app.py` substantially. This hardening pass may add incremental support modules, but it must avoid broad rewrites that obscure safety-critical behavior.

### SWH-006 — Guided Custom Git-policy editor

Custom Git-policy editing must be guided rather than JSON-first while still validating through `GitPolicyConfig`.

Required behavior:

- every current `GitPolicyConfig` field has a guided control or explicit support disposition;
- Recommended and Conservative presets retain their tested semantics;
- opening Custom does not persist mutations until the operator validates/applies the step;
- direct default-branch writes, force push, force-with-lease, deletion, published amend, pull strategy, remote-protection modes, and required-check behavior are explained;
- sequence/list fields are edited through guided controls;
- any raw JSON escape hatch, if retained, is advanced/debug-only and not the primary Custom path.

## 5. Validation requirements

Every correctness fix must have focused regression coverage. The final implementation head must pass:

- Ruff format;
- Ruff lint;
- strict mypy;
- MCP contract export check;
- `contract_version` history gate;
- `docs/TOOLS.md` drift gate;
- full Docker-backed pytest with coverage.

The final TODO closure must record exact SHA, run ID, job ID, test totals, coverage totals, and contract-version decision. Merge must be exact-head guarded, and post-merge `master` CI must also be green.
