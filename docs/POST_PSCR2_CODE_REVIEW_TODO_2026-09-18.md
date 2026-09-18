# Post-PSCR2 Code Review TODO

**Status:** Open — `PSCR3-001` through `PSCR3-007` not started  
**Date:** 2026-09-18  
**Spec:** `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1`  
**Task series:** `PSCR3-001` through `PSCR3-007`  
**Validation series:** `PSCR3V-*`

This TODO implements `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`. A re-read of the merged
`PSCR2-001`–`PSCR2-011` work found no production defect. It found residue in that series' own record
and test scaffolding: a specification describing a rejected design, unverified line citations, a
guard with unstated boundaries, a fixture that cannot detect a rename, and two safe-but-undocumented
behaviours. No task here changes what the software does.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Follow the project's commit convention: direct conventional commits to `master`, no branch or PR.
- [ ] Make no production behavior change. Test code and documentation only.
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

- [ ] Rewrite PSCR2-002 Option B in `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md` to describe the
      implemented mechanism: skip when the running module lies under the checkout's `src/`, otherwise
      compare the installed distribution's metadata version.
- [ ] State, in that passage, that the original `__version__` wording was found false during
      implementation, and point to the TODO's deviation note.
- [ ] Change PSCR2-009's "all four locations" to three, and say why not four.
- [ ] Leave the PSCR2 TODO's deviation notes unchanged.

**Validation — PSCR3V-001**

- [ ] Grep: the specification no longer instructs comparing `mcp_chatgpt_sandbox.__version__`.
- [ ] Grep: no "all four locations" remains for the live-server caveat.
- [ ] Both corrections read as corrections, naming PSCR3-001.

### PSCR3-002 — Replace unverified line citations with verified, durable ones

**Goal:** no citation in the PSCR2 TODO is presented as current while being wrong.

- [ ] Replace `manager.py:213` with the symbol (`DockerWorkspaceManager.reconcile`, the
      `container.labels` read) and a commit-qualified line number.
- [ ] Replace `inventory.py:283` likewise (`getattr(container, "labels", {})`).
- [ ] Replace `manager_core.py:148` likewise (`_CoreMixin._find_container`'s namespace check).
- [ ] Verify each number with a command run at the cited commit, and record the command's output.
- [ ] Leave the explicitly historical `manager_core.py:197` / `:200` citations alone.

**Validation — PSCR3V-002**

- [ ] Recorded command output matches every number written.
- [ ] Each corrected citation names its symbol and its commit.

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
- [ ] No production code path changed.

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
- [ ] Public MCP contract confirmed unchanged by re-export and diff.
- [ ] Exact-head CI is green.
- [ ] Exact closure evidence is recorded in this file.
