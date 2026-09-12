# Interactive Setup Wizard Specification

**Status:** Proposed  
**Project:** `mcp-chatgpt-sandbox` / `dev-workspace-mcp`  
**Primary command:** `dev-workspace-mcp setup`  
**Companion task plan:** `docs/SETUP_WIZARD_TODO.md`

## 1. Purpose

`mcp-chatgpt-sandbox` has grown beyond a configuration that a new operator should be expected to assemble safely by hand. The server configuration now spans repository registration, Docker workspace behavior, SSH isolation, optional GitHub API integration, filesystem and execution limits, Git safety policy, resource limits, state storage, audit logging, and operational logging. A working ChatGPT deployment can additionally require the OpenAI Secure MCP Tunnel and a custom MCP app.

The project SHALL provide an interactive terminal user interface that guides an operator through setup, validates each choice with production code wherever possible, reviews every persistent change before writing it, preserves unrelated valid configuration, and ends with a machine-verifiable diagnostics report.

The intended first-run experience is:

```bash
uv sync --group dev
uv run dev-workspace-mcp setup
```

A user who follows the wizard SHALL NOT need to hand-author YAML for the normal supported deployment path.

## 2. Design inspiration

The wizard is intentionally informed by the setup application in `ekkus93/chatgpt_ralph_loop_tools`, especially these patterns:

- Textual-based full-screen TUI.
- Stable typed setup-step identifiers and per-step status.
- A resumable non-secret setup state machine.
- `Resume`, `Repair`, and `Start fresh` handling for an existing installation.
- Secrets excluded from resumable state.
- `Next` disabled until the current step satisfies its acceptance predicate.
- Explicit confirmations for consequential actions.
- Review-before-write for configuration changes.
- Source-digest binding so a reviewed candidate cannot overwrite a file changed after review.
- Atomic writes with restrictive permissions and timestamped backups.
- Reload of the written configuration through the production parser.
- A final diagnostics matrix that does not convert unresolved checks into success.
- User attestation only for external UI actions that the program cannot independently verify.

This project SHALL reuse those architectural ideas, not copy Ralph Bridge's domain model. `mcp-chatgpt-sandbox` has different configuration semantics and SHALL keep `ServerConfig`, `load_config()`, repository validation, Docker behavior, SSH behavior, and Git policy as the authorities.

## 3. Goals

The wizard SHALL:

1. Explain what each major configuration choice means in operator terms rather than exposing raw YAML first.
2. Produce or safely update a valid server configuration.
3. Preserve valid settings that the wizard does not manage.
4. Never silently weaken an existing safety policy.
5. Never persist raw secrets in resumable wizard state.
6. Validate paths, Docker availability, repository identity, SSH material references, and production configuration semantics before declaring setup complete.
7. Support an initial guided path and an advanced path without forcing every operator to tune every low-level limit.
8. Support safe maintenance of an existing installation.
9. Provide headless diagnostics through the same validation engine used by the TUI.
10. Make it obvious what is machine-verified, what is user-attested, and what is optional.

## 4. Non-goals

The first implementation SHALL NOT:

- invent a second configuration schema separate from `ServerConfig`;
- bypass `load_config()` validation;
- store private SSH key contents, GitHub tokens, OpenAI runtime keys, passwords, or equivalent secret material in setup state;
- automatically grant GitHub repository access or change repository settings without an explicit operator action;
- silently modify remote branch-protection settings;
- silently enable destructive Git policies;
- add a public unauthenticated MCP listener;
- implement a general-purpose YAML editor;
- own or delete existing Docker volumes, workspaces, repositories, SSH keys, or state databases merely because `Start fresh` was selected;
- make optional ChatGPT/tunnel setup a prerequisite for using the server with a local stdio MCP client.

## 5. User-facing command surface

### 5.1 Primary setup command

```text
dev-workspace-mcp setup
```

Launches the interactive Textual wizard.

Recommended options:

```text
--config PATH      Target server config; use the project's documented operator default when omitted.
--resume           Resume incomplete non-secret wizard state.
--check            Run the shared diagnostics engine without opening the TUI.
--no-browser       Never open external URLs; display/copy them instead.
--advanced         Start with advanced configuration controls visible.
```

`--resume` SHALL fail clearly when no valid incomplete setup state exists. `--check` SHALL be non-interactive and suitable for automation.

