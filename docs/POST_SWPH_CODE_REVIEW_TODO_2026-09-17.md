# Post-SWPH Code Review TODO

**Status:** Complete — `PSCR-001` through `PSCR-010` closed  
**Date:** 2026-09-17  
**Spec:** `docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`  
**Reviewed baseline:** `ba79759a1ed9287105d9891bbb1cefccdc396d78`  
**Task series:** `PSCR-001` through `PSCR-010`  
**Validation series:** `PSCRV-*`

This TODO implements `docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`. It addresses one functional
defect and four fidelity gaps found by re-reviewing the closed
`docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md` (SWPH-001→010) against its merged
implementation. All ten SWPH tasks were confirmed substantively implemented; these are the residue.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Use `ralph/...` branches for source work.
- [ ] Use exact-head compare-and-swap guards for writes and merges.
- [ ] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [ ] Preserve manual YAML configuration and existing CLI workflows.
- [ ] Preserve reviewed config-write semantics, serialization, and production reload.
- [ ] Preserve the SWPH-006 lock design: one non-blocking attempt, persistent inode, no retry/sleep.
- [ ] Preserve leaf-private directory semantics as documented in `docs/CONFIGURATION.md`.
- [ ] Preserve explicit operator approval and two-step reviewed Docker apply.
- [ ] Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- [ ] Add a focused regression test for every correctness defect.
- [ ] Do not weaken existing tests or architecture guards to make CI green.
- [ ] Do not bump `contract_version` unless the public MCP contract actually changes.
- [ ] Never run the full suite concurrently with a CI run on the self-hosted runner.
- [ ] Record exact SHA/PR/run/job evidence as each phase closes.

---

## Phase 1 — Installed-distribution Docker correctness

### PSCR-001 — Decide and record the installed-distribution Docker build policy

**Goal:** the Docker build-context contract must be an explicit recorded decision, not an accident of
repository layout.

- [x] Confirm the defect on the current baseline: build a wheel and show `docker/default/` is absent.
- [x] Confirm `parents[3]` resolves outside the package root for an installed layout.
- [x] Confirm the development install is editable and therefore masks the condition.
- [x] Choose Option A (support installed Docker builds) or Option B (declare them checkout-only).
- [x] Record the chosen option and its rationale in this file.
- [x] Record the choice in `setup/docker_setup.py` where the context is resolved.
- [x] If Option B, remove the docstring claim that the context comes from an "installed source tree".
- [x] Confirm the choice does not alter Docker-unavailable degraded behavior.

**Validation — PSCRV-001**

- [x] Test or recorded evidence: the packaging outcome matches the chosen option.
- [x] Documentation states the chosen option in operator-facing terms.

**Decision and evidence (2026-09-17)**

- **Option B selected — the bundled Docker build context is a source-checkout asset.**
  Rationale: the benefit of Option A exists only for a deployment mode this project does not
  have (no PyPI publication; `README.md` and `docs/QUICKSTART.md` both drive the wizard with
  `uv run ... setup` from a checkout), while its cost includes touching the two-step reviewed
  plan/apply boundary that SWPH-002/SWPH-003 were written to establish: `client.images.build`
  needs a real directory, `importlib.resources.as_file()` guarantees one only inside its
  context manager, and the reviewed plan stores `build_context` for a *later* handler
  invocation. Option A remains the migration path if the package is ever published.
- Defect confirmed on baseline `ba79759a1ed9287105d9891bbb1cefccdc396d78`: a wheel built from
  that tree contains 120 entries with no `Dockerfile` and no `entrypoint.sh`, and
  `pyproject.toml` declares no data/force-include configuration.
- `parents[3]` confirmed to resolve `<venv>/lib/pythonX.Y/docker/default` for an installed
  layout versus `<repo>/docker/default` for the checkout.
- Development install confirmed editable (`mcp_chatgpt_sandbox.pth`, no real package directory
  in `site-packages`), which is why `__file__` always pointed into `src/` and neither CI nor
  the local suite could observe the defect.
- Docker-unavailable degraded behavior unchanged: the `DockerException`/`SetupDockerError`
  handler in `validate_docker()` is untouched by this task.
- Policy recorded in `setup/docker_setup.py` (`_source_checkout_root`), `README.md`, and
  `docs/DEVELOPMENT.md`'s troubleshooting entry.

### PSCR-002 — Make bundled build-context resolution correct for the selected policy

**Goal:** resolution must not depend on repository layout.

