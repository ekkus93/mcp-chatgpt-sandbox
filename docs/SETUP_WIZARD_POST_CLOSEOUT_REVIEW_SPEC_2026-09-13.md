# Setup Wizard Post-Closeout Review Fixes Spec

**Status:** Integrated implementation candidate green; guarded merge pending  
**Original baseline:** `master` at `53f9518ba206efa2aae537983601f196da3374c4`  
**Integrated baseline:** `master` at `ce0b291c316dc6b654916cd49f463bae4f60c2c5` after setup hardening PR #64  
**Review source:** Post-closeout setup-wizard code review on 2026-09-13  
**Companion TODO:** `docs/SETUP_WIZARD_POST_CLOSEOUT_REVIEW_TODO_2026-09-13.md`  
**Task series:** `SWF-001` through `SWF-006`

## 1. Purpose

The setup-wizard WIZ and WCR tracks are complete and green. This follow-up does not reopen those tracks wholesale. It captures the remaining issues found during the post-closeout review and defines the narrow corrective work required to make the setup wizard's review/mutation semantics, repository policy, and guardrails match the project's safety standard.

The review found two substantive correctness/safety follow-ups and three lower-risk hardening items:

1. Docker image build and Secure MCP Tunnel profile mutation were labeled/displayed as reviewed actions, but the button handlers could display the exact command and execute the mutation in the same event.
2. Setup state rejected duplicate GitHub identities, while production `ServerConfig` only requires unique repository IDs. That was stricter than production semantics and could reject legitimate multiple-local-clone setups.
3. `src/dev_workspace_mcp/setup/app.py` still had a per-file Ruff `E501` ignore even though the remediation introduced coordinator-growth pressure.
4. `apply_tunnel_profile(runtime_environment=...)` replaced the entire child environment when an explicit runtime mapping was supplied, which could accidentally drop `PATH` and other required ambient process settings.
5. Secret regression tests scanned secret-bearing field names, but they did not explicitly exercise obvious secret-looking values pasted into allowed non-secret fields.

This integrated replay preserves PR #64's guided Git-policy setup hardening and layers these SWF fixes on top of it.

## 2. Non-goals

This work must not redesign the setup wizard, change the public setup command surface, weaken any production validation, bypass guarded PR/CI policy, or alter unrelated runtime behavior.

This work must not relax security checks to make tests easier. It must not introduce automatic mutation retries, silent defaults for malformed config, trust-on-first-use SSH behavior, direct writes to `master`, or a second configuration-writing path.

## 3. Safety principles

All fixes must preserve the existing WCR conclusions:

- Production Pydantic config models remain authoritative.
- Durable setup state remains versioned, strict, and non-secret.
- Configuration writes remain reviewed, source-bound, atomic, byte-verified, backup-preserving, and production-reloaded.
- Repository verification remains read-only.
- User attestation remains separate from machine diagnostic PASS.
- Optional external integrations fail as explicit action-required/unverified states, never as silent success.
- Consequential host mutations are separated from ordinary validation when practical.

## 4. SWF-001 — True two-step reviewed mutation flow

### 4.1 Problem

`SetupStepActionsMixin.build_docker_image()` and `SetupStepActionsMixin.apply_tunnel()` computed/rendered the reviewed command and executed the host mutation in the same button handler. The user did click a mutation button, so this was not a hidden mutation. However, the exact final command may not have been visible before the action was committed.

The desired standard is stricter:

1. Prepare and display the exact mutation plan.
2. Require a separate explicit confirmation/apply action bound to that exact stored plan.
3. Execute only the stored reviewed plan, not freshly reread mutable controls.

### 4.2 Docker requirements

The Docker image build flow must be split into a non-mutating prepare step and a separate confirmed apply step.

Required behavior:

- A prepare action reads the current Docker controls, validates the image/tag enough to build a plan, and renders the exact build command.
- The prepare action must not call `build_default_image()` or mutate Docker state.
- A separate confirmation/apply action executes `build_default_image()` only if a pending reviewed plan exists.
- The apply action must execute the stored pending plan, not reread potentially changed input fields.
- Editing any Docker control after plan preparation must invalidate the pending Docker build plan and disable or reject apply.
- A failed build must leave the Docker step unresolved and must not mark the image as present or the step complete.
- A successful build may re-run Docker validation, but validation must still use the production Docker/image inspection path.

### 4.3 Tunnel requirements

The Secure MCP Tunnel profile flow must be split into a non-mutating prepare step and a separate confirmed apply step.

Required behavior:

- A prepare action reads the current tunnel controls, validates profile/tunnel ID values, and renders the exact `tunnel-client init ...` argv/server command.
- The prepare action must not call `apply_tunnel_profile()` or mutate tunnel-client profile state.
- A separate confirmation/apply action executes `apply_tunnel_profile()` only if a pending reviewed plan exists.
- The apply action must execute the stored pending plan, not reread changed input fields.
- Editing any tunnel-profile or tunnel-id control after preparation must invalidate the pending tunnel plan and disable or reject apply.
- Tunnel runtime credentials must remain external/process-local. No raw runtime credential may be added to persisted setup state or to a Textual `Input` control.
- `--no-browser` semantics remain unchanged.

### 4.4 Testing requirements

Add permanent tests proving:

- Docker prepare displays the exact command and does not call the build mutation.
- Docker apply requires a pending reviewed plan.
- Docker apply uses the stored plan even if widgets change after preparation, or rejects the stale plan explicitly.
- Docker input changes invalidate the pending plan.
- Tunnel prepare displays the exact command and does not call `apply_tunnel_profile()`.
- Tunnel apply requires a pending reviewed plan.
- Tunnel apply uses the stored plan even if widgets change after preparation, or rejects the stale plan explicitly.
- Tunnel input changes invalidate the pending plan.
- Static audit forbids same-handler display-and-mutate patterns for reviewed setup mutations.

