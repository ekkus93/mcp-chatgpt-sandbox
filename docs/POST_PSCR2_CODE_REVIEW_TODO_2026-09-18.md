# Post-PSCR2 Code Review TODO

**Status:** In progress — `PSCR3-001`–`PSCR3-002` closed; `PSCR3-003` next  
**Date:** 2026-09-18  
**Spec:** `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1`  
**Task series:** `PSCR3-001` through `PSCR3-009`  
**Validation series:** `PSCR3V-*`

This TODO implements `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`. A re-read of the merged
`PSCR2-001`–`PSCR2-011` work found no production defect. It found residue in that series' own record
and test scaffolding: a specification describing a rejected design, unverified line citations, a
guard with unstated boundaries, a fixture that cannot detect a rename, and two safe-but-undocumented
behaviours. A second review pass added two more: the function the version-skew check rests on is
never executed by any test, and project-name matching is exact rather than normalised. No task here
changes what the software does, with one recorded exception — PSCR3-009, if its Option A is chosen.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Follow the project's commit convention: direct conventional commits to `master`, no branch or PR.
- [ ] Make no production behavior change, except PSCR3-009 Option A if chosen (accepts equivalent
      spellings of this project's own name, and nothing else).
- [ ] Preserve every PSCR and PSCR2 outcome.
- [ ] Correct the historical record visibly, never silently.
- [ ] Cite code by symbol; qualify any line number with the commit it was verified at, and verify it
      with a command, not from memory.
- [ ] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [ ] Add a regression test for every behavioral claim, and prove it discriminates.
- [ ] Do not weaken existing tests or guards. Do not bump `contract_version`.
- [ ] Run `--cov` suites on an idle host; non-coverage runs may overlap CI.
- [ ] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — Make the PSCR2 record true

### PSCR3-001 — Reconcile the PSCR2 specification with what was built

**Goal:** a reader of the PSCR2 specification alone learns the design that was implemented.

- [x] Rewrite PSCR2-002 Option B in `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md` to describe the
      implemented mechanism: skip when the running module lies under the checkout's `src/`, otherwise
      compare the installed distribution's metadata version.
- [x] State, in that passage, that the original `__version__` wording was found false during
      implementation, and point to the TODO's deviation note.
- [x] Change PSCR2-009's "all four locations" to three, and say why not four.
- [x] Leave the PSCR2 TODO's deviation notes unchanged.
- [x] Update the PSCR2 TODO's introduction to say what PSCR2-009→011 added, and that they were added
      after the original closure.
- [x] Add one Definition-of-done entry to the PSCR2 TODO per post-closure outcome (live-server
      isolation demonstrated; production `sparse=False` pinned; shadowed `reconcile()` removed).
- [x] Reword no existing PSCR2 entry or evidence.

**Validation — PSCR3V-001**

- [x] Grep: the specification no longer instructs comparing `mcp_chatgpt_sandbox.__version__`.
- [x] Grep: no "all four locations" remains for the live-server caveat.
- [x] Both corrections read as corrections, naming PSCR3-001.
- [x] The PSCR2 TODO's introduction and Definition of done cover PSCR2-009→011.

**Closure evidence (2026-09-18)**

- `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md` PSCR2-002 Option B now describes the implemented
  mechanism — skip when the running module lies under the checkout's `src/`, otherwise compare the
  installed distribution's metadata version, failing closed on mismatch or an undeterminable
  version. An italic correction note names PSCR3-001, quotes what the passage originally claimed,
  says why it was false, and points to the TODO's deviation note.
- PSCR2-009's closing sentence now says "every location that carries it", with a correction note
  naming the three locations and why `CLAUDE.md` was never one.
- The PSCR2 TODO gained a marked paragraph after its introduction describing PSCR2-009→011 and that
  they were added after the original closure, and three marked Definition-of-done entries, one per
  post-closure outcome. **No existing entry, evidence, or deviation note was reworded**; every
  addition is labelled "added by PSCR3-001" so it cannot be mistaken for original text.
- Greps: the specification no longer instructs comparing the running
  `mcp_chatgpt_sandbox.__version__` (0 matches outside the correction note), and "removed from all
  four locations" no longer appears (0 matches).
- Documentation only. Acceptance-coverage audit and `docs/TOOLS.md` drift guard: `18 passed`. No
  full suite run: nothing executable changed.

### PSCR3-002 — Replace unverified line citations with verified, durable ones

**Goal:** no citation in the PSCR2 TODO is presented as current while being wrong.

- [x] Replace `manager.py:213` with the symbol (`DockerWorkspaceManager.reconcile`, the
      `container.labels` read) and a commit-qualified line number.
- [x] Replace `inventory.py:283` likewise (`getattr(container, "labels", {})`).
- [x] Replace `manager_core.py:148` likewise (`_CoreMixin._find_container`'s namespace check).
- [x] Verify each number with a command run at the cited commit, and record the command's output.
- [x] Leave the explicitly historical `manager_core.py:197` / `:200` citations alone.

**Validation — PSCR3V-002**

- [x] Recorded command output matches every number written.
- [x] Each corrected citation names its symbol and its commit.

**Closure evidence (2026-09-18)**

- The three citations in `docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md` now name the symbol and give
  a line number qualified by commit `68a37fd` — the last commit that changed any of the three files
  (`git diff --stat 68a37fd HEAD` over them is empty). Each carries a visible correction note saying
  what it originally said and why that was wrong.
- **Line numbers verified by command, output recorded.** `git show 68a37fd:<file> | grep -nF <pattern>`:

  ```text
  manager.py:       218:                container.labels,
  inventory.py:     288:                labels = dict(getattr(container, "labels", {}) or
  manager_core.py:  145:            if not container_matches_test_namespace(container.labe
  ```

- **Symbols verified by command too.** An AST walk resolving the innermost enclosing definition of
  each line gave `DockerWorkspaceManager.reconcile`, `ResourceInventoryService._collect_containers`,
  and `_CoreMixin._find_container`. This was checked because the first draft of this very correction
  named the inventory class from memory — the habit this task exists to end. The name happened to
  be right; it is now cited to the method, from output.
- `manager_core.py:200` is kept and explicitly marked historical, since the body it pointed into
  was deleted by PSCR2-011. The `:197`/`:200` citations elsewhere in that file were already framed
  as historical and are unchanged.
- Documentation only; guards `18 passed`.

---

## Phase 2 — Test scaffolding fidelity

### PSCR3-003 — Record the `sparse=False` guard's boundaries

**Goal:** the guard's limits are documented and pinned, not left to be rediscovered.

- [ ] Record in `_unpinned_container_lists()`'s docstring the evasion: an aliased collection
      (`cs = x.containers; cs.list(...)`) is not flagged.
- [ ] Record the false-positive class: `**opts` and `sparse=<variable>` are flagged even when pinned.
- [ ] State that neither shape exists in the production package today.
- [ ] State why neither is fixed: data-flow analysis is out of an AST walk's reach, and treating
      `**opts` as pinned would be unsound.
- [ ] Add assertions to the negative test pinning both boundaries as known behavior.
- [ ] No detector change. (Worded under VR-000b for the recording; closing the evasion is declined.)

**Validation — PSCR3V-003**

- [ ] `tests/test_docker_list_sparse_guard.py` remains green with the added assertions.
- [ ] Recorded honestly: these assertions pin current behavior, so they pass before and after by
      design and are not presented as discrimination.

### PSCR3-004 — Make the version-pin fixture rename-safe

**Goal:** renaming `_installed_version` fails loudly instead of silently unpinning the module.

- [ ] Keep `raising=False`, and keep its docstring rationale.
- [ ] Assert in the fixture that `docker_setup._installed_version` exists whenever
      `docker_setup._check_context_matches_installed_version` exists.
- [ ] Explain the conditional in the fixture's docstring: it is what keeps pre-PSCR2-002
      discrimination runs possible.

**Validation — PSCR3V-004**

- [ ] Simulated rename (the check present, `_installed_version` absent) fails fixture setup.
- [ ] The module still runs — without fixture errors — against pre-PSCR2-002 `docker_setup.py`.
- [ ] The module is green against current code.

### PSCR3-005 — Document the production-labelled test container

**Goal:** a reader need not work out for themselves why the test is safe on a shared daemon.

- [ ] State in the second live-server test's docstring that it creates a production-labelled
      container (managed, no namespace) on the shared daemon, and how briefly.
- [ ] State the three reasons it is safe: per-run repository id, no workspace row in any real
      server's database, exact-resource teardown (HR-026).
- [ ] No code change.

**Validation — PSCR3V-005**

- [ ] Docstring review confirms the exposure and all three reasons.
- [ ] `tests/workspace/test_pscr2_live_server_isolation.py` remains green.

### PSCR3-006 — Exercise the startup sweep's dispatch to the override

**Goal:** CI, not server startup, is where a lost `reconcile()` override would be found.

- [ ] Add a Docker-free test: a `DockerWorkspaceManager` with one workspace row and a fake Docker
      client runs `reconcile_all()`.
- [ ] Assert the concrete `reconcile()` ran for that workspace.
- [ ] Assert the `_CoreMixin` declaration was never reached (no `NotImplementedError`).
- [ ] Record that `reconcile_all()` catches only `ContainerRuntimeFailedError`, so the failure mode
      without the override is loud, not a silently skipped workspace.

**Validation — PSCR3V-006**

- [ ] Discrimination: with the override removed from `DockerWorkspaceManager`, the test fails.
- [ ] `tests/workspace/test_pscr2_reconcile_declaration.py` remains green.

### PSCR3-008 — Test `_installed_version()` itself

**Goal:** the function the skew check rests on is executed by tests, on both branches.

Added after a second review pass measured its coverage at zero.

- [ ] Add a test that bypasses the autouse pin and asserts the real function returns the version
      `importlib.metadata` reports for this distribution.
- [ ] Add a test that makes the lookup raise `PackageNotFoundError` and asserts `None`.
- [ ] Keep the bypass explicit and local; the pin stays autouse for every other test.
- [ ] Do not weaken PSCR3-004's rename assertion.

**Validation — PSCR3V-008**

- [ ] A recorded coverage run shows `_installed_version()` fully covered, up from zero.
- [ ] The rest of `tests/setup/test_pscr_docker_context_policy.py` remains green.

### PSCR3-009 — Dispose of exact project-name matching

**Goal:** restyling this project's name cannot silently break its own checkout search.

Added after a second review pass.

- [ ] Choose Option A (normalise per PEP 503) or Option B (keep exact, on the record); record it.
- [ ] If Option A: normalise both sides — lower-case; runs of `-`, `_`, `.` collapsed to `-`.
- [ ] If Option A: confirm a different project's name is still rejected after normalisation.
- [ ] If Option B: state in `_source_checkout_root()`'s docstring that matching is deliberately
      exact, the symptom of a restyled name, and where to change it.

**Validation — PSCR3V-009**

- [ ] Test (if A): `mcp_chatgpt_sandbox` and `MCP-ChatGPT-Sandbox` spellings are accepted.
- [ ] Test (if A): PSCR2-001's wrong-project decoy tests still pass unchanged.
- [ ] Discrimination (if A): the equivalent-spelling test fails against the exact comparison.
- [ ] Docstring review (if B) confirms the three statements.

---

## Phase 3 — Final acceptance and closure

### PSCR3-007 — Final regression, contract, and closure

**Goal:** close only after every touched file is re-verified.

#### Final code-review checklist

- [ ] The PSCR2 specification describes the implemented PSCR2-002 mechanism, visibly corrected.
- [ ] The PSCR2 specification says three locations for PSCR2-009.
- [ ] The three line citations name symbols and carry verified, commit-qualified numbers.
- [ ] The `sparse=False` guard's boundaries are recorded and pinned.
- [ ] A rename of `_installed_version` fails the fixture; pre-fix runs still work.
- [ ] The live-server test documents its production-labelled container.
- [ ] `reconcile_all()` is shown to dispatch to the override.
- [ ] The PSCR2 TODO's frame reflects PSCR2-009→011.
- [ ] `_installed_version()` is covered on both branches.
- [ ] Project-name matching has its recorded disposition.
- [ ] No production code path changed, other than PSCR3-009 Option A if chosen.

#### Contract/version decision

- [ ] Re-export schemas and diff against `schemas/tools.json`.
- [ ] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCR3V-007

- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy src` passes in strict mode.
- [ ] MCP exported-schema contract check passes.
- [ ] `docs/TOOLS.md` drift gate and acceptance-coverage audit pass.
- [ ] Full Docker-backed suite passes; record whether it was a `--cov` run and whether the host was idle.
- [ ] Record exact pytest count and coverage total/statements/misses.
- [ ] Record exact implementation head SHA.
- [ ] Record exact CI run ID and job ID.
- [ ] Post-push `master` CI passes on the exact head SHA.
- [ ] Update this TODO status to complete only after post-push evidence is green.

---

## Definition of done

- [ ] The PSCR2 specification describes the mechanism PSCR2-002 built and says three locations for
      PSCR2-009, both visibly corrected.
- [ ] The three line citations name their symbols and carry verified, commit-qualified numbers.
- [ ] The `sparse=False` guard's boundaries are recorded in its docstring and pinned by assertions.
- [ ] A rename of `_installed_version` fails the version-pin fixture loudly, and the module still
      runs against pre-PSCR2-002 code.
- [ ] The live-server test documents its production-labelled container and why it is safe.
- [ ] A test shows `reconcile_all()` dispatches to the concrete override, and fails without it.
- [ ] The PSCR2 TODO's introduction and Definition of done reflect PSCR2-009→011.
- [ ] `_installed_version()` is executed by tests on both branches, shown by coverage.
- [ ] Exact project-name matching has a recorded disposition, implemented per the chosen option.
- [ ] Public MCP contract confirmed unchanged by re-export and diff.
- [ ] Exact-head CI is green.
- [ ] Exact closure evidence is recorded in this file.