- [x] Remove `Path(__file__).resolve().parents[3]` arithmetic from context resolution.
- [x] If Option A, package `docker/default/` into the distribution and resolve via
      `importlib.resources`. — not applicable; Option B selected.
- [x] If Option B, resolve from an explicitly checkout-scoped location and fail with a distinct,
      actionable error when absent.
- [x] Keep `validate_default_image_build_context()` as the single normalization/refusal point.
- [x] Keep the resolved context identical between preview and execution.
- [x] Ensure the failure message names the operator remedy and contains no secrets.

**Validation — PSCRV-002**

- [x] Regression test proves resolution succeeds from a source checkout.
- [x] Regression test proves the installed-layout outcome **without** depending on the development
      layout (for example by resolving against a constructed package root, not the real one).
- [x] Test: preview argv and executed argv still resolve the same context.
- [x] Existing `tests/setup/test_guided_domain.py` bundled-context test remains green or is replaced
      by a strictly stronger one.
- [x] Existing SWPH Docker boundary regressions remain green unmodified.

**Closure evidence (2026-09-17)**

- `_source_checkout_root()` replaces the parent-index arithmetic with an upward search for a
  directory holding **both** `pyproject.toml` and `docker/default/`. The search is
  self-validating: it cannot report a root that does not actually carry the context, so the
  installed case fails at the single point of resolution instead of returning a path that does
  not exist.
- A non-editable install into an in-repo virtualenv still resolves the enclosing checkout. That
  is intended and documented — the checkout is genuinely present and its context is usable.
- `tests/setup/test_pscr_docker_context_policy.py`: 4/4 PASS. The installed-layout tests
  synthesize the module location via `monkeypatch.setattr(docker_setup, "__file__", ...)` rather
  than trusting this process's layout, which is what makes them able to observe the defect at
  all. One test also proves a `pyproject.toml` *alone* does not satisfy the search, so an
  installed distribution sitting under an unrelated project root cannot resurrect the defect in
  a new disguise.
- Full Docker-backed suite on the implementation tree: `1415 passed` in `677.05s`, zero skips.
  Ruff format (315 files) PASS; Ruff lint PASS; strict mypy (103 files) PASS.
- Existing SWPH Docker boundary regressions and `tests/setup/test_guided_domain.py` remained
  green unmodified.

### PSCR-003 — Decouple Docker step validation from build-context availability

**Goal:** a present configured image must be sufficient to validate the Docker step.

- [x] Audit `validate_docker()` ordering: plan construction currently precedes `image_exists()`.
- [x] Resolve image presence before requiring a build context.
- [x] Make the build command preview best-effort in the validation path, not a precondition.
- [x] Keep build-context failure actionable in `prepare_docker_build()` and `build_docker_image()`,
      where a context is genuinely required.
- [x] Ensure the `#docker-defer-image` deferral remains reachable when the image is missing.
- [x] Ensure no Docker client or build call occurs after a context resolution failure.
- [x] Preserve mutation-free screen rendering and explicit approval before build.

**Validation — PSCRV-003**

- [x] Test: image present + build context unavailable ⇒ Docker step validates.
- [x] Test: image missing + context unavailable ⇒ actionable, deferral still reachable.
- [x] Test: build path with unavailable context remains action-required and invokes no build.
- [x] Test: wizard is not blocked from advancing in the image-present/context-absent case.
- [x] Existing Docker-unavailable degraded-behavior tests remain green.

**Closure evidence (2026-09-17)**

- `validate_docker()` now reads `#docker-defer-image` and validates widget values first, resolves
  image presence second, and resolves the build context **last**, purely to render a preview. A
  failed context yields `Build command unavailable: ...` instead of failing the step. Because the
  context is resolved after the Docker call, no Docker client or build call can follow a context
  resolution failure — the subtask is satisfied by ordering, not by a guard.
- Catching `SetupDockerError` narrowly around plan construction is sound because
  `choices.config_fragments()` has already validated the image name through the same
  `DockerConfig` the plan builder uses; a rejected image name therefore fails earlier, and the
  only error reachable at that point is an unavailable context.
- `tests/setup/test_pscr_docker_validation_ordering.py`: 3/3 PASS, asserting a present image
  validates the step **and leaves `#next` enabled** (a green message alone would not prove the
  wizard is unblocked), that the deferral is reachable when the image is missing, and that the
  build path still refuses an unavailable context without reaching Docker.

**Superseded SWPH-002 guard**

