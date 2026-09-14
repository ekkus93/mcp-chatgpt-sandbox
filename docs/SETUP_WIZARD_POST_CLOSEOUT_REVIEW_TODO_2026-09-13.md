# Setup Wizard Post-Closeout Review Fixes TODO

**Status:** Implementation candidate green; guarded merge pending  
**Baseline:** `master` at `53f9518ba206efa2aae537983601f196da3374c4`  
**Spec:** `docs/SETUP_WIZARD_POST_CLOSEOUT_REVIEW_SPEC_2026-09-13.md`  
**Task series:** `SWF-001` through `SWF-006`  
**Implementation candidate:** `f603e4aa1ea84e66c61c6bbfbfd069014e43c078`  
**Implementation CI:** GitHub Actions `34791638623` — PASS

A checkbox closes only from source, test, documentation, CI, and merge evidence. Do not weaken production validation, reviewed configuration transactions, destructive-safety rules, repository isolation, secret handling, static audits, or CI to obtain green.

## SWF-001 — Make reviewed host mutations true two-step transactions

### Docker image build

- [x] Split Docker build into a non-mutating prepare/review action and a separate confirm/apply action.
- [x] Store the pending Docker build plan separately from mutable widget state.
- [x] Render the exact Docker build command before the apply action can run.
- [x] Ensure the prepare/review action does not call `build_default_image()` or mutate Docker state.
- [x] Ensure the confirm/apply action refuses to run without a pending reviewed Docker build plan.
- [x] Ensure the confirm/apply action uses the stored reviewed plan, not freshly reread controls.
- [x] Invalidate the pending Docker build plan when relevant Docker controls change.
- [x] Keep failed Docker builds action-required and never silently mark the Docker step complete.
- [x] Add tests proving Docker prepare displays the plan without mutation.
- [x] Add tests proving Docker apply requires a pending reviewed plan.
- [x] Add tests proving Docker apply is bound to the stored reviewed plan or explicitly rejects stale plans.
- [x] Add tests proving Docker control edits invalidate pending reviewed build plans.

**Evidence:** `src/dev_workspace_mcp/setup/step_panels.py` now exposes separate `prepare-docker-build` and disabled-by-default `build-docker-image` controls. `src/dev_workspace_mcp/setup/step_actions.py` implements `prepare_docker_build()` as a non-mutating plan builder and `build_docker_image()` as a stored-plan-only apply path. `src/dev_workspace_mcp/setup/app.py` clears `_pending_docker_build_plan` when Docker controls change. `tests/setup/test_reviewed_mutations.py` covers prepare-without-mutation, apply-without-plan rejection, stored-plan binding, and edit invalidation.

### Secure MCP Tunnel profile mutation

- [x] Split tunnel profile mutation into a non-mutating prepare/review action and a separate confirm/apply action.
- [x] Store the pending `TunnelProfilePlan` separately from mutable widget state.
- [x] Render the exact `tunnel-client init ...` argv and server command before apply can run.
- [x] Ensure the prepare/review action does not call `apply_tunnel_profile()` or mutate tunnel-client state.
- [x] Ensure the confirm/apply action refuses to run without a pending reviewed tunnel plan.
- [x] Ensure the confirm/apply action uses the stored reviewed plan, not freshly reread controls.
- [x] Invalidate the pending tunnel plan when tunnel ID/profile controls change.
- [x] Keep runtime credentials external/process-local; do not add a raw secret input or persisted field.
- [x] Add tests proving tunnel prepare displays the plan without mutation.
- [x] Add tests proving tunnel apply requires a pending reviewed plan.
- [x] Add tests proving tunnel apply is bound to the stored reviewed plan or explicitly rejects stale plans.
- [x] Add tests proving tunnel control edits invalidate pending reviewed tunnel plans.

