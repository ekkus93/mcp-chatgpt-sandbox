# Setup Wizard Post-Closeout Review Fixes TODO

**Status:** Open  
**Baseline:** `master` at `53f9518ba206efa2aae537983601f196da3374c4`  
**Spec:** `docs/SETUP_WIZARD_POST_CLOSEOUT_REVIEW_SPEC_2026-09-13.md`  
**Task series:** `SWF-001` through `SWF-006`

A checkbox closes only from source, test, documentation, CI, and merge evidence. Do not weaken production validation, reviewed configuration transactions, destructive-safety rules, repository isolation, secret handling, static audits, or CI to obtain green.

## SWF-001 — Make reviewed host mutations true two-step transactions

### Docker image build

- [ ] Split Docker build into a non-mutating prepare/review action and a separate confirm/apply action.
- [ ] Store the pending Docker build plan separately from mutable widget state.
- [ ] Render the exact Docker build command before the apply action can run.
- [ ] Ensure the prepare/review action does not call `build_default_image()` or mutate Docker state.
- [ ] Ensure the confirm/apply action refuses to run without a pending reviewed Docker build plan.
- [ ] Ensure the confirm/apply action uses the stored reviewed plan, not freshly reread controls.
- [ ] Invalidate the pending Docker build plan when relevant Docker controls change.
- [ ] Keep failed Docker builds action-required and never silently mark the Docker step complete.
- [ ] Add tests proving Docker prepare displays the plan without mutation.
- [ ] Add tests proving Docker apply requires a pending reviewed plan.
- [ ] Add tests proving Docker apply is bound to the stored reviewed plan or explicitly rejects stale plans.
- [ ] Add tests proving Docker control edits invalidate pending reviewed build plans.

### Secure MCP Tunnel profile mutation

- [ ] Split tunnel profile mutation into a non-mutating prepare/review action and a separate confirm/apply action.
- [ ] Store the pending `TunnelProfilePlan` separately from mutable widget state.
- [ ] Render the exact `tunnel-client init ...` argv and server command before apply can run.
- [ ] Ensure the prepare/review action does not call `apply_tunnel_profile()` or mutate tunnel-client state.
- [ ] Ensure the confirm/apply action refuses to run without a pending reviewed tunnel plan.
- [ ] Ensure the confirm/apply action uses the stored reviewed plan, not freshly reread controls.
- [ ] Invalidate the pending tunnel plan when tunnel ID/profile controls change.
- [ ] Keep runtime credentials external/process-local; do not add a raw secret input or persisted field.
- [ ] Add tests proving tunnel prepare displays the plan without mutation.
- [ ] Add tests proving tunnel apply requires a pending reviewed plan.
- [ ] Add tests proving tunnel apply is bound to the stored reviewed plan or explicitly rejects stale plans.
- [ ] Add tests proving tunnel control edits invalidate pending reviewed tunnel plans.

### Static guard

- [ ] Add a permanent static audit that prevents reviewed mutation handlers from both rendering a new plan and executing the corresponding host mutation in the same handler.

## SWF-002 — Resolve repository duplicate GitHub identity policy

- [ ] Decide final policy for duplicate GitHub identities in setup state.
- [ ] Prefer aligning setup with production semantics: allow duplicate GitHub identities when IDs and local paths are unique.
- [ ] If keeping setup stricter than production, document the reason and add explicit tests for the stricter policy.
- [ ] Update `SetupState._validate_repositories()` to match the chosen policy.
- [ ] Update repository CRUD helpers if needed so setup state, guided CRUD, and production config agree.
- [ ] Add/retain tests proving duplicate repository IDs are rejected.
- [ ] Add/retain tests proving duplicate normalized local paths are rejected.
- [ ] Add tests proving duplicate GitHub identities with distinct IDs and paths are accepted, unless the stricter policy is deliberately retained and documented.
- [ ] Add tests proving edit/remove flows continue to operate by repository ID, not GitHub identity.

## SWF-003 — Remove or justify the setup `app.py` E501 ignore