- `test_swph_002_missing_context_during_validation_stays_inside_handler` previously asserted
  `docker_calls == 0`. That held only incidentally: the build context used to be a precondition
  of validation, so its failure returned before Docker was reached. Learning whether the image
  is present necessarily means asking Docker, so no form of PSCR-003 can preserve that
  assertion. The test was rewritten, with the reasoning recorded in its own docstring, to assert
  strictly more: nothing escapes the Textual boundary, no build is invoked, Docker is consulted
  exactly once, the preview reports its own unavailability, and the step outcome follows image
  presence. SWPH-002's actual goal — expected missing-context conditions never escaping the
  event handler — is unchanged and still covered.

**Concurrency incident (recorded per the global rules)**

- The first full-suite run for this task was started while CI run `35271467140` was in flight for
  the PSCR-001/002 push, violating this file's own rule against concurrent suite runs. Both runs
  failed with the documented collision signature: 11 failures in CI and 1 locally, every one an
  `assert [<Container: ...>] == []` on a namespace-blind workspace-label query seeing the other
  run's container. A clean re-run on a verified-idle host passed `1418 passed` in `687.07s` with
  zero failures, including every test that had failed in either run.
- Related observation for a future task, deliberately not fixed here: several `tests/workspace`
  assertions query Docker by `LABEL_WORKSPACE_ID` **without** the `container_matches_test_namespace`
  filter that `workspace/manager_core.py` applies, so the `MCP_CHATGPT_SANDBOX_TEST_RESOURCE_NAMESPACE`
  isolation mechanism does not protect them. That is why two suites on one host cannot coexist.

---

## Phase 2 — Review-surfaced correctness and hygiene

### PSCR-004 — Remove the vestigial tunnel `project_root` parameter

**Goal:** no dead parameter, and no `Path.cwd()` anywhere in the setup package.

- [x] Remove `project_root` from `build_tunnel_profile_plan()`.
- [x] Remove the `Path.cwd()` call site in `setup/step_actions.py`.
- [x] Remove the now-obsolete "source compatibility" docstring paragraph.
- [x] Confirm no remaining caller passes a project root.
- [x] Keep the persistent profile bound to the active Python environment.

**Validation — PSCRV-004**

- [x] Static or unit test: no `Path.cwd()` / `os.getcwd` in `src/mcp_chatgpt_sandbox/setup/`.
- [x] Existing `tests/setup/test_swph_tunnel_invocation.py` remains green after signature change.
- [x] Test: profile plan still resolves the interpreter and stays secret-free.

**Closure evidence (2026-09-17)**

- `project_root` removed from `build_tunnel_profile_plan()` along with its `del project_root`
  and the "source compatibility" paragraph; the `Path.cwd()` call site and the then-unused
  `from pathlib import Path` import removed from `setup/step_actions.py`. Three test call sites
  updated (`test_tunnel.py`, `test_reviewed_mutations.py`, `test_swph_tunnel_invocation.py`).
- No coverage was lost by dropping the parameter. SWPH-001's shell-safety test asserted that a
  deliberately-ignored project root did not leak into the command, which is vacuous once the
  parameter is gone; cwd-independence is proven independently by
  `test_swphv_001_tunnel_prepare_is_independent_of_caller_cwd`, which uses `monkeypatch.chdir`.
- `test_pscrv_004_setup_never_reads_the_process_working_directory` added to
  `tests/setup/test_setup_static_audit.py`. It is **AST-based, not substring-based**: the first
  draft grepped for `"Path.cwd()"` and would have failed on `docker_setup.py`'s and `tunnel.py`'s
  own docstrings, which now document that they never consult the working directory — the same
  fragility PSCR-007 exists to remove from the persistence-ownership guard.
- `test_pscrv_004_working_directory_guard_detects_a_violation` pins the detector itself, proving
  it catches `Path.cwd()`, `os.getcwd()`, and an aliased `P.cwd()` while ignoring prose mentions.
  A guard whose detection silently matched nothing would otherwise pass for the wrong reason,
  which is precisely the defect PSCR-006 addresses.
- Verification: Ruff format (316 files) PASS; Ruff lint PASS; strict mypy (103 files) PASS;
  Docker-free suite `614 passed`; `tests/setup/test_setup_static_audit.py` `13 passed`.
- The full Docker-backed suite was deliberately **not** run for this task: CI run `35274522322`
  was in flight for PSCR-003, and this task changes only `setup/` code whose tests are all
  Docker-free. The full suite is run once at PSCR-008 on an idle host.

### PSCR-005 — Fix the `fcntl` fail-closed descriptor leak

**Goal:** the unsupported-platform path must leak nothing.