## 5. SWF-002 — Repository identity policy consistency

### 5.1 Problem

`SetupState._validate_repositories()` rejected duplicate GitHub identities in setup state. Production `ServerConfig` enforces unique `repositories[].id`, and setup CRUD enforces unique IDs and unique local paths, but production does not prohibit multiple configured repositories pointing at the same `owner/repo`.

That matters for legitimate operator shapes such as:

- two local clones of the same GitHub repository under different repository IDs;
- a primary checkout and a worktree-like second checkout represented as separate configured repositories;
- migration/testing configurations that compare two local states of the same remote.

### 5.2 Required decision

The preferred fix is to align setup state with production semantics:

- keep duplicate repository IDs rejected;
- keep duplicate normalized local paths rejected;
- allow duplicate GitHub identities when IDs and local paths are distinct.

If the implementation instead intentionally keeps setup stricter than production, the stricter rule must be documented as an explicit setup policy, justified in the spec/TODO, and protected with tests. The selected policy for this follow-up is **allow duplicate GitHub identities** when ID/path uniqueness holds.

### 5.3 Testing requirements

Add or update tests proving:

- duplicate IDs are rejected;
- duplicate normalized local paths are rejected;
- duplicate GitHub identities with distinct IDs and paths are accepted by setup state;
- repository CRUD behavior matches the final policy;
- edit/remove flows continue to operate by repository ID, not GitHub identity.

## 6. SWF-003 — Remove or explicitly narrow the `app.py` E501 ignore

### 6.1 Problem

`pyproject.toml` had a per-file Ruff ignore for `E501` on `src/dev_workspace_mcp/setup/app.py`. That exception is undesirable after WCR split the setup Textual code into separate lifecycle, action, and panel modules.

### 6.2 Required behavior

Preferred fix:

- remove the `app.py` `E501` per-file ignore;
- wrap or refactor long lines in `app.py` until Ruff passes normally.

If any setup presentation-module line-count or style guard must be adjusted after integration with other setup hardening branches, that adjustment must be explicit, narrow, and documented in the guard itself rather than silently disabling lint.

### 6.3 Testing requirements

Add a static contract proving `app.py` does not regain a broad per-file `E501` exemption. Coordinator-growth guards must remain active across the setup shell/action/panel modules.

## 7. SWF-004 — Harden tunnel environment merging

### 7.1 Problem

`apply_tunnel_profile(runtime_environment=...)` passed `env=dict(runtime_environment)` when a runtime environment was supplied. That replaced the child process environment instead of merging overrides onto `os.environ`. A caller that supplied only secure runtime material could accidentally drop `PATH`, locale, or other required environment variables, despite `tunnel_client_path()` having just resolved the executable.

### 7.2 Required behavior

- When `runtime_environment is None`, preserve current behavior: pass `env=None` so the child inherits the current process environment.
- When `runtime_environment` is provided, pass `{**os.environ, **runtime_environment}` or equivalent, so supplied values override but do not erase the ambient environment.
- Do not log, render, persist, or echo runtime environment values.
- Add tests that verify base environment preservation and override precedence without using real secrets.

## 8. SWF-005 — Strengthen secret-value regression checks

### 8.1 Problem

Current secret contracts are useful: setup state rejects secret-bearing field names, and the TUI has no raw-secret input fields. The remaining gap is that future code could accidentally add raw secret capture fields unless the field-name and value-shape tests are explicit enough.

Some fields legitimately reference credential files or external IDs, so this must be handled carefully. The goal is not to block every string containing `token` or every path under `/run/secrets`. The goal is to catch accidental addition of raw secret capture paths.

### 8.2 Required behavior

Add tests around the fields most likely to be confused with raw secrets:

- `github_auth_file` remains a credential file path, not token content.
- `tunnel_id` remains a non-secret tunnel identifier, not a runtime API key.
- `tunnel_profile` remains a profile name.
- no setup-state field is added for raw tunnel runtime keys, API keys, passwords, private-key contents, or token contents.

Suggested approach:

- keep name-based scanning as the hard invariant;
- add explicit negative fixtures showing raw-secret-shaped keys/fields are rejected;
- add positive fixtures showing legitimate credential-file paths remain allowed;
- avoid brittle entropy heuristics that would reject normal operator paths or IDs.

## 9. SWF-006 — Final audit, evidence, and merge

The final implementation must include durable evidence comparable to previous WCR tracks:

- exact implementation candidate SHA;
- exact CI run proving Ruff format/lint, strict mypy, contract checks, docs drift, and full Docker-backed pytest pass;
- checklist reconciliation;
- guarded PR merge evidence;
- exact merged `master` SHA;
- fresh post-merge CI run on that exact `master` SHA.

The TODO may be marked complete only after the exact merged `master` validation is green.

## 10. Acceptance criteria

This follow-up is complete only when:

- Docker and tunnel host mutations require true two-step review/apply flow;
- no same-handler reviewed-plan display and host mutation path remains for those actions;
- setup repository identity validation is consistent with the chosen documented policy;
- the broad `app.py` E501 ignore is removed and the remaining setup presentation guardrails are explicit;
- tunnel runtime environment overrides preserve the base process environment;
- secret-lifecycle tests cover both field-name and value-shape regression risks without blocking legitimate credential path references;
- all new contracts are permanent tests;
- authoritative CI is green before and after merge.