### 5.2 Future convenience commands

The component architecture SHOULD permit these later without duplicating logic:

```text
dev-workspace-mcp config
dev-workspace-mcp repo add
dev-workspace-mcp repo edit
dev-workspace-mcp repo remove
dev-workspace-mcp doctor
```

`doctor` SHOULD be an alias or thin wrapper over the same diagnostics engine used by `setup --check` and the final wizard step.

## 6. TUI framework and dependency

The implementation SHOULD use Textual, matching the proven approach in Ralph Bridge.

Textual SHALL be a normal runtime dependency if `dev-workspace-mcp setup` is part of the installed CLI. The project SHALL pin a compatible major version range in `pyproject.toml` and SHALL cover the setup package with normal Ruff, mypy, and pytest gates.

The TUI SHALL remain keyboard-operable and usable in an ordinary terminal without mouse input.

## 7. Wizard shell

The default layout SHALL have:

- a left progress/status panel;
- a main instructions/content area;
- a validation/status message area;
- `Back`, `Cancel`, and `Next`/`Finish` controls;
- a footer containing relevant key bindings.

Representative progress display:

```text
> [3/10] Docker workspace
    ACTION REQUIRED

  [4/10] Repository access
    NOT STARTED

  [5/10] SSH and GitHub
    NOT STARTED
```

The active step SHALL be visually distinct. Completed steps, failed steps, optional skipped steps, and action-required steps SHALL be distinguishable without relying solely on color.

## 8. Setup state machine

### 8.1 Stable step identifiers

The setup package SHALL define stable enum-like step identifiers. The initial logical flow is:

1. `local_prerequisites`
2. `server_storage`
3. `docker_workspace`
4. `repository_access`
5. `ssh_and_github`
6. `git_safety_policy`
7. `runtime_and_resources`
8. `chatgpt_connection`
9. `configuration_review`
10. `final_validation`

A later implementation MAY split a logical step into several screens without changing the externally persisted state format carelessly; migrations SHALL be explicit.

### 8.2 Step statuses

At minimum:

- `not_started`
- `action_required`
- `waiting_for_user`
- `validating`
- `complete`
- `failed`
- `skipped_optional`

### 8.3 Progression rule

`Next` SHALL remain disabled unless the active step is currently valid or is explicitly optional and deliberately skipped.

Changing an upstream value SHALL invalidate dependent downstream validation. Examples:

- changing the Docker image invalidates Docker-image validation and final diagnostics;
- changing repository identity invalidates its SSH/GitHub verification;
- changing SSH scope invalidates repository credential resolution;
- changing Git safety preset invalidates configuration review;
- changing any wizard-managed config value invalidates the reviewed configuration candidate.

## 9. Secrets and resumable state

### 9.1 Non-secret state only

The persisted setup-state file MAY contain:

- current step;
- step statuses;
- selected config path;
- non-secret paths;
- repository IDs and canonical `owner/repo` identities;
- Docker image name;
- policy selections;
- operator choices that are safe to persist.

It SHALL NOT contain raw:

- SSH private key contents;
- GitHub API tokens;
- OpenAI runtime keys;
- passwords;
- bearer credentials;
- environment dumps;
- arbitrary secret-bearing values.

The state serializer SHALL reject secret-like field names outside explicitly schema-controlled step/status identifiers.

### 9.2 Resume behavior

If a required secret was process-local and intentionally not persisted, resume SHALL rewind to the earliest step necessary to reacquire it instead of pretending the step remains complete.

## 10. Existing installation behavior

When setup material already exists, the wizard SHALL present explicit choices:

### Resume

Continue from a valid incomplete setup state.

### Repair

Revalidate the existing config and setup files, preserve valid material, and guide the user only through unresolved or invalid pieces.

### Edit configuration

Start from the currently valid production configuration and allow changes while preserving values outside wizard ownership.

### Start fresh

Reset wizard progress, but SHALL NOT blindly delete operational resources or credentials. Existing files that the wizard later replaces SHALL receive timestamped backups before replacement. Repository contents, Docker volumes, state DB contents, SSH keys, and existing workspace containers SHALL remain untouched unless a separate explicit operation governs them.

### Cancel

Exit without deleting existing configuration. Valid non-secret progress MAY remain resumable.

## 11. Guided setup flow

## 11.1 Step 1 — Local prerequisites

The wizard SHALL check and display at least:

- supported Python version;
- `uv` availability when running from source, where meaningful;
- Docker CLI/daemon reachability;
- basic filesystem access to the chosen config/state locations;
- presence of `tunnel-client` only when ChatGPT connection setup is selected.

Docker unavailability SHALL be reported distinctly from configuration invalidity. Because the daemon itself can start degraded, the wizard MAY allow configuration work to continue, but final diagnostics SHALL clearly report Docker-dependent checks as unresolved.

The screen SHALL explain the intended connection path and which external systems may be touched.

## 11.2 Step 2 — Server storage

The wizard SHALL guide selection of:

- config file location;
- `state_db_path`;
- audit log path;
- SSH keys directory;
- known-hosts file location.

The wizard SHOULD offer documented operator defaults but SHALL show the exact resolved paths before writing.

It SHALL validate:

- required paths are absolute where the production schema requires absolute paths;
- parent directories are creatable or writable;
- config/state/audit targets do not resolve through unsafe symlink patterns defined by the setup filesystem policy;
- restrictive file/directory permissions can be applied where appropriate.

Directory creation SHALL require a clear review/confirmation if it changes the filesystem.

## 11.3 Step 3 — Docker workspace

The guided screen SHALL cover:

- default workspace image;
- whether the image currently exists locally;
- building the project's default image when selected;
- workspace mount path, with `/workspace` as the normal default;
- container user mode;
- Git directory visibility mode;
- idle-stop policy.

The default path SHOULD optimize for the project's documented recommended configuration, not expose every field immediately.

An advanced subsection MAY expose:

- root container user selection, accompanied by the existing production safety constraints;
- custom workspace path;
- custom idle-stop duration.

The wizard SHALL validate the resulting values through the same model semantics used by production configuration.

## 11.4 Step 4 — Repository access

The wizard SHALL provide an interactive repository manager rather than a raw YAML list.

Each explicit repository entry SHALL collect and validate:

- stable repository ID;
- absolute local repository path;
- GitHub `owner/repo` identity;
- optional repository-specific SSH key filename when required by SSH scope.

The repository manager SHOULD support:

- Add;
- Edit;
- Remove;
- Verify;
- inspect detected Git remote identity;
- detect duplicate IDs and duplicate/conflicting repository records.

The screen MAY also configure `repository_roots` for discovery. The UI SHALL explain the difference between explicit registration and root-based discovery.

Verification SHOULD confirm, where possible:

- local path exists and is a Git repository;
- repository stays inside any applicable authorized root constraint;
- configured remote identity matches the local repository's GitHub remote normalization rules;
- duplicate repository IDs are rejected.

No repository content SHALL be modified by verification.

## 11.5 Step 5 — SSH and GitHub

This step SHALL present security choices in operator language first.

### SSH isolation choice

Recommended default:

- dedicated key per repository.

Alternative:

- shared key.

The wizard SHALL explain that shared scope trades away per-repository credential isolation.

### Container credential exposure choice

Recommended default:

- SSH agent mode, where private key material does not enter the container.

Alternative:

- file mode, with an explicit warning that container code can read mounted key material.

### Known hosts

The wizard SHALL verify the configured known-hosts file exists or guide creation/population without relying on trust-on-first-use. If it offers a GitHub host-key convenience action, the source and fingerprint verification expectations SHALL be documented and reviewable.

### GitHub API integration

The wizard SHALL support the existing `none`, `token`, and `app` modes without inventing secret-bearing config fields. It SHALL preserve the current path-reference model for credentials.

For token mode, the UI SHALL collect or select only the credential file path, not persist the token in wizard state.

For app mode, any app credential paths/identifiers supported by current production configuration at implementation time SHALL be handled through production models. If the current server configuration does not yet fully support a desired App flow, the wizard SHALL NOT imply that it does.

### Verification

Where credentials are available, the wizard SHOULD perform bounded checks that prove the selected repository can authenticate or that GitHub protection metadata can be queried. Failed optional API verification SHALL remain visibly `unverified`; it SHALL NOT be converted into protected/allowed success.

## 11.6 Step 6 — Git safety policy

The normal flow SHOULD expose presets rather than every enum immediately.

### Recommended preset

Should map to the project's current safe defaults, including:

- ordinary push allowed;
- default-branch push limited to the configured safe default behavior;
- force push denied;
- force-with-lease denied unless the current default changes by explicit design;
- remote branch deletion requiring approval;
- remote tag deletion denied;
- amending published commits denied;
- fast-forward-only pull strategy;
- remote-protection behavior matching the current documented default.

### Conservative preset

SHOULD tighten mutating operations further.

### Custom

Exposes every current `GitPolicyConfig` field with concise consequences and validation.

Presets SHALL materialize into the existing `GitPolicyConfig`; they SHALL NOT become a parallel runtime policy layer.

## 11.7 Step 7 — Runtime and resources

The default view SHOULD keep existing sane defaults and present only commonly useful decisions.

An `Advanced` section SHALL permit editing the current production fields for:

- filesystem byte/result limits;
- synchronous and background execution timeouts;
- output-capture and response limits;
- job retention;
- CPU, memory, and PID limits;
- operational logging level.

The UI SHALL display units clearly and SHALL validate values through production Pydantic models before review.

The wizard SHALL NOT require a first-time operator to understand every low-level limit to complete setup.

## 11.8 Step 8 — ChatGPT connection

This step SHALL be optional because local stdio clients can use the server without ChatGPT.

When selected, it SHALL guide the operator through the project's supported Secure MCP Tunnel flow:

- verify `tunnel-client` availability;
- point to current OpenAI documentation for tunnel creation and workspace eligibility;
- collect non-secret tunnel identity needed to construct a profile;
- accept runtime secrets only in process-local masked inputs or through external secure storage, never in resumable wizard state;
- create or repair the tunnel profile only after review;
- explain the command the tunnel will launch;
- guide creation or refresh of the ChatGPT custom MCP app;
- mark ChatGPT UI completion as `USER-ATTESTED` when the wizard cannot inspect it independently.

`--no-browser` SHALL suppress all browser launches and provide copyable URLs/instructions instead.

The documentation source of truth for plan/UI availability SHALL remain current OpenAI documentation; the wizard SHALL avoid hardcoding fragile subscription claims unless they are machine-maintained and tested.

## 11.9 Step 9 — Configuration review

This is a mandatory write gate.

The wizard SHALL construct a complete candidate configuration from:

1. a new empty config, or
2. a deep copy of an existing valid config.

It SHALL modify only fields it owns and SHALL preserve unrelated valid advanced settings from an existing config.

The review screen SHALL show:

- target config path;
- whether a file exists;
- whether a timestamped backup will be created;
- a structured before/after summary of wizard-managed fields;
- repository additions/removals/changes;
- selected SSH/GitHub modes;
- Git safety policy summary;
- Docker/runtime/resource changes;
- any optional ChatGPT/tunnel artifacts that will be written separately.

The screen SHALL require an explicit `Write reviewed configuration` action.

## 12. Safe configuration writer

The setup package SHALL provide a fail-closed config writer with semantics equivalent to these steps:

1. Safely inspect the target without following prohibited links.
2. Bound the maximum readable config size.
3. Decode as UTF-8.
4. Parse YAML while rejecting duplicate mapping keys.
5. Require a mapping root.
6. Validate an existing config with production `load_config()` / production models.
7. If valid, deep-copy and preserve fields outside wizard ownership.
8. If invalid, refuse replacement unless the operator explicitly approves repair.
9. Materialize wizard-managed fields.
10. Validate the candidate through production configuration models.
11. Serialize deterministically enough for review.
12. Parse and validate the serialized candidate again.
13. Record a digest of the exact source bytes reviewed.
14. Immediately before commit, re-read the source and compare existence + digest.
15. If the source changed, abort and require a fresh review.
16. Atomically write the exact reviewed candidate with restrictive permissions.
17. If replacing an existing file, create a timestamped backup first.
18. Re-read the persisted file and verify it exactly matches the reviewed candidate.
19. Reload it through production `load_config()`.
20. Report success only after production reload succeeds.

The wizard SHALL NOT hand-maintain a weaker shadow validator.

## 13. Setup filesystem safety

A reusable setup filesystem module SHALL govern wizard writes.

It SHALL:

- reject unsafe sensitive targets;
- avoid following unexpected symlinks for sensitive files;
- create parent directories deliberately, not incidentally;
- use temporary files in the destination filesystem when practical;
- `fsync`/replace according to the project's chosen atomic-write policy;
- set restrictive modes for config/state/credential-reference files where appropriate;
- create timestamped backups before destructive replacement;
- never silently delete a backup after a failed post-write verification.