- [x] Resolve `_fcntl_module()` before opening the lock file.
- [x] Ensure no lock file is created on a platform that cannot lock it.
- [x] Preserve the existing fail-closed error and its message.
- [x] Preserve all other lock ordering and semantics unchanged.

**Validation — PSCRV-005**

- [x] Test: simulated missing `fcntl` raises the fail-closed error, creates no lock file, and leaks
      no descriptor.
- [x] Existing `tests/setup/test_swph_config_commit_lock.py` remains green unmodified.

**Closure evidence (2026-09-17)**

- `fcntl = _fcntl_module()` moved above `_commit_lock_path()`/`os.open()`. It previously sat
  between the open and the `try/finally` that closes the descriptor, so an unsupported platform
  failed closed correctly while leaking the descriptor and leaving a lock file it could never
  lock. Failing before anything is created keeps "cannot serialize" from mutating the filesystem.
- SWPH-006's lock semantics are untouched: single non-blocking `LOCK_EX|LOCK_NB` attempt,
  persistent lock inode, owner-only mode, regular-file check, no retry or sleep, and source
  revalidation plus backup/atomic replacement/byte verification/production reload still inside
  the critical section.
- `test_pscrv_005_unlockable_platform_creates_no_lock_file_and_leaks_no_descriptor` added to the
  existing SWPH-006 lock file. It asserts the fail-closed error, an unchanged
  `/proc/self/fd` count, no lock file on disk, and an unmodified config.
- **The test was verified to discriminate**, not merely to pass: re-applying the pre-PSCR-005
  ordering makes it fail on the descriptor count (13 open descriptors where 12 were expected),
  and restoring the fix makes it pass. A regression test for a leak that passes against the
  leaking code would be worthless, which is the same defect PSCR-006 removes from the
  Git-policy mutation tests.
- Verification: Ruff format PASS; Ruff lint PASS; strict mypy PASS;
  `tests/setup/test_swph_config_commit_lock.py` `7 passed`.

---

## Phase 3 — Test and guard fidelity

### PSCR-006 — Replace the tautological Git-policy mutation tests

**Goal:** the mutation proofs must exercise the real contract.

- [x] Replace the future-production-field test so it runs the real exhaustiveness check against a
      mutated model rather than asserting local set inequality.
- [x] Replace the stale-guided-field test on the same basis.
- [x] Keep the existing bidirectional production/editor/UI/manifest equality assertions.
- [x] Keep `GIT_POLICY_GUIDED_CONTROLS` independent of `GitPolicyConfig.model_fields`.

**Validation — PSCRV-006**

- [x] Test: the replacement fails when a field is added to the model under test and no control exists.
- [x] Test: the replacement fails for a manifest entry absent from the model under test.
- [x] Demonstrate the replacement is strictly stronger than what it replaces.
- [x] Existing guided-control pilots, round-trip, and preset tests remain green.

**Closure evidence (2026-09-17)**

- The contract is now `_check_guided_manifest(policy_model=..., guided_controls=..., ui_fields=...)`,
  and all three tests call it. The production test passes the real values; the mutation tests pass a
  `GitPolicyConfig` subclass carrying an unsupported field, and a manifest carrying an entry with no
  production field.
- The future-field test additionally re-runs the same call against the *unmutated* model and
  asserts it passes, so its `pytest.raises` is attributable to the added field rather than to a
  broken helper.
- **Strict strengthening demonstrated empirically.** Temporarily removing
  `assert production_fields == guided_fields` from the contract makes
  `test_swphv_004_future_production_field_fails_guided_exhaustiveness_contract` fail, because
  `pytest.raises` then sees no `AssertionError`. The replaced version would have passed against
  that same neutered contract, since it never invoked the contract at all — it asserted only
  `frozenset(A | {x}) != frozenset(A)`. The new test is therefore coupled to the real check and
  the old one was coupled to nothing.
- Noted honestly: the stale-manifest test trips on the `editor_fields` comparison rather than the
  production one, because it also widens `ui_fields` to isolate the manifest discrepancy. It is
  still coupled to the real contract, just to a different assertion within it.
- `tests/setup/test_swph_git_policy_exhaustiveness.py`: `4 passed`. `GIT_POLICY_GUIDED_CONTROLS`
  remains an explicit literal independent of `GitPolicyConfig.model_fields`.

### PSCR-007 — Strengthen the presentation persistence-ownership guard

**Goal:** the guard must not be defeatable by aliasing or trippable by a comment.