**Evidence:** `src/dev_workspace_mcp/setup/step_panels.py` now exposes separate `tunnel-prepare` and disabled-by-default `tunnel-apply` controls. `src/dev_workspace_mcp/setup/step_actions.py` implements `prepare_tunnel()` as a non-mutating `TunnelProfilePlan` builder and `apply_tunnel()` as a stored-plan-only mutation path. `src/dev_workspace_mcp/setup/app.py` clears `_pending_tunnel_plan` when ChatGPT/tunnel controls change. `tests/setup/test_reviewed_mutations.py` covers prepare-without-mutation, apply-without-plan rejection, stored-plan binding, and edit invalidation.

### Static guard

- [x] Add a permanent static audit that prevents reviewed mutation handlers from both rendering a new plan and executing the corresponding host mutation in the same handler.

**Evidence:** `tests/setup/test_setup_static_audit.py` includes SWF static checks preventing one handler from both preparing/rendering a reviewed host mutation plan and executing the associated mutation call.

## SWF-002 — Resolve repository duplicate GitHub identity policy

- [x] Decide final policy for duplicate GitHub identities in setup state.
- [x] Prefer aligning setup with production semantics: allow duplicate GitHub identities when IDs and local paths are unique.
- [x] If keeping setup stricter than production, document the reason and add explicit tests for the stricter policy.
- [x] Update `SetupState._validate_repositories()` to match the chosen policy.
- [x] Update repository CRUD helpers if needed so setup state, guided CRUD, and production config agree.
- [x] Add/retain tests proving duplicate repository IDs are rejected.
- [x] Add/retain tests proving duplicate normalized local paths are rejected.
- [x] Add tests proving duplicate GitHub identities with distinct IDs and paths are accepted, unless the stricter policy is deliberately retained and documented.
- [x] Add tests proving edit/remove flows continue to operate by repository ID, not GitHub identity.

**Evidence:** The chosen policy is production-aligned: duplicate `github` identities are allowed when repository IDs and paths are distinct. `src/dev_workspace_mcp/setup/model.py` rejects duplicate IDs and duplicate paths, but no longer rejects duplicate GitHub identities. `src/dev_workspace_mcp/setup/repository_setup.py` continues to enforce ID/path uniqueness for guided CRUD. `tests/setup/test_model.py` and `tests/setup/test_review_remediation_contracts.py` cover duplicate-ID rejection, duplicate-path rejection, duplicate-GitHub acceptance, and ID-based edit/remove behavior.

## SWF-003 — Remove or justify the setup `app.py` E501 ignore

- [x] Remove the broad per-file Ruff `E501` ignore for `src/dev_workspace_mcp/setup/app.py`, or document and narrow an intentional exception.
- [x] Wrap/refactor remaining long setup `app.py` lines so Ruff passes without the broad exemption.
- [x] Add a static contract proving setup files do not regain broad per-file style exemptions without explicit justification.
- [x] Ensure coordinator-growth guards remain active after any formatting/refactor changes.

**Evidence:** `pyproject.toml` no longer contains the setup `app.py` `E501` per-file ignore. `tests/setup/test_setup_static_audit.py` now checks setup per-file ignores and retains coordinator growth guards. Candidate CI run `34791638623` passed Ruff format and Ruff lint without the broad exemption.

## SWF-004 — Merge tunnel runtime environment over the ambient process environment

- [x] Change `apply_tunnel_profile(runtime_environment=...)` so provided runtime values override `os.environ` instead of replacing the full environment.
- [x] Preserve current inheritance behavior when `runtime_environment is None`.
- [x] Ensure runtime environment values are not logged, rendered, persisted, or echoed in errors.
- [x] Add tests proving ambient values such as `PATH` survive when a runtime override mapping is supplied.
- [x] Add tests proving explicit runtime overrides take precedence over ambient environment values.
- [x] Add tests proving failure messages remain secret-safe when tunnel-client fails.

