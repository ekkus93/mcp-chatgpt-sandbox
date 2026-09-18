# Post-PSCR2 Code Review TODO

**Status:** Closing — `PSCR3-001`–`PSCR3-010` implemented; `PSCR3-010` awaiting exact-head CI  
**Date:** 2026-09-18  
**Spec:** `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1`  
**Task series:** `PSCR3-001` through `PSCR3-010`  
**Validation series:** `PSCR3V-*`

This TODO implements `docs/POST_PSCR2_CODE_REVIEW_SPEC_2026-09-18.md`. A re-read of the merged
`PSCR2-001`–`PSCR2-011` work found no production defect. It found residue in that series' own record
and test scaffolding: a specification describing a rejected design, unverified line citations, a
guard with unstated boundaries, a fixture that cannot detect a rename, and two safe-but-undocumented
behaviours. A second review pass added two more: the function the version-skew check rests on is
never executed by any test, and project-name matching is exact rather than normalised. No task here
changes what the software does, with one recorded exception — PSCR3-009, if its Option A is chosen.

## Global rules

- [x] Start from an exact inspected `master` SHA. — `a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1`.
- [x] Follow the project's commit convention: direct conventional commits to `master`, no branch or PR.
- [x] Make no production behavior change, except PSCR3-009 Option A if chosen (accepts equivalent
      spellings of this project's own name, and nothing else).
- [x] Preserve every PSCR and PSCR2 outcome.
- [x] Correct the historical record visibly, never silently.
- [x] Cite code by symbol; qualify any line number with the commit it was verified at, and verify it
      with a command, not from memory.
- [x] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [x] Add a regression test for every behavioral claim, and prove it discriminates.
- [x] Do not weaken existing tests or guards. Do not bump `contract_version`.
- [x] Run `--cov` suites on an idle host; non-coverage runs may overlap CI.
- [x] Record exact SHA/run/job evidence as each phase closes.

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

- [x] Record in `_unpinned_container_lists()`'s docstring the evasion: an aliased collection
      (`cs = x.containers; cs.list(...)`) is not flagged.
- [x] Record the false-positive class: `**opts` and `sparse=<variable>` are flagged even when pinned.
- [x] State that neither shape exists in the production package today.
- [x] State why neither is fixed: data-flow analysis is out of an AST walk's reach, and treating
      `**opts` as pinned would be unsound.
- [x] Add assertions to the negative test pinning both boundaries as known behavior.
- [x] No detector change. (Worded under VR-000b for the recording; closing the evasion is declined.)

**Validation — PSCR3V-003**

- [x] `tests/test_docker_list_sparse_guard.py` remains green with the added assertions.
- [x] Recorded honestly: these assertions pin current behavior, so they pass before and after by
      design and are not presented as discrimination.

**Closure evidence (2026-09-18)**

- `_unpinned_container_lists()`'s docstring records both boundaries as **recorded, not fixed**: the
  aliased-collection evasion, and the `**opts` / `sparse=<variable>` false positives. It says why
  neither is fixed (data-flow analysis is out of an AST walk's reach; treating an opaque argument as
  pinned would defeat a guard whose point is a *visible* pin), that neither shape exists in
  production today, and what to do if one appears: write the call in the literal shape.
- The negative test gained three assertions pinning those boundaries as known behavior.
- **Stated plainly, as the spec required:** these assertions describe the detector's *current*
  behavior, so they pass before and after this task by design. They guard against an unnoticed
  future change to the detector; they are not a discrimination proof and are not presented as one.
- No detector change. **VR-000b:** every checkbox above is worded for the recording, which is done;
  closing the evasion is declined, and no checkbox claims it.
- `tests/test_docker_list_sparse_guard.py`: `2 passed`. Ruff lint PASS.

### PSCR3-004 — Make the version-pin fixture rename-safe

**Goal:** renaming `_installed_version` fails loudly instead of silently unpinning the module.

- [x] Keep `raising=False`, and keep its docstring rationale.
- [x] Assert in the fixture that `docker_setup._installed_version` exists whenever
      `docker_setup._check_context_matches_installed_version` exists.
- [x] Explain the conditional in the fixture's docstring: it is what keeps pre-PSCR2-002
      discrimination runs possible.

**Validation — PSCR3V-004**

- [x] Simulated rename (the check present, `_installed_version` absent) fails fixture setup.
- [x] The module still runs — without fixture errors — against pre-PSCR2-002 `docker_setup.py`.
- [x] The module is green against current code.

**Closure evidence (2026-09-18)**

- The autouse fixture now asserts `docker_setup._installed_version` exists **whenever**
  `docker_setup._check_context_matches_installed_version` exists, before applying the pin.
  `raising=False` and its rationale are kept. A comment explains both halves: `raising=False`
  *creates* a missing attribute, so alone it would silently unpin the module on a rename; and the
  assertion is conditional because against pre-PSCR2-002 code neither name exists and that run must
  stay possible.
- Three cases verified:
  1. Current code: `12 passed`.
  2. Simulated rename (the check present, `_installed_version` → `_dist_version`): **`12 errors`**,
     every test failing in fixture setup with "docker_setup._installed_version is gone but the
     version check remains".
  3. Pre-PSCR2-002 source (`docker_setup.py` from `7f13beb`): `3 failed, 9 passed`, **zero errors** —
     the three failures are the expected discrimination failures, and no test is blocked by the
     fixture.
- **The first simulation of case 2 was invalid, and is recorded.** It used
  `sed s/_installed_version/_dist_version/g`, which also renamed
  `_check_context_matches_installed_version` — that name *contains* the substring — so the
  assertion's precondition went false and it was skipped (`1 failed, 11 passed`). That was a flaw
  in the simulation, not the fixture; redone with a word-boundary pattern it behaves as designed.
  The substring relationship between the two names is worth knowing: a careless rename of one can
  take the other with it.
- `docker_setup.py` was restored byte-identical to `HEAD` after each case. Test-only change; Ruff
  lint PASS.

### PSCR3-005 — Document the production-labelled test container

**Goal:** a reader need not work out for themselves why the test is safe on a shared daemon.

- [x] State in the second live-server test's docstring that it creates a production-labelled
      container (managed, no namespace) on the shared daemon, and how briefly.
- [x] State the three reasons it is safe: per-run repository id, no workspace row in any real
      server's database, exact-resource teardown (HR-026).
- [x] No code change.

**Validation — PSCR3V-005**

- [x] Docstring review confirms the exposure and all three reasons.
- [x] `tests/workspace/test_pscr2_live_server_isolation.py` remains green.

**Closure evidence (2026-09-18)**

- The second live-server test's docstring now states what it puts on a shared daemon — a container
  carrying `managed=true` and no namespace label, indistinguishable by label from a real workspace,
  which a live `serve` could therefore list in `resource_inventory` — and the three reasons that is
  safe.
- **Each factual claim was checked rather than asserted.** Duration: `--durations` reports the test's
  call phase at `0.19s`, so the docstring says "about 0.2s measured" rather than the spec's looser
  "well under a second". Database: the `db` fixture is `open_db(":memory:")`, so the workspace row
  cannot exist in any real server's database. Teardown: after the run, zero containers with the
  `pscr2-` prefix remain.
- No code change. `tests/workspace/test_pscr2_live_server_isolation.py`: `2 passed`.

### PSCR3-006 — Exercise the startup sweep's dispatch to the override

**Goal:** CI, not server startup, is where a lost `reconcile()` override would be found.

- [x] Add a Docker-free test: a `DockerWorkspaceManager` with one workspace row and a fake Docker
      client runs `reconcile_all()`.
- [x] Assert the concrete `reconcile()` ran for that workspace.
- [x] Assert the `_CoreMixin` declaration was never reached (no `NotImplementedError`).
- [x] Record that `reconcile_all()` catches only `ContainerRuntimeFailedError`, so the failure mode
      without the override is loud, not a silently skipped workspace.

**Validation — PSCR3V-006**

- [x] Discrimination: with the override removed from `DockerWorkspaceManager`, the test fails.
- [x] `tests/workspace/test_pscr2_reconcile_declaration.py` remains green.

**Closure evidence (2026-09-18)**

- Two Docker-free tests added to `tests/workspace/test_pscr2_reconcile_declaration.py`, sharing a
  helper that builds a real `DockerWorkspaceManager` over an in-memory database with one workspace
  row and a fake Docker client that finds nothing and records each `containers.list()` call.
- `test_pscr3v_006_reconcile_all_dispatches_to_the_concrete_override` asserts the sweep returns the
  workspace — impossible had the declaration been reached, since it raises — **and** that the
  recorded Docker query filtered on `workspace-id=ws_example`, which shows *which* body ran: only the
  concrete `reconcile()` lists containers by workspace label.
- `test_pscr3v_006_a_lost_override_fails_the_sweep_loudly` removes the override with
  `monkeypatch.delattr` and asserts `reconcile_all()` raises `NotImplementedError`. This pins the
  claim the task was asked to record: `reconcile_all()` catches only `ContainerRuntimeFailedError`,
  so a lost override is a loud startup failure, not a silently "skipped" workspace. Were the sweep
  ever to catch more broadly, this test fails.
- **Discrimination verified.** With the override renamed away in `workspace/manager.py`, the dispatch
  test fails. (In that same run the lost-override test also fails, but only because its own
  `delattr` then has nothing to remove — an artifact of the experiment, recorded so it is not
  mistaken for a second proof.) `manager.py` was restored byte-identical to `HEAD`.
- Test-only change. Strict mypy PASS; Docker-free suite plus guards `696 passed` (this figure was first
  written as 705 before the run finished, and corrected from the actual output). The full
  Docker-backed suite runs at closure.

### PSCR3-008 — Test `_installed_version()` itself

**Goal:** the function the skew check rests on is executed by tests, on both branches.

Added after a second review pass measured its coverage at zero.

- [x] Add a test that bypasses the autouse pin and asserts the real function returns the version
      `importlib.metadata` reports for this distribution.
- [x] Add a test that makes the lookup raise `PackageNotFoundError` and asserts `None`.
- [x] Keep the bypass explicit and local; the pin stays autouse for every other test.
- [x] Do not weaken PSCR3-004's rename assertion.

**Validation — PSCR3V-008**

- [x] A recorded coverage run shows `_installed_version()` fully covered, up from zero.
- [x] The rest of `tests/setup/test_pscr_docker_context_policy.py` remains green.

**Closure evidence (2026-09-18)**

- `tests/setup/test_pscr_docker_context_policy.py` captures the real function at import —
  `_REAL_INSTALLED_VERSION = getattr(docker_setup, "_installed_version", None)` — before the autouse
  pin replaces the module attribute. The bypass is explicit and local; the pin stays autouse for
  every other test.
- Two tests call the captured function: one asserts it equals
  `importlib.metadata.version("mcp-chatgpt-sandbox")`; one makes that lookup raise
  `PackageNotFoundError` and asserts `None`.
- **Coverage, from a recorded run.** `--cov-branch` over this module alone: before this task
  `_installed_version()`'s lines `157-160` were listed as missing; after it they are absent from the
  missing list. The function went from zero to complete on both branches.
- **PSCR3-004 not weakened.** The rename simulation now gives `14 errors` — every test, including
  the two new ones, fails in fixture setup. `getattr` rather than attribute access is what keeps the
  module importable against pre-PSCR2-002 source, where the function does not exist: that run gives
  `5 failed, 9 passed` with **zero errors** (the two new tests fail there on `is not None`, which is
  correct — there is no function to test).
- Test-only change. Ruff lint PASS; module `14 passed`.

### PSCR3-009 — Dispose of exact project-name matching

**Goal:** restyling this project's name cannot silently break its own checkout search.

Added after a second review pass.

- [x] Choose Option A (normalise per PEP 503) or Option B (keep exact, on the record); record it.
- [x] If Option A: normalise both sides — lower-case; runs of `-`, `_`, `.` collapsed to `-`.
- [x] If Option A: confirm a different project's name is still rejected after normalisation.
- [x] If Option B: state in `_source_checkout_root()`'s docstring that matching is deliberately
      exact, the symptom of a restyled name, and where to change it.

**Validation — PSCR3V-009**

- [x] Test (if A): `mcp_chatgpt_sandbox` and `MCP-ChatGPT-Sandbox` spellings are accepted.
- [x] Test (if A): PSCR2-001's wrong-project decoy tests still pass unchanged.
- [x] Discrimination (if A): the equivalent-spelling test fails against the exact comparison.
- [x] Docstring review (if B) confirms the three statements.

**Decision and evidence (2026-09-18)**

- **Option A selected — normalise per PEP 503.** The rule is standard, the code is a few lines, and
  it removes a cliff whose symptom ("ships only in a source checkout", reported from inside one)
  points away from its cause. This is the series' one recorded production change.
- `_normalized_project_name()` lower-cases and collapses runs of `-`, `_`, `.` to `-`, returning
  `None` for a non-string. `_source_checkout_root()` compares that against `_PROJECT_NAME`, which is
  already in normalised form. Every checkout accepted before is still accepted: normalising the
  exact string yields itself.
- Seven tests added (three parametrised functions). Equivalent spellings `mcp_chatgpt_sandbox`,
  `MCP-ChatGPT-Sandbox`, and `mcp.chatgpt--sandbox` are accepted. Near-misses
  `mcp-chatgpt-sandbox-extra`, `chatgpt-sandbox`, and `mcpchatgptsandbox` stay rejected, so
  normalising separators did not become fuzzy matching. A malformed `name = 42` disqualifies the
  candidate rather than crashing `.lower()`.
- **Discrimination verified.** Against the exact comparison restored from `HEAD`, all three
  equivalent-spelling cases fail; the near-miss and non-string cases pass against both versions,
  which is correct — they guard against over-reach, not against the old behavior. PSCR2-001's
  wrong-project decoy tests pass unchanged.
- Full Docker-backed suite: `1455 passed in 644.91s`, zero failures, zero skips; Ruff format, Ruff
  lint, and strict mypy PASS. A non-coverage run, started while **six** CI runs were active on the
  shared runner, with no collision — further corroboration of PSCR-010, recorded per the global
  rules.

### PSCR3-010 — Reconcile three stale checkboxes in older TODOs

**Goal:** no unticked box in `docs/` carries a reason later work has overtaken.

Added after closure, on request.

- [x] Verify each of the three boxes against the code before touching it.
- [x] Tick `HARDENING_TODO.md`'s collision-half box, crediting both G3R-015 and PSCR-010.
- [x] Tick `TODO.md`'s disk-usage/prune box, crediting O2–O8, and record the container-size gap.
- [x] Leave `TODO.md`'s job-promotion box unticked; correct its stale rationale.
- [x] Correct the same stale rationale in `execution/service.py`'s docstring.
- [x] Leave original wording and the explanatory paragraphs beneath each entry as written.

**Validation — PSCR3V-010**

- [x] Every cited task id and commit was looked up, not recalled.
- [x] Guards and gates stay green; no behavior change.
- [ ] Exact-head CI green. — recorded at closure.

**Closure evidence (2026-09-18)**

- **Verified before annotating, and the verification changed one answer.** Reading
  `HARDENING_TODO.md`'s own "Not fixed" paragraph showed its "collision half" is the `409 Conflict`
  from two processes racing on the deterministic container *name*. That was removed by G3R-015's
  namespaced `container_name()`, not by PSCR-010, which fixed a different layer (label-only
  assertions). The first draft of this task credited PSCR-010 alone.
- `promoted_to_job` was confirmed absent: zero matches in `schemas/tools.json`, and its only match
  in `src/` is the docstring saying it is not implemented. `docs/SPEC.md` §14.4 was read to confirm
  promotion is a MAY.
- Prune and inventory were confirmed from the exported contract (`resource_inventory`,
  `resource_prune_plan`, `resource_prune_execute`), and the container-size gap from
  `operations/inventory.py`'s `ByteObservation.unavailable("container size not measured by O2")`.
- Cited ids were looked up: O2/O3/O4/O5–O8 from `docs/POST_MVP_OPERATIONS_TODO.md`'s headings, and
  PSCR-010's commit `ec3600c` from `git log`.
- Each annotation is dated, names PSCR3-010, and says whether the box was ticked because the work
  was done elsewhere or left unticked because it was not. No original wording was changed.
- One `src/` file touched, docstring only: `execution/service.py`. Ruff format, Ruff lint, strict
  mypy PASS.

---

## Phase 3 — Final acceptance and closure

### PSCR3-007 — Final regression, contract, and closure

**Goal:** close only after every touched file is re-verified.

#### Final code-review checklist

- [x] The PSCR2 specification describes the implemented PSCR2-002 mechanism, visibly corrected.
- [x] The PSCR2 specification says three locations for PSCR2-009.
- [x] The three line citations name symbols and carry verified, commit-qualified numbers.
- [x] The `sparse=False` guard's boundaries are recorded and pinned.
- [x] A rename of `_installed_version` fails the fixture; pre-fix runs still work.
- [x] The live-server test documents its production-labelled container.
- [x] `reconcile_all()` is shown to dispatch to the override.
- [x] The PSCR2 TODO's frame reflects PSCR2-009→011.
- [x] `_installed_version()` is covered on both branches.
- [x] Project-name matching has its recorded disposition.
- [x] No production code path changed, other than PSCR3-009 Option A if chosen.

#### Contract/version decision

- [x] Re-export schemas and diff against `schemas/tools.json`.
- [x] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCR3V-007

- [x] `uv run ruff format --check .` passes.
- [x] `uv run ruff check .` passes.
- [x] `uv run mypy src` passes in strict mode.
- [x] MCP exported-schema contract check passes.
- [x] `docs/TOOLS.md` drift gate and acceptance-coverage audit pass.
- [x] Full Docker-backed suite passes; record whether it was a `--cov` run and whether the host was idle. —
      a `--cov` run, on a verified-idle host.
- [x] Record exact pytest count and coverage total/statements/misses. — `1455 passed`; `88%`,
      `9348` statements / `1104` misses.
- [x] Record exact implementation head SHA. — `65a03f3`.
- [x] Record exact CI run ID and job ID. — run `35327544137`, job `105544027363`.
- [x] Post-push `master` CI passes on the exact head SHA. — run `35330475384`, job `105553422421`
      on `ae2021c`, **success**: `1455 passed`, `88%` (`9348` / `1104`).
- [x] Update this TODO status to complete only after post-push evidence is green.

---

**Closure evidence (2026-09-18)**

- Gates on `65a03f3`: Ruff format (324 files) PASS; Ruff lint PASS; strict mypy (103 files) PASS.
  Acceptance-coverage audit, `docs/TOOLS.md` drift guard, and schema-export guard: `27 passed`.
- Contract re-exported through CI's own command and diffed byte-for-byte against
  `schemas/tools.json`: **identical**. `contract_version` stays `0.10.3`; no bump was required.
- **One production file changed in the whole series**: `setup/docker_setup.py`, `+24/-2`, all of it
  PSCR3-009's name normalisation — the single exception the specification recorded.
  (`git diff --stat b3a9430 HEAD -- src/`.)
- Closure suite, `pytest --cov --cov-report=term-missing`, on a verified-idle host (CI drained at
  09:24:56Z, no runner process, 23 GiB available): `1455 passed in 668.96s`, coverage `88%` —
  `9348` statements / `1104` misses. Zero skips. Completed first time; the two earlier coverage
  kills in PSCR2-008 both overlapped CI's own coverage run, so this is consistent with — though not
  proof of — the idle-host guidance.
- Exact-head CI for the code: run `35327544137`, job `105544027363` on `65a03f3`, **success**, with
  the identical `1455 passed` and `9348`/`1104`/`88%`. `65a03f3` contains every change in this
  series; what follows it is this evidence.
- Six CI runs (`4e293d0`, `8fe2296`, `14d8017`, `50d6a2d`, `d1f72fb`, `df7081f`) were cancelled as
  superseded, on the operator's decision, to reach an idle host in ~25 minutes rather than ~95. Each
  of those tasks' local verification is recorded in its own entry. `01b1222` (PSCR3-001) ran and
  passed.

**Slips made and caught during this series, recorded rather than smoothed over**

- PSCR3-002: while correcting citations written from memory, a class name was written from memory.
  It was right, but was then verified by AST and cited to the method.
- PSCR3-004: the first rename simulation used a plain `sed`, which also renamed
  `_check_context_matches_installed_version` (it contains the substring) and so disabled the very
  assertion under test. Redone with a word-boundary pattern.
- PSCR3-006: a pass count (`705`) was written into evidence before the run finished. The real count
  was `696`; corrected from the output before commit.

---

## Definition of done

- [x] The PSCR2 specification describes the mechanism PSCR2-002 built and says three locations for
      PSCR2-009, both visibly corrected.
- [x] The three line citations name their symbols and carry verified, commit-qualified numbers.
- [x] The `sparse=False` guard's boundaries are recorded in its docstring and pinned by assertions.
- [x] A rename of `_installed_version` fails the version-pin fixture loudly, and the module still
      runs against pre-PSCR2-002 code.
- [x] The live-server test documents its production-labelled container and why it is safe.
- [x] A test shows `reconcile_all()` dispatches to the concrete override, and fails without it.
- [x] The PSCR2 TODO's introduction and Definition of done reflect PSCR2-009→011.
- [x] `_installed_version()` is executed by tests on both branches, shown by coverage.
- [x] Exact project-name matching has a recorded disposition, implemented per the chosen option.
- [x] Public MCP contract confirmed unchanged by re-export and diff.
- [x] Exact-head CI is green.
- [x] Exact closure evidence is recorded in this file.