- [x] Replace raw substring matching with parsed-source analysis (AST imports and call targets).
- [x] Ensure a comment mentioning a forbidden token no longer fails the guard.
- [x] Ensure an aliased YAML or atomic-write import is still caught.
- [x] Ensure `Path.open("w")`-style persistence in a presentation module is caught.
- [x] Keep the discovery-versus-manifest equality check unchanged.

**Validation — PSCRV-007**

- [x] Test: benign comment mentioning a forbidden token passes.
- [x] Test: aliased import of a config-write primitive fails.
- [x] Test: direct file-write call in a presentation module fails.
- [x] Existing setup static-audit suite remains green.

**Closure evidence (2026-09-17)**

- `_persistence_violations()` parses each presentation module and reports forbidden imports
  (including aliased ones), `write_text`/`write_bytes` calls, `atomic_write_text` calls whether
  bare or module-qualified, and `open(...)` in a writing mode. `_opens_for_writing()` examines
  every positional argument and the `mode=` keyword rather than one fixed position, because the
  builtin puts the mode second (`open(path, "w")`) while `Path.open` puts it first
  (`path.open("w")`).
- `test_pscrv_007_persistence_guard_catches_what_substring_matching_missed` pins the detector on
  both directions: the two former false positives (a docstring and a comment mentioning a token)
  now pass, and the three former bypasses (`import yaml as y`, `atomic_write_text as aw`,
  `p.open("w")`) are caught. Reads (`p.open("r")`, `p.read_text()`) are not flagged.
- One gap was found and closed during implementation: the first version checked
  `_PERSISTENCE_NAMES` only on bare `Name` calls, so a module-qualified
  `filesystem.atomic_write_text(...)` would have passed even though the substring scan caught it.
  That would have made this guard weaker rather than strictly stronger, so the `Attribute` branch
  now checks the same set and a regression test pins the shape.
- The discovery-versus-manifest equality inside `_presentation_files()` is untouched, so every
  consumer still enforces it.
- `tests/setup/test_setup_static_audit.py`: `14 passed`.

### PSCR-009 — Remove the second layout-dependent build-context site

**Goal:** the defect class PSCR-002 removed must not survive anywhere in the production package.

Found by PSCR-008's final review. `workspace/container_config.py` carries the same
`parents[3]` arithmetic, exported from `workspace/__init__.py`, with no production consumer —
its only reader is `tests/workspace/conftest.py`'s lazy image-build fixture.

- [x] Confirm `DEFAULT_IMAGE_BUILD_CONTEXT` has no production consumer.
- [x] Move the constant to its only consumer rather than re-deriving it in production.
- [x] Remove it from `workspace/__init__.py`'s imports and `__all__`.
- [x] Use the established test repo-root idiom, not parent-index arithmetic from the package.
- [x] Leave `DEFAULT_IMAGE_TAG` in production, where it is genuinely used.

**Validation — PSCRV-009**

- [x] Static test: no `parents[3]`-style build-context arithmetic remains in `src/`.
- [x] Existing Docker-backed default-image tests still build and use the test image.
- [x] `tests/workspace/conftest.py` resolves the same context path as before the move.

**Closure evidence (2026-09-17)**

- Confirmed no production consumer: the only reader was `tests/workspace/conftest.py`'s lazy
  image-build fixture. The constant was nonetheless defined in `workspace/container_config.py`
  and re-exported from `workspace/__init__.py`'s `__all__`, so it read as public API.
- Moved to `tests/workspace/conftest.py` using `Path(__file__).resolve().parents[2]`, the
  repo-root idiom already used by `test_setup_static_audit.py`,
  `test_swph_git_policy_exhaustiveness.py`, and others. Moving it rather than re-deriving it in
  production dissolves three problems at once: layout-dependent arithmetic, a test-only constant
  living in production, and a misleading public export.
- Verified the relocated constant resolves the **same** absolute path as before
  (`/home/phil/work/mcp-chatgpt-sandbox/docker/default`, `is_dir()` true), so the fixture builds
  from an identical context.
- `DEFAULT_IMAGE_TAG` deliberately stays in `container_config.py`, where production genuinely
  uses it.
- `test_pscrv_009_production_has_no_layout_dependent_build_context` added. It is scoped to the
  whole production package rather than the two known sites, and flags any `.parents[<int>]`
  subscript, so a third instance cannot appear unnoticed. It reports zero offenders.
- Verification: Ruff format (316 files) PASS; Ruff lint PASS; strict mypy (103 files) PASS;
  Docker-free suite `668 passed`. The now-unused `pathlib.Path` import was removed from
  `container_config.py`.