- [ ] Remove the broad per-file Ruff `E501` ignore for `src/dev_workspace_mcp/setup/app.py`, or document and narrow an intentional exception.
- [ ] Wrap/refactor remaining long setup `app.py` lines so Ruff passes without the broad exemption.
- [ ] Add a static contract proving setup files do not regain broad per-file style exemptions without explicit justification.
- [ ] Ensure coordinator-growth guards remain active after any formatting/refactor changes.

## SWF-004 — Merge tunnel runtime environment over the ambient process environment

- [ ] Change `apply_tunnel_profile(runtime_environment=...)` so provided runtime values override `os.environ` instead of replacing the full environment.
- [ ] Preserve current inheritance behavior when `runtime_environment is None`.
- [ ] Ensure runtime environment values are not logged, rendered, persisted, or echoed in errors.
- [ ] Add tests proving ambient values such as `PATH` survive when a runtime override mapping is supplied.
- [ ] Add tests proving explicit runtime overrides take precedence over ambient environment values.
- [ ] Add tests proving failure messages remain secret-safe when tunnel-client fails.

## SWF-005 — Strengthen secret-value regression coverage without blocking valid path references

- [ ] Keep the existing hard invariant that secret-bearing setup-state field names are rejected.
- [ ] Keep the existing hard invariant that the TUI defines no raw-secret input control.
- [ ] Add negative fixtures for raw-secret-shaped setup-state fields such as API keys, tokens, passwords, private-key contents, and runtime keys.
- [ ] Add positive fixtures proving credential file path references remain allowed, including paths under conventional secret directories.
- [ ] Add positive fixtures proving non-secret tunnel IDs/profile names remain allowed.
- [ ] Avoid brittle entropy heuristics that would reject normal operator paths, IDs, repository names, or profile names.
- [ ] Verify setup-state load/save errors do not echo supplied raw payloads.

## SWF-006 — Final audit, evidence, and merge

- [ ] Run the unsafe-fallback/silent-failure audit after implementation changes.
- [ ] Verify no new bare `except`, unjustified broad `except Exception`, silent `pass`, unbounded setup subprocess, automatic mutation retry, or presentation-owned config writer path was introduced.
- [ ] Verify user attestation still does not enter machine diagnostics.
- [ ] Verify Docker/tunnel mutation review is now genuinely two-step.
- [ ] Verify repository verification remains read-only.
- [ ] Update this TODO with exact source/test evidence for SWF-001 through SWF-005.
- [ ] Record exact implementation candidate SHA.
- [ ] Record exact implementation candidate CI run.
- [ ] Confirm Ruff format/check green on the implementation candidate.
- [ ] Confirm strict mypy green on the implementation candidate.
- [ ] Confirm setup unit/Textual/integration tests green on the implementation candidate.
- [ ] Confirm full Docker-backed pytest green on the implementation candidate.
- [ ] Confirm MCP/schema/contract/docs drift gates green on the implementation candidate.
- [ ] Merge only through the guarded policy-approved PR path.
- [ ] Record exact merged `master` SHA.
- [ ] Record fresh exact merged-`master` CI run.
- [ ] Mark the Definition of Done only after post-merge CI is green.

## Definition of Done

- [ ] Docker reviewed build is prepare/review first, confirm/apply second.
- [ ] Tunnel reviewed profile mutation is prepare/review first, confirm/apply second.
- [ ] No reviewed host mutation path displays a newly generated plan and mutates in the same handler.
- [ ] Repository duplicate GitHub identity policy is explicit and consistent with production config or deliberately documented as stricter.
- [ ] Setup no longer depends on a broad unexplained `app.py` E501 ignore.
- [ ] Tunnel runtime environment overrides preserve ambient environment values while overriding only supplied keys.
- [ ] Secret lifecycle tests cover field-name and value-shape regression risks without blocking legitimate credential path references.
- [ ] Unsafe-fallback/silent-failure audit remains green.
- [ ] Exact candidate CI is green.
- [ ] Guarded merge is complete.
- [ ] Exact merged-`master` CI is green.