**Evidence:** `src/dev_workspace_mcp/setup/tunnel.py` merges runtime overrides over a copy of `os.environ` only when an explicit override mapping is supplied, while preserving `env=None` inheritance behavior for the default path. `tests/setup/test_tunnel.py` covers ambient preservation, override precedence, and secret-safe tunnel-client failure messages.

## SWF-005 — Strengthen secret-value regression coverage without blocking valid path references

- [x] Keep the existing hard invariant that secret-bearing setup-state field names are rejected.
- [x] Keep the existing hard invariant that the TUI defines no raw-secret input control.
- [x] Add negative fixtures for raw-secret-shaped setup-state fields such as API keys, tokens, passwords, private-key contents, and runtime keys.
- [x] Add positive fixtures proving credential file path references remain allowed, including paths under conventional secret directories.
- [x] Add positive fixtures proving non-secret tunnel IDs/profile names remain allowed.
- [x] Avoid brittle entropy heuristics that would reject normal operator paths, IDs, repository names, or profile names.
- [x] Verify setup-state load/save errors do not echo supplied raw payloads.

**Evidence:** `src/dev_workspace_mcp/setup/model.py` rejects secret-bearing state key fragments, now including `runtime_key`. `tests/setup/test_model.py` adds negative fixtures for secret-shaped fields and positive fixtures for `/run/secrets/...` credential-file paths plus non-secret tunnel identifiers/profile names. Existing state-load error tests continue to verify raw payloads are not echoed. `tests/setup/test_review_remediation_contracts.py` continues to verify the TUI defines no raw-secret `Input` controls.

## SWF-006 — Final audit, evidence, and merge

- [x] Run the unsafe-fallback/silent-failure audit after implementation changes.
- [x] Verify no new bare `except`, unjustified broad `except Exception`, silent `pass`, unbounded setup subprocess, automatic mutation retry, or presentation-owned config writer path was introduced.
- [x] Verify user attestation still does not enter machine diagnostics.
- [x] Verify Docker/tunnel mutation review is now genuinely two-step.
- [x] Verify repository verification remains read-only.
- [x] Update this TODO with exact source/test evidence for SWF-001 through SWF-005.
- [x] Record exact implementation candidate SHA.
- [x] Record exact implementation candidate CI run.
- [x] Confirm Ruff format/check green on the implementation candidate.
- [x] Confirm strict mypy green on the implementation candidate.
- [x] Confirm setup unit/Textual/integration tests green on the implementation candidate.
- [x] Confirm full Docker-backed pytest green on the implementation candidate.
- [x] Confirm MCP/schema/contract/docs drift gates green on the implementation candidate.
- [ ] Merge only through the guarded policy-approved PR path.
- [ ] Record exact merged `master` SHA.
- [ ] Record fresh exact merged-`master` CI run.
- [ ] Mark the Definition of Done only after post-merge CI is green.

**Evidence:** Implementation candidate `f603e4aa1ea84e66c61c6bbfbfd069014e43c078` passed GitHub Actions CI run `34791638623`. That run passed Ruff format, Ruff lint, strict mypy, MCP contract check, `contract_version` history check, `docs/TOOLS.md` drift check, and full Docker-backed pytest.

## Definition of Done

- [x] Docker reviewed build is prepare/review first, confirm/apply second.
- [x] Tunnel reviewed profile mutation is prepare/review first, confirm/apply second.
- [x] No reviewed host mutation path displays a newly generated plan and mutates in the same handler.
- [x] Repository duplicate GitHub identity policy is explicit and consistent with production config or deliberately documented as stricter.
- [x] Setup no longer depends on a broad unexplained `app.py` E501 ignore.
- [x] Tunnel runtime environment overrides preserve ambient environment values while overriding only supplied keys.
- [x] Secret lifecycle tests cover field-name and value-shape regression risks without blocking legitimate credential path references.
- [x] Unsafe-fallback/silent-failure audit remains green.
- [x] Exact candidate CI is green.
- [ ] Guarded merge is complete.
- [ ] Exact merged-`master` CI is green.