### PSCR-010 — Make workspace test assertions namespace-aware

**Goal:** the G3R-015 isolation the suite already builds must actually protect its assertions.

Root cause of the collision recorded under PSCR-003. No test applies
`container_matches_test_namespace`, so absence and exact-count assertions see concurrent runs'
containers through their logical-id labels.

- [x] Enumerate every `containers.list` / `volumes.list` assertion in `tests/workspace`.
- [x] Classify each as absence, exact-count, or inclusion; only the first two are affected.
- [x] Add one shared helper that scopes a label query to the running context's namespace.
- [x] Convert the affected assertions to it, including the `name`-substring filters.
- [x] Leave inclusion assertions alone and record why.
- [x] Do not weaken any assertion: absence must stay absence, exact counts stay exact.

**Validation — PSCRV-010**

- [x] Test: the helper excludes a container carrying a foreign namespace label.
- [x] Test: the helper includes a container carrying this context's namespace label.
- [x] Test: the helper excludes nothing when no namespace is active (production shape).
- [x] Full Docker-backed suite passes on an idle host.

**Closure evidence (2026-09-17)**

- `containers_in_this_context()` added to `tests/workspace/conftest.py`, beside the
  `docker_client` fixture that establishes the namespace, and applied at seven sites:
  `test_project_config_integration.py` (3), `test_resources.py` (2), `test_default_image.py` (1),
  `test_recovery.py` (1), `test_remote_protection.py` (1).
- The three `test_project_config_integration.py` sites were the subtlest: they filtered on
  `{"name": "mcp-chatgpt-sandbox-ws-example"}`, and Docker's `name` filter is a **substring**
  match, so a concurrent run's `...-ws-example-t<namespace>-<digest>` satisfied it. They are now
  label-scoped like the rest.
- `test_project_config_integration.py`'s cache-volume assertion is deliberately **not** converted.
  It is an *inclusion* check against a hash-derived namespaced name that a foreign run cannot
  produce, so it was never vulnerable; converting it for uniformity would have added noise without
  adding protection.
- No assertion was weakened: absence assertions remain `== []`, exact-count assertions remain
  exact, and `test_recovery.py`'s single-container unpack remains an unpack.
- `tests/workspace/test_pscr_namespace_scoped_assertions.py`: 3/3 PASS, using a fake client so
  they need no Docker daemon. The first test demonstrates the defect shape directly — the
  unfiltered query returns two containers while the helper returns one — so the filter is pinned
  independently of whether a converted assertion would notice a regression.
- Full Docker-backed suite on an idle host (CI run `35279966298` confirmed complete first):
  `1426 passed in 662.09s`, zero failures, zero skips. Ruff format (316 files) PASS; Ruff lint
  PASS; strict mypy (103 files) PASS.
- Implementation note: the first pass at adding imports used a line heuristic and inserted them
  mid-file, breaking five modules' syntax; Ruff caught it immediately. Redone by locating the true
  top-level import block with `ast`, and the "already imported?" test was itself corrected to
  check for a real `ImportFrom` binding rather than a substring, which the converted call sites
  had been satisfying spuriously.

---

## Phase 4 — Final acceptance and closure

### PSCR-008 — Final regression, code-review, contract, and merge closure

**Goal:** close only after every touched boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [x] Bundled Docker context resolves per the recorded policy in both layouts.
- [x] No repository-layout-dependent path arithmetic remains in context resolution.
- [x] A present configured image validates the Docker step without a build context.
- [x] Build-context failures remain actionable and invoke no Docker build.
- [x] No `Path.cwd()` remains in the setup package; no dead parameter carries one.
- [x] Lock fail-closed path creates no file and leaks no descriptor.
- [x] Lock ordering, non-blocking acquisition, and no-retry properties unchanged.
- [x] Git-policy mutation tests exercise the real contract.
- [x] Presentation ownership guard is parse-based and still enforces discovery equality.
- [x] Leaf-private directory semantics unchanged and still matching `docs/CONFIGURATION.md`.
- [x] Setup state remains secret-free; diagnostics remain fail-closed and non-secret.
- [x] Manual YAML and CLI workflows remain supported.
- [x] Documentation matches implementation for the Docker context policy.

#### Contract/version decision

- [x] Re-export schemas and diff against `schemas/tools.json`.
- [x] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCRV-008