## 14. Final validation and `doctor`

The final screen SHALL use a shared diagnostics engine, also callable non-interactively.

The matrix SHOULD include at least:

- production config parse/reload;
- config file safety/permissions checks;
- state DB parent writability and DB openability;
- audit-log parent/path writability;
- Docker daemon reachability;
- selected default Docker image existence;
- repository-path existence and Git-repository checks;
- configured repository identity vs normalized remote identity;
- SSH key-path resolution for each repository where expected;
- known-hosts availability;
- optional GitHub API/protection verification state;
- ability to construct the MCP server from the config;
- optional tunnel-client/profile health when ChatGPT setup was selected.

Each diagnostic SHALL have a stable code, label, status, safe message, and optional remediation.

Statuses SHALL distinguish:

- `PASS`
- `FAIL`
- `ACTION_REQUIRED`
- `SKIPPED`

Finish SHALL remain disabled while mandatory machine-verifiable checks are unresolved.

The final summary SHALL clearly separate:

- machine-verified success;
- user-attested external UI actions;
- optional skipped actions;
- unresolved failures.

Unexpected diagnostic exceptions SHALL fail closed and SHALL NOT be displayed as success.

## 15. Progressive disclosure

The normal setup path SHALL favor safe defaults and avoid presenting every configuration field.

Advanced controls MAY be revealed per step or globally with `--advanced`.

Fields with reasonable production defaults SHOULD remain untouched unless the operator changes them. This preserves existing server defaults as the single source of truth and reduces wizard drift.

## 16. Configuration ownership

The wizard SHALL explicitly define which fields it owns.

For an existing valid config:

- wizard-owned fields may be changed after review;
- non-wizard-owned valid fields SHALL be preserved byte-semantically where possible or value-semantically at minimum;
- unknown fields are already rejected by production models and therefore cannot be silently preserved as if valid;
- fields added in future production models SHALL require an explicit wizard support decision before the wizard starts modifying them.

A test SHALL make the ownership inventory exhaustive so a new top-level config field cannot accidentally become silently dropped or overwritten.

## 17. Security requirements

The setup feature SHALL preserve all existing security invariants.

Specifically:

- read access does not imply write access;
- repository identity validation remains authoritative;
- SSH scope and mode warnings are explicit;
- no raw private key content is written into config;
- no raw API token is written into config or resumable state;
- unsafe path handling fails closed;
- remote-protection uncertainty remains uncertainty;
- setup SHALL NOT enable broad Docker cleanup or pruning;
- setup SHALL NOT weaken destructive-operation safeguards;
- setup SHALL NOT alter `.mcp-workspace.yml` in registered repositories unless a future explicit feature defines that operation separately;
- logs and diagnostics SHALL redact or omit secret material;
- error messages SHALL be useful without echoing raw secret input.

## 18. Proposed package architecture

```text
src/dev_workspace_mcp/setup/
    __init__.py
    app.py
    app_base.py
    model.py
    state.py
    filesystem.py
    browser.py
    config_writer.py
    diagnostics.py

    prerequisites_step.py
    storage_step.py
    docker_step.py
    repositories_step.py
    ssh_github_step.py
    git_policy_step.py
    runtime_resources_step.py
    chatgpt_step.py
    config_review_step.py
    final_validation_step.py
```

The exact file split MAY evolve, but the architecture SHALL keep presentation, state, mutation planning, filesystem writes, and diagnostics independently testable.

## 19. Production-code reuse requirements

The wizard SHALL call production implementations rather than copy semantics wherever possible, including:

- `load_config()` and `ServerConfig` for final configuration acceptance;
- repository registry/remote normalization helpers for repository identity;
- current Docker availability/probing helpers;
- SSH key resolution/validation helpers where safe and appropriate;
- `create_server()` for a construction-level final check;
- existing duration parsing and Pydantic validators;
- current Git policy types.

If production functionality is currently difficult to reuse because it is embedded in CLI code, implementation SHOULD extract a reusable side-effect-controlled helper rather than duplicate logic in setup.

## 20. Testing strategy

### Unit tests

Cover:

- step-state serialization/deserialization;
- secret-field rejection;
- resume rewinding when process-local secrets are absent;
- dependency invalidation between steps;
- preset-to-production-config mapping;
- candidate generation;
- preservation of advanced fields;
- duplicate YAML-key rejection;
- source-digest/CAS mismatch;
- atomic-write and backup behavior;
- unsafe target rejection;
- diagnostics classification;
- fail-closed unexpected exceptions.

### TUI tests

Use Textual's test/pilot support to verify:

- initial focus and keyboard navigation;
- progress status rendering;
- `Next` disabled on invalid steps;
- `Back` behavior;
- cancel confirmation;
- resume/repair/fresh-start dialog behavior;
- advanced controls visibility;
- review/write gating;
- final `Finish` gating;
- optional-step skip semantics.

### Integration tests

Cover:

- creating a new config end to end;
- updating an existing valid config while preserving advanced settings;
- repairing invalid config only after explicit approval;
- writing and reloading through production `load_config()`;
- Docker/image diagnostics using the project's normal test environment;
- repository verification using temporary Git repositories;
- no-secret setup-state persistence;
- final diagnostics against a realistic valid setup.

### Regression tests

Existing full suite SHALL remain green. The setup implementation SHALL not reduce existing coverage expectations or bypass current contract/docs drift gates.

## 21. Documentation requirements

Implementation SHALL update:

- `README.md` to make the interactive wizard the recommended first-run path;
- `docs/QUICKSTART.md` with wizard-first instructions plus manual fallback;
- `docs/DEVELOPMENT.md` with setup-package development/testing guidance;
- `docs/CONFIGURATION.md` to explain wizard ownership and manual advanced configuration;
- `docs/CONNECTING_A_REPOSITORY.md` to reference `repo add`/wizard flow where implemented;
- `docs/CONNECTING_CHATGPT.md` to reference the optional ChatGPT setup step without duplicating fast-changing OpenAI UI details unnecessarily.

Manual configuration SHALL remain documented for headless, recovery, and advanced use.

## 22. Accessibility and terminal behavior

The TUI SHALL:

- work with keyboard-only navigation;
- not encode status only by color;
- keep important errors in text, not transient notification only;
- handle narrow terminals gracefully with scrolling rather than truncating required controls;
- avoid displaying secret values in clear text;
- provide copy actions for non-secret IDs, paths, URLs, and smoke-test commands where useful;
- return meaningful process exit codes for success, user cancellation, and fatal setup errors.

## 23. Exit semantics

Recommended exit codes:

- `0`: completed successfully or `--check` found no mandatory unresolved diagnostics;
- `1`: expected validation/diagnostic failure in non-interactive mode;
- `2`: fatal setup/config/filesystem error;
- `130`: operator cancelled the interactive wizard.

Exact codes MAY change if the existing CLI has a conflicting convention, but SHALL be documented and tested.

## 24. Compatibility and migration

The wizard SHALL work with configurations authored manually before the wizard existed.

No existing valid configuration SHALL require migration merely to keep running.

If setup-state schema evolves, the state file SHALL carry a version and either:

- migrate explicitly, or
- fail with a clear repair/start-fresh path.

It SHALL never silently reinterpret an unknown state schema.

## 25. Acceptance criteria

The feature is complete when all of the following are true:

1. A fresh operator can produce a valid minimal server config without hand-authoring YAML.
2. A user can register at least one repository through the TUI.
3. Recommended Docker, SSH, and Git safety defaults are explained and materialized correctly.
4. Existing valid configuration can be edited without losing unrelated valid settings.
5. Invalid existing configuration cannot be overwritten without explicit repair approval and backup.
6. Reviewed configuration changes are source-digest bound and cannot clobber a concurrent edit.
7. Writes are atomic and production reload succeeds before success is reported.
8. Raw secrets do not appear in resumable setup state, logs, diagnostics, or generated config fields that are defined as path references.
9. Resume, repair, edit, start-fresh, and cancel paths are covered by tests.
10. Mandatory final diagnostics gate `Finish`.
11. `setup --check`/`doctor` uses the same diagnostic engine as the TUI.
12. The complete existing CI suite remains green.
13. README/Quickstart recommend the wizard while retaining manual setup documentation.

## 26. Implementation principle

The defining rule of this feature is:

> The wizard is an orchestrator and user interface over production configuration semantics; it is not a second implementation of them.

Whenever the TUI and the daemon could disagree, the implementation SHALL restructure code so both call the same validated production path rather than teaching the wizard a parallel approximation.