- [x] `uv run ruff format --check .` passes. — 316 files.
- [x] `uv run ruff check .` passes.
- [x] `uv run mypy src` passes in strict mode. — 103 source files.
- [x] MCP exported-schema contract check passes. — re-export byte-identical.
- [x] `contract_version` history gate passes. — unchanged, so the gate is satisfied trivially.
- [x] `docs/TOOLS.md` drift gate passes. — `tests/test_docs_tools.py` green.
- [x] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [x] Confirm the run was not concurrent with a self-hosted CI run.
- [x] Record exact pytest count. — `1423 passed`.
- [x] Record exact warning count. — `0 warnings`.
- [x] Record exact coverage total/statements/misses. — `88%`, `9327` statements / `1124` misses.
- [x] Record exact implementation head SHA. — `0c7752600950a693dc276fe93b0c209dfb000679`.
- [x] Record exact CI run ID and job ID. — run `35278709124`, job `105395410852`.
- [x] Merge through exact-head guard. — **not applicable**: this series commits directly to
      `master` per the project's current convention, so there is no PR or merge commit. Recorded
      as N/A rather than checked as done.
- [x] Record merge commit SHA. — **not applicable**, see above.
- [x] Post-merge `master` CI passes on the exact merge SHA. — the `master` push *is* the
      exact-head run: `35278709124` on `0c77526`, conclusion **success**.
- [x] Record post-merge run/job/test/coverage evidence. — run `35278709124`, job
      `105395410852`; `1423 passed in 730.74s`, `0 warnings`; coverage `88%` (`9327` statements /
      `1124` misses).
- [x] Update this TODO status to complete only after post-merge evidence is green.

**Closure evidence (2026-09-17)**

- Local closure run on a verified-idle host (no CI in flight, no competing suite, no leftover
  project containers): `1423 passed in 714.55s`, `0 warnings`, coverage `88%` — `9327`
  statements / `1124` misses. Zero skips, which matters because `tests/_skip_policy.py` converts
  any skip into a session failure.
- Gates: `ruff format --check` (316 files) PASS; `ruff check` PASS; `mypy src` strict (103 files)
  PASS.
- Contract: re-exported through the same command CI uses and diffed byte-for-byte against
  `schemas/tools.json` — **identical**. `contract_version` remains `0.10.3` and **no bump was
  required**, because none of these findings touched the public MCP tool surface. Packaging and
  path-resolution changes do not alter exported tool schemas.
- The immediately preceding CI run `35275666421` (head `8831b1b`, PSCR-007) completed
  **success**, which is also the first clean CI evidence for PSCR-001 through PSCR-007 as an
  integrated whole.
- PSCR-008's own review found a real defect rather than rubber-stamping: the same `parents[3]`
  arithmetic survived in `workspace/container_config.py`. That became PSCR-009, and this TODO's
  `PSCR-001`/`PSCR-002` scope wording ("context resolution") is why it was not caught earlier.

**Process deviations, recorded honestly**

- **One concurrency violation.** The first PSCR-003 suite run overlapped CI run `35271467140`
  (PSCR-001/002), producing 11 CI failures and 1 local failure, every one an
  `assert [<Container: ...>] == []` on a namespace-blind workspace-label query. Both were
  collision artifacts; a clean re-run passed. CI run `35271467140` therefore remains red against
  `ed6132e` for a reason that is not a defect in that commit, and the later green runs supersede
  it.
- **Three CI runs were cancelled as superseded** (`6971e3b`, `7a70036`, `cec5460`) to free the
  single self-hosted runner for this closure run. Each task's local verification is recorded in
  its own section above; the exact-head run is what this closure depends on.
- **PSCR-001/PSCR-002 share a commit** because they rewrite the same function; splitting would
  have landed a docstring describing unimplemented behavior.
- **PSCR-004 and PSCR-007 each had a first draft that was wrong and was corrected before commit.**
  PSCR-004's guard began substring-based and would have flagged the very docstrings it guards;
  PSCR-007's began matching `atomic_write_text` only on bare names, making it weaker than the scan
  it replaced. Both are AST-based with negative tests pinning the detector.
- **Every new regression test was verified to discriminate**, not merely to pass: PSCR-005's leak
  test fails against the pre-fix ordering on the descriptor count, and PSCR-006's mutation test
  fails against a deliberately neutered contract. This is the standard the review criticised
  SWPH-004 for missing.

---

## Definition of done

- [x] Bundled Docker build context resolves correctly under the recorded policy, in a source checkout
      and an installed distribution, or is documented as checkout-only with a non-blocking degraded
      path.
- [x] A present configured image is sufficient to validate the Docker step.
- [x] A regression test covers the installed-layout case without depending on the development layout.
- [x] No `Path.cwd()` remains in the setup package.
- [x] No layout-dependent build-context arithmetic remains in the production package.
- [x] Workspace absence/count assertions are scoped to the running context's namespace.
- [x] The lock fail-closed path leaks no descriptor and creates no lock file.
- [x] Both Git-policy mutation tests exercise the real contract.
- [x] The presentation ownership guard is parse-based.
- [x] Existing setup/config safety invariants remain intact.
- [x] Public MCP contract remains unchanged unless deliberately versioned.
- [x] Exact-head implementation CI is green. — run `35278709124` on `0c77526`.
- [x] Exact-head guarded merge is complete. — **not applicable**: direct-to-`master` series.
- [x] Post-merge `master` CI is green. — same run; the push is the exact-head run.
- [x] Exact closure evidence is recorded in this file.

**Final acceptance evidence (2026-09-17)**

- Exact head `0c7752600950a693dc276fe93b0c209dfb000679`; CI run `35278709124`, job
  `105395410852`, conclusion **success**. Ruff format PASS; Ruff lint PASS; strict mypy PASS; MCP
  exported-schema contract PASS; `contract_version` history PASS; `docs/TOOLS.md` drift PASS; full
  Docker-backed pytest `1423 passed in 730.74s`, `0 warnings`; coverage `88%` (`9327` statements /
  `1124` misses).
- CI's numbers match the local closure run exactly (`1423 passed`, `88%`, `9327`/`1124`), so the
  two are corroborating rather than merely both green.
- Public MCP tool/schema contract unchanged throughout; `contract_version` stays `0.10.3`.
- This final status update is documentation-only and changes no code, so the CI evidence above
  covers the complete implementation of `PSCR-001` through `PSCR-009`.

**PSCR-010 acceptance evidence (2026-09-17)**

- PSCR-010 was added after that closure, on request, to fix the namespace-blind assertions this
  series had only *recorded* as a future task. Exact head
  `ec3600c`; CI run `35282113953`, job `105406174784`, conclusion **success**: all gates plus the
  full Docker-backed suite, `1426 passed in 719.03s`, coverage `88%` (`9327` statements / `1121`
  misses).
- Local run on an idle host agreed: `1426 passed in 662.09s`, zero failures, zero skips.
**PSCR-010 concurrency proof (2026-09-17)**

The evidence above is serial, and PSCR-010's actual claim is that *concurrent* runs no longer
collide. That claim was subsequently demonstrated rather than left inferred.

- **Experiment 1 — the property holds.** Two `tests/workspace` sessions run concurrently from the
  same tree, started five seconds apart and overlapping almost entirely (`586.51s` and `583.47s`):
  **`438 passed` and `438 passed`**, zero failures.
- The collision condition was captured live mid-run: two containers for the *same logical
  workspace* coexisting, separated only by namespace —
  `mcp-chatgpt-sandbox-ws-example-t56505647df114100-e01c0ce4` and
  `mcp-chatgpt-sandbox-ws-example-t1d24f9b501c71c24-a7326e1e`. Both carry
  `LABEL_WORKSPACE_ID=ws_example`, which is exactly what a namespace-blind absence assertion
  matches.
- **Experiment 2 — the fix is what causes it.** A `git worktree` detached at `da3d7bf`
  (pre-PSCR-010) was run concurrently against the post-fix tree: same host, same contention, same
  suite, differing only in assertion code. Pre-fix: **`6 failed, 429 passed`** (`594.30s`).
  Post-fix: **`438 passed`** (`590.95s`).
- The correspondence is exact. Every one of the six pre-fix failures is in a file PSCR-010
  converted — `test_project_config_integration.py` (2), `test_remote_protection.py` (2),
  `test_recovery.py` (1), `test_resources.py` (1) — and nothing failed elsewhere. That rules out
  generic flakiness: the failures track the converted assertions and nothing else.
- Two honest caveats on this evidence. Both counterfactual runs were piped through `tail`, so the
  recorded `exit code 0` for the pre-fix run is *tail's* status, not pytest's — the summary line
  is the authority, and it reports six failures. The same truncation discarded the tracebacks, so
  the individual `assert [<Container: ...>] == []` shapes were not captured on this run; the
  failure counts and the file-level correspondence are what was directly observed, with the
  assertion shape known from the earlier CI evidence recorded under PSCR-003.
- The scenario originally proposed — a local run against a live CI run — was not used. CI was idle
  when the proof was run, and two local contexts reproduce the same property under full control
  without risking a red `master` run.
