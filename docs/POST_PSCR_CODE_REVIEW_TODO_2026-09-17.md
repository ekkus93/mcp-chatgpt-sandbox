# Post-PSCR Code Review TODO

**Status:** Complete — `PSCR2-001` through `PSCR2-011` closed  
**Date:** 2026-09-17  
**Spec:** `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`  
**Reviewed baseline:** `134795429eae988f927e43ec97cc04c5f98b867a`  
**Task series:** `PSCR2-001` through `PSCR2-011`  
**Validation series:** `PSCR2V-*`

This TODO implements `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`. It closes one correctness gap,
four heuristic boundaries, and one falsified document found by adversarially re-reading the merged
`PSCR-001`–`PSCR-012` work. All twelve PSCR tasks were confirmed substantively implemented; these
are the residue, held to the same standard: every fix gets a regression test proven to discriminate,
every declined fix is recorded as declined, and no document claims more than the code does.

*(Added by PSCR3-001.)* That paragraph describes `PSCR2-001`–`PSCR2-008`, the scope at authoring.
Three tasks were added after the original closure, each on request: `PSCR2-009` demonstrated
live-server isolation on a real daemon, `PSCR2-010` pinned `sparse=False` at every production
`containers.list()` site, and `PSCR2-011` removed the shadowed, never-executed
`_CoreMixin.reconcile()` body that PSCR2-009 had uncovered.

## Global rules

- [x] Start from an exact inspected `master` SHA. — `134795429eae988f927e43ec97cc04c5f98b867a`.
- [x] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR. (This rule is stated correctly here; the PSCR TODO carried a `ralph/` branch rule from an
      older template that contradicted `CLAUDE.md`, and had to be corrected during closure.)
- [x] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [x] Preserve the PSCR-001 checkout-only Docker context policy; do not reopen Option A.
- [x] Preserve PSCR-003: a present configured image validates the Docker step without a context.
- [x] Preserve reviewed config-write semantics and the SWPH-006 lock design unchanged.
- [x] Preserve leaf-private directory semantics as documented in `docs/CONFIGURATION.md`.
- [x] Preserve G3R-015 namespacing and PSCR-010's scoped assertions; weaken no assertion.
- [x] Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- [x] Add a focused regression test for every correctness defect, and prove it discriminates by
      running it against the pre-fix code.
- [x] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [x] Do not weaken existing tests or architecture guards; a replacement must be strictly stronger.
- [x] Do not bump `contract_version` unless the public MCP contract actually changes.
- [x] Until `PSCR2-007` disposes of it, treat overlapping a local suite with a CI run as advisory-avoid:
      PSCR-010 made it safe, but the self-hosted runner is shared. If it happens, record it as
      corroborating evidence, not as a violation. — Followed: PSCR2-001 and PSCR2-002 overlapped CI
      and recorded it. The closure run, which could not complete while overlapping, was run on an
      idle host (see PSCR2-008).
- [x] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — Checkout resolution correctness

### PSCR2-001 — Make the checkout search project-specific

**Goal:** `_source_checkout_root()` returns only this project's checkout.

- [x] Parse the candidate `pyproject.toml` with `tomllib` and require
      `[project] name == "mcp-chatgpt-sandbox"`.
- [x] Skip a candidate with the right layout and the wrong name; continue the upward search.
- [x] Keep the search self-validating: the returned root still holds `docker/default/`.
- [x] Decide whether "found but foreign" needs a distinct operator message from "not found"; record
      the decision and its reasoning.
- [x] Tolerate an unreadable or malformed `pyproject.toml` by skipping that candidate, not by raising
      an unrelated parse error out of context resolution.
- [x] Keep `validate_default_image_build_context()` as the single normalization/refusal point.

**Validation — PSCR2V-001**

- [x] Test: resolution still succeeds from the real source checkout.
- [x] Test (decoy): a synthesized ancestor with `pyproject.toml` + `docker/default/` but the wrong
      `[project] name` is skipped, and the search reports no checkout.
- [x] Test: a decoy *below* the real root does not shadow it — the search reaches the correct
      project root.
- [x] Test: a malformed `pyproject.toml` on a candidate is skipped without an unrelated error.
- [x] Discrimination: the decoy test fails against the pre-PSCR2-001 search.
- [x] Existing `tests/setup/test_pscr_docker_context_policy.py` remains green.

**Closure evidence (2026-09-18)**

- `_source_checkout_root()` now accepts a candidate only when `_declared_project()` returns a
  `[project]` table whose `name` is `mcp-chatgpt-sandbox` (`_PROJECT_NAME`). A right-layout,
  wrong-name candidate is skipped with `continue`, so the search keeps climbing rather than
  stopping or failing. `tomllib` is new to `src/`; it is standard library.
- `_declared_project()` returns `None` for `OSError`, `UnicodeDecodeError`, `TOMLDecodeError`, and
  a missing or non-table `[project]`, so a bad file disqualifies only that one candidate.
- **Decision — one message, not two.** "Found but foreign" deliberately produces the same
  `SetupDockerError` as "not found". The operator's remedy is identical in both cases (build the
  image manually from a checkout, or select an existing image), so a second message would add a
  distinction with no action behind it.
- Three tests added to `tests/setup/test_pscr_docker_context_policy.py`: a right-layout/wrong-name
  decoy is skipped; a decoy *nearer* to the module than the real root does not shadow it; and a
  malformed `pyproject.toml` on a nearer candidate is skipped rather than raised.
- **Discrimination verified.** Against the pre-PSCR2-001 search restored from `HEAD`, all three new
  tests fail and the four pre-existing tests pass — which is correct, since those cover behavior
  this task had to preserve. Restoring the fix makes all seven pass.
- **One fixture updated, not weakened.** `test_pscrv_002_upward_search_requires_both_markers` wrote
  an *empty* `pyproject.toml`, which declares no project and so can never resolve under this rule.
  It now declares this project's name; its assertions are unchanged, and it still isolates what it
  was written to prove, that both layout markers are required. Its directory was renamed from
  `unrelated-project` to `checkout`, since under the new rule it has to be this project to resolve.
- Full Docker-backed suite: `1431 passed in 641.39s`, zero failures, zero skips. Ruff format PASS;
  Ruff lint PASS; strict mypy (103 files) PASS.
- **Overlap recorded as corroboration, per the global rules.** The suite started at 02:14:10Z while
  CI `35297412776` (`1347954`) was in its test phase; that run completed **success** at 02:19:06Z,
  about five minutes of overlap including the workspace phase. The remainder of the suite
  overlapped CI `35298161437` (`dbd120b`), which picked up at 02:19Z. Before PSCR-010, the
  equivalent overlap produced 11 CI failures.

### PSCR2-002 — Dispose of the in-repo-virtualenv version skew

**Goal:** the package version and the checkout version cannot silently disagree.

- [x] Choose Option A (document only), B (detect and fail closed), or C (refuse the case).
- [x] Record the chosen option and its rationale in this file.
- [x] If Option B: compare the package version against the found `pyproject.toml`'s
      `[project] version`; on mismatch raise `SetupDockerError` naming both versions and the
      remedy. — implemented against the **installed distribution's** version, not
      `mcp_chatgpt_sandbox.__version__`; see the deviation below.
- [x] If Option B: confirm an editable install cannot trigger the check and say so in the docstring.
- [x] Reword the PSCR-002 docstring sentence "the context it names is genuinely usable" to state what
      is actually guaranteed under the chosen option.
- [x] Leave the same-version-dirty-checkout case (ordinary Dockerfile development) untouched.
- [x] Update `docs/DEVELOPMENT.md`'s troubleshooting entry to describe the chosen behavior.

**Validation — PSCR2V-002**

- [x] Test: matching versions resolve normally.
- [x] Test (if B): mismatched versions fail closed with both versions in the message.
- [x] Test (if B): the message names the remedy and contains no secrets.
- [x] Test: the current real checkout (editable install) passes the check.
- [x] Discrimination (if B): the mismatch test fails against the pre-PSCR2-002 code.

**Decision and evidence (2026-09-18)**

- **Option B selected — detect and fail closed.** It converts a silent mismatch into an actionable
  error for the one case that can actually skew, and leaves ordinary Dockerfile development alone.
- **Deviation from this TODO's own wording, because the spec was wrong.** The spec proposed
  comparing `mcp_chatgpt_sandbox.__version__` and asserted that "an editable install always matches
  by construction". Implementation showed that claim false: `__version__` is a hard-coded literal in
  `__init__.py`, maintained separately from `[project] version`. A half-done version bump would
  have failed the Docker step of an *editable* install over bookkeeping unrelated to Docker. The
  property that genuinely holds by construction is locational, so the check is built on it:
  - if `docker_setup.py` resolves under the found root's `src/` (`_running_from`), code and context
    are one tree, and the check is skipped;
  - otherwise the **installed distribution's** version (`importlib.metadata`, the wheel actually
    running) is compared with the checkout's `pyproject.toml`.
  Option B's intent is preserved exactly; only the mechanism changed, and the spec's stated
  invariant is now true instead of asserted.
- A mismatch, or an installed version that cannot be determined, fails closed. The message names
  both versions and the remedy ("Reinstall the package from this checkout, or build the image
  manually and select it") and contains no secrets.
- The PSCR-002 docstring now says what is and is not guaranteed: finding the enclosing checkout from
  an in-repo venv does *not* guarantee the context matches the running code, and names the function
  that closes that gap.
- `docs/DEVELOPMENT.md` gained a troubleshooting entry describing the check, the remedy, why the
  editable install never trips it, and why a same-version dirty checkout passes deliberately.
- Five tests added to `tests/setup/test_pscr_docker_context_policy.py`: matching versions resolve;
  mismatched versions fail closed naming both and the remedy; an undeterminable installed version
  fails closed; an **in-tree module with version drift is not tripped** (the exact case the spec's
  original design would have broken); and the real editable checkout resolves.
- An autouse fixture pins the installed version for the module, so tests relocated outside a `src/`
  tree no longer pass only because the real version happens to equal the fixture default.
- **Discrimination — one invalid run caught and redone.** The first run against pre-PSCR2-002 code
  reported nothing: every test *errored* in fixture setup, because the fixture patched a function
  that did not yet exist, and the result filter matched only `passed|failed`. The fixture now uses
  `raising=False` (documented in its docstring so it is not "tidied" away). The valid run: the
  mismatch and undeterminable-version tests **fail** against the pre-fix code on behavior; the
  real-editable-checkout test also fails, but only because `_running_from` does not exist yet — a
  reference failure, recorded as such and not counted as behavioral proof. The in-tree-drift and
  matching-version tests pass against both, which is correct: they guard against over-reach.
- Full Docker-backed suite: `1436 passed in 645.65s`, zero failures, zero skips. Ruff format PASS;
  Ruff lint PASS; strict mypy (103 files) PASS.
- **Overlap recorded as corroboration.** Started 02:27:48Z during CI `35298161437` (`dbd120b`),
  which completed **success** at 02:31:33Z; the remainder overlapped CI `35299275538` (`7f13beb`).

---

## Phase 2 — Guard and helper fidelity

### PSCR2-003 — Make the sparse-object dependency explicit

**Goal:** `containers_in_this_context()` cannot break at a distance from a `sparse=True` change.

- [x] Pass `sparse=False` explicitly in the helper's `containers.list(...)` call.
- [x] State in the docstring that the namespace filter reads `.labels`, which docker-py refuses on
      sparse objects, and that this is why the argument is explicit.

**Validation — PSCR2V-003**

- [x] Test: the helper's `list` call carries `sparse=False` (assert on the recorded call kwargs in
      the existing fake client).
- [x] Existing `tests/workspace/test_pscr_namespace_scoped_assertions.py` remains green.

**Closure evidence (2026-09-18)**

- `containers_in_this_context()` now calls `containers.list(all=True, sparse=False, filters=...)`.
  Its docstring records why: the namespace filter reads `container.labels`, which docker-py refuses
  on sparse objects, so the library default it silently relied on is now stated and pinned.
- `test_pscr2v_003_helper_requests_non_sparse_containers` asserts the recorded call carries
  `sparse=False`. **Discrimination verified:** against the pre-PSCR2-003 `conftest.py` it fails and
  the three existing tests pass; restored, all four pass. (That filter matched `ERROR` lines as well
  as `FAILED`, having learned from PSCR2-002's invalid first run.)
- Full Docker-backed suite: `1437 passed in 654.28s`, zero failures, zero skips. This change sits in
  the helper every converted workspace assertion routes through, so the Docker-backed run is the
  meaningful one.

### PSCR2-004 — Close the uncalled `os.getcwd` reference gap

**Goal:** the working-directory guard sees a passed `os.getcwd`, without flagging a field named `cwd`.

- [x] Extend `_working_directory_reads()` to flag an `ast.Attribute` whose value is the name `os` and
      whose attr is `getcwd` or `getcwdb`, whether or not it is called.
- [x] Do **not** extend to bare `.cwd` attribute references; keep those call-only.
- [x] Keep the existing call-node detection unchanged.

**Validation — PSCR2V-004**

- [x] Test: an uncalled `os.getcwd` reference (e.g. passed as an argument) is flagged.
- [x] Test: an uncalled `record.cwd` attribute reference is **not** flagged.
- [x] Test: the existing positive cases (`Path.cwd()`, `os.getcwd()`, aliased `P.cwd()`) still flag.
- [x] Test: the existing negative case (prose mention) still passes.
- [x] Discrimination: the uncalled-reference test fails against the pre-PSCR2-004 detector.
- [x] `tests/setup/test_setup_static_audit.py` remains green.

**Closure evidence (2026-09-18)**

- A second pass flags an `ast.Attribute` whose value is the name `os` and whose attr is `getcwd` or
  `getcwdb`, called or not. The call-node pass is unchanged.
- **A double-count trap avoided.** `ast.walk` visits the `os.getcwd` attribute node *inside* every
  `os.getcwd()` call, so a naive extension would have reported `os.getcwd()` twice and broken the
  existing `== [2]` assertion. Attribute nodes already counted as a call's target are recorded by
  identity and skipped; results are returned sorted so the order stays deterministic.
- The extension is limited to `os.getcwd`/`os.getcwdb` on purpose. A bare `.cwd` attribute is left
  call-only, because `record.cwd` is an ordinary data field and not a working-directory read.
- `test_pscr2v_004_uncalled_os_getcwd_is_flagged_but_a_cwd_field_is_not` covers both uncalled
  forms, the `record.cwd` boundary, and single counting of `os.getcwd()`.
- **Discrimination verified.** The detector lives in the same module as its tests, so the new
  assertions were run directly against the `HEAD` version of `_working_directory_reads`: both
  uncalled-reference cases **fail** (`got=[]`), while the `record.cwd` boundary and the
  single-count case pass against both versions — confirming the extension closes the gap without
  over-reaching or double-counting.
- Verification: Ruff format (319 files) PASS; Ruff lint PASS; strict mypy PASS; Docker-free suite
  plus the acceptance audit `690 passed`; `tests/setup/test_setup_static_audit.py` `16 passed`.
- The full Docker-backed suite was deliberately **not** run for this task: it changes only a
  detector inside a test module, so a Docker run would show only that unrelated tests are
  unaffected by a test file. It runs once at `PSCR2-008`.

### PSCR2-005 — State the `.parents[<int>]` guard's policy

**Goal:** a reader who trips the guard learns the policy from the guard.

- [x] Rewrite `test_pscrv_009_production_has_no_layout_dependent_build_context`'s docstring to state
      it forbids **every** integer subscript of `.parents` in production as a standing policy.
- [x] State why: two such sites were each silently wrong for an installed layout.
- [x] State the alternative: compute ancestor paths from an explicit, documented anchor and record
      the decision.
- [x] No code change.

**Validation — PSCR2V-005**

- [x] The guard still reports zero offenders.
- [x] Docstring review confirms it names the policy, the reason, and the alternative.

**Closure evidence (2026-09-18)**

- The docstring now opens by declaring the check a **standing policy, not only a regression guard**,
  and compares its deliberate breadth to the presentation-module manifest, which likewise fails on
  any unclassified new module to force an explicit decision.
- **Reason** stated: the two sites were each correct only for the `src/` layout, each resolved
  outside the package root once installed, and neither was observable from the editable install —
  a class of defect tests cannot see, hence a ban rather than case-by-case review.
- **Alternative** stated for a future author who trips it: compute the path from an explicit,
  documented anchor — a configured path, a self-validating marker search such as
  `docker_setup._source_checkout_root()`, or `.parent` on a path already held — and record the
  decision in the relevant spec.
- No code change. The guard still reports zero offenders; Ruff lint PASS.

### PSCR2-006 — Record the write-mode heuristic's boundary

**Goal:** the receiver-agnostic check is documented as a known boundary, not left to be rediscovered.

- [x] Record in `_opens_for_writing()`'s docstring that the check is receiver-agnostic.
- [x] Name the false-positive class (`tarfile.open`, `gzip.open`, and similar).
- [x] State that no presentation module currently calls `.open(` at all.
- [x] State that the fix — receiver typing — is outside what an AST guard can do.
- [x] No code change. (Worded under VR-000b for the recording, which is done; fixing the heuristic
      is declined, and no checkbox claims it.)

**Validation — PSCR2V-006**

- [x] `tests/setup/test_setup_static_audit.py` remains green.
- [x] Docstring review confirms the boundary, the class, the current exposure, and the reason.

**Closure evidence (2026-09-18)**

- `_opens_for_writing()`'s docstring now records the boundary as **recorded, not fixed**: the check
  is receiver-agnostic, and it names the false-positive class — `tarfile.open`, `gzip.open`, and
  archive APIs reached through an `.open` attribute.
- **The exposure claim was verified, not repeated.** It was first observed with grep during the
  review; because the docstring now asserts it, it was re-checked with an AST scan of all nine
  presentation modules, which found no `open(...)` or `.open(...)` call of any kind.
- The docstring states why the fix is out of reach (the receiver's type needs a type checker) and
  what to do on a future false positive: move archive handling out of the presentation layer,
  where it would not belong anyway, rather than loosen the check.
- **VR-000b.** Every checkbox above describes the *recording*, which is done. Fixing the heuristic
  is declined, and no checkbox claims it.
- No code change. `tests/setup/test_setup_static_audit.py` `16 passed`; Ruff lint PASS.

---

## Phase 3 — Documentation truth

### PSCR2-007 — Correct the concurrency documentation

**Goal:** no document claims concurrent suite runs collide, because they do not.

- [x] Choose Option A (remove the rule) or Option B (downgrade to advisory); record the choice.
- [x] Rewrite `CLAUDE.md`'s "Never run the suite while another copy…" bullet.
- [x] Rewrite `docs/DEVELOPMENT.md`'s "Full-suite Docker isolation warning" section.
- [x] Rewrite `.claude/skills/gates/SKILL.md`'s collision paragraph in Step 1.
- [x] Rewrite `.claude/skills/lint-n-test/SKILL.md`'s collision paragraph in Step 2.
- [x] If Option B: each rewrite states that runs no longer collide (citing PSCR-010's proof), that
      the self-hosted runner is shared so overlap is slower, and that overlap is safe.
- [x] Change all four locations in **one** commit so they cannot disagree with each other.
- [x] Leave the historical record alone: the PSCR TODO's global-rule entry and PSCR-003 incident
      note describe what was true when the violation happened.

**Validation — PSCR2V-007**

- [x] Grep: no remaining passage claims concurrent runs adopt or reconcile each other's containers.
- [x] All four locations agree with each other and with PSCR-010's recorded proof.
- [x] The `gates` and `lint-n-test` skills still describe the Docker-reachable and image-present
      preconditions unchanged.

**Decision and evidence (2026-09-18)**

- **Option B selected — downgrade to advisory.** The *collision* claim was false and is gone; the
  *contention* point is still true, since the CI runner is self-hosted on this machine and shares
  its CPU and Docker. Deleting the warning outright, immediately after a documented violation of
  it, when part of its substance survives, would have been the wrong direction.
- All four locations now say the same thing: concurrent suite runs are **safe but slower**, prefer
  not to overlap, don't wait on it. `docs/DEVELOPMENT.md`'s section was retitled from "Full-suite
  Docker isolation warning" to "Running the suite concurrently", explains both mechanisms
  (G3R-015 namespacing and PSCR-010's scoped assertions), and cites the A/B demonstration.
- **Scoped so as not to trade one over-claim for another.** Two of the old passages also warned
  about a *live server*. PSCR-010's proof covered two test sessions, not a test session beside a
  running `serve`. That case is protected by design — the production manager's reconcile paths
  filter on the namespace (`manager_core.py:148` `_find_container`, and — *corrected during
  PSCR2-009* — `operations/resources.py` `_namespace_proof()` via `classify_workspace_container()`;
  the `manager_core.py:200` originally cited here is inside `_CoreMixin.reconcile()`, which
  `DockerWorkspaceManager` overrides and never calls), a live server's containers carry no
  namespace label, and suite teardown removes only the exact resources it created (HR-026) — but
  it was not demonstrated. The rewrites say exactly that, and still advise against running the
  suite on a daemon that is serving real workspaces.
- **A reversal the review had not named.** The old text told readers that collision-shaped
  failures were "collisions, not real defects". After PSCR-010 that is backwards: such a failure
  now means the namespacing has regressed. `CLAUDE.md`, `docs/DEVELOPMENT.md`, and both skills'
  report sections (`gates` Step 1 report bullet, `lint-n-test` Step 2 and report bullet) now say a
  collision is a **real defect** to investigate, never to hide with a retry or skip.
- Grep for the old claims finds none; the two remaining matches are the negated form ("no longer
  adopt each other's containers"). No links break: the only references to the old heading are this
  series' own spec and TODO, which describe the task. Both skills keep their `docker info`,
  `docker image inspect`, and test-image build preconditions unchanged.
- Documentation only; no code change. Acceptance-coverage and `docs/TOOLS.md` drift guards
  `18 passed`.

### PSCR2-009 — Demonstrate live-server isolation

**Goal:** replace "protected by design, but not demonstrated" with a demonstration.

Added after closure, on request.

- [x] Add a real-Docker test: a production-shaped reconcile does not adopt a test context's container.
- [x] Add a real-Docker test: a test context's reconcile does not adopt a production-shaped container.
- [x] Give each test a control reconcile proving the container was findable under its own shape.
- [x] Give the production-shaped container a per-run repository id, so the test is itself safe to
      run concurrently.
- [x] Remove the "not demonstrated" caveat from every location that carried it, in one commit. —
      **three** locations, not four as first written: `CLAUDE.md` never mentioned a live server.

**Validation — PSCR2V-009**

- [x] Both tests pass against real Docker.
- [x] Discrimination: with the live namespace boundary neutered, the exclusion assertions fail. —
      see below: the first attempt targeted dead code.
- [x] Full Docker-backed suite passes.
- [x] Exact-head CI green. — run `35323036386` on `68a37fd`, **success** (see below).

**Closure evidence (2026-09-18)**

- `tests/workspace/test_pscr2_live_server_isolation.py` adds two real-Docker tests. Each creates a
  workspace container under one shape, clears the recorded id so reconcile must adopt by label,
  reconciles under the *other* shape and asserts nothing is adopted, then reconciles under the
  original shape and asserts the container **is** adopted — so an exclusion cannot pass merely
  because a container was invisible. Both pass.
- The production-shaped container uses a per-run repository id (`pscr2-<12 hex>`). With the shared
  `example` id its deterministic production name would collide between two concurrent suites, and a
  test meant to demonstrate isolation would have reintroduced the hazard PSCR-010 removed.
- **The first discrimination run was invalid, and exposed dead code.** It removed the namespace
  filter from `_CoreMixin.reconcile()` in `workspace/manager_core.py`, and both tests still passed.
  Investigation showed `DockerWorkspaceManager` overrides `reconcile()` in `workspace/manager.py`
  (the MRO resolves there) and nothing calls `super().reconcile()`, so the method edited was never
  executed. The live boundary is `_namespace_proof()` in `operations/resources.py`, reached through
  PMOR-001's `classify_workspace_container()`. Neutering it makes **both tests fail**; restoring it
  makes both pass, with `resources.py` byte-identical to `HEAD` afterwards.
- **Wrong citations corrected.** PSCR2-007's evidence above cited `manager_core.py:200` as the live
  protection; it is inside the shadowed method. It is corrected in place, visibly, as is this task's
  own spec text, which had described `manager_core.reconcile()` as the mechanism. The test's
  docstring and `docs/DEVELOPMENT.md` now name the real path. Deleting the dead method is PSCR2-011.
- **The caveat replaced, not merely removed.** `docs/DEVELOPMENT.md`, `gates`, and `lint-n-test` now
  state that a live `serve` is isolated in both directions that matter, each demonstrated: adoption
  (PSCR2-009) and teardown (HR-026 / HAT-020, `test_conftest_teardown.py`, confirmed to create both a
  sentinel container and a sentinel volume). The wording stops there — neither test says anything
  about sharing CPU, memory, or disk with real workspaces, so all three still advise against running
  the suite beside real work, now for capacity rather than safety.
- **The re-run of CI `35271467140`** was done in the same session, deliberately sequenced so no local
  Docker work overlapped it, because it executes `ed6132e`'s pre-PSCR-010, namespace-blind assertions.
  Result: attempt 2, job `105516949482`, **success**, `1415 passed` — the same test count as the
  original red attempt (1404 passed + 11 failed), so every test that failed during the collision
  passed against unchanged code. Attempt 1 remains in GitHub's run history.
- Full Docker-backed suite (covering PSCR2-009 and PSCR2-010 together, which is recorded in both
  entries): `1442 passed in 655.74s`, zero failures, zero skips. It overlapped two `pytest` processes
  from an unrelated project on the same host without incident.

### PSCR2-010 — Pin `sparse=False` in production

**Goal:** no production `containers.list()` relies on an unstated docker-py default.

Found while writing PSCR2-009; added on request.

- [x] Find every production `containers.list()` / `volumes.list()` call site.
- [x] Confirm which read `.labels` on their results.
- [x] Pass `sparse=False` explicitly at each container site, with a comment saying why.
- [x] Exclude `volumes.list()` deliberately, and record why.
- [x] Add a repository-wide guard against an unpinned production `containers.list()`.

**Validation — PSCR2V-010**

- [x] Guard passes on the fixed tree.
- [x] Negative test pins the detector, including out-of-scope calls.
- [x] Discrimination: against the pre-fix source the guard names exactly the unpinned sites.
- [x] Full Docker-backed suite passes.

**Closure evidence (2026-09-18)**

- Four `list()` call sites exist in `src/`. `sparse` exists only on `ContainerCollection.list`
  (default `False`); `VolumeCollection.list` takes only `**kwargs`, so `inventory.py`'s
  `volumes.list` is out of scope and was deliberately left unchanged rather than edited for symmetry.
- The three container sites all read `.labels` on their results: `manager.py:213` (the live
  reconcile), `manager_core.py:200`, and `inventory.py:283`. Each now passes `sparse=False`, with a
  comment saying why.
- **The `inventory.py` site was a trap.** It reads `getattr(container, "labels", {})`, which looks
  defensive, but a sparse object raises `DockerException` there, not `AttributeError`, so the
  default would never have applied. Its comment now says so.
- **One of the three sites is dead code.** `manager_core.py`'s site is inside
  `_CoreMixin.reconcile()`, which PSCR2-009 found is shadowed and never executed. It was pinned
  anyway, since the guard is textual and the method still existed; PSCR2-011 deletes it.
- `tests/test_docker_list_sparse_guard.py` flags any `<x>.containers.list(...)` in
  `src/mcp_chatgpt_sandbox` that lacks `sparse=False`, and a negative test pins the detector: bare
  and `sparse=True` calls are flagged, a pinned call is not, and `volumes.list` / unrelated `.list()`
  calls are out of scope.
- **Discrimination verified.** Against the three files restored from `HEAD`, the guard fails naming
  exactly `operations/inventory.py:264`, `workspace/manager.py:201`, and
  `workspace/manager_core.py:197` — precise, not merely able to fail. Restored, it passes.
- Full Docker-backed suite (the same run as PSCR2-009, recorded in both entries): `1442 passed in
  655.74s`, zero failures. Behavior cannot change, since `sparse=False` is the library default; the
  run confirms nothing else did.

### PSCR2-011 — Remove the shadowed `_CoreMixin.reconcile()`

**Goal:** no code that reads as the live reconcile path while never executing.

Found by PSCR2-009; added on request.

- [x] Confirm the method is unreachable: overridden, no `super()` call, no test caller.
- [x] Replace the implementation with a declaration that raises and names the real one.
- [x] Remove imports only the dead body used, after checking none is re-exported.
- [x] Correct the module docstrings and `docs/DEVELOPMENT.md` that described it.
- [x] Pin the arrangement with a test.

**Validation — PSCR2V-011**

- [x] Strict mypy passes, including the mixins' `self.reconcile(...)` callers.
- [x] `DockerWorkspaceManager.reconcile` still resolves to `workspace/manager.py` at runtime.
- [x] Discrimination: the pinning test fails against the pre-PSCR2-011 module.
- [x] Full Docker-backed suite passes. — `1444 passed in 672.35s`, zero failures.

**Closure evidence (2026-09-18)**

- **Unreachable, confirmed three ways.** The MRO resolves `DockerWorkspaceManager.reconcile` to
  `workspace/manager.py`; `_LifecycleMixin` is the only subclass of `_CoreMixin` and nothing calls
  `super().reconcile()`; no test references `_CoreMixin` at all.
- **Not deleted outright, deliberately.** `_CoreMixin.reconcile_all()` (`manager_core.py`) and
  `manager_lifecycle.py:113` call `self.reconcile(...)`. Those resolve to the override at runtime,
  but strict mypy needs the mixin to declare the method. The 77-line body is replaced by a 22-line
  declaration whose docstring names `DockerWorkspaceManager.reconcile()` as the implementation,
  explains why it is only declared, and records why the old body was removed; it raises
  `NotImplementedError` naming `workspace/manager.py`.
- Five imports were left unused by the removal (`replace`, `LABEL_WORKSPACE_ID`, `_now`,
  `_oom_killed_from_docker_container`, `_state_from_docker_container`). Before removing them, every
  import from `manager_core` in `src/` and `tests/` was checked, including parenthesized multi-line
  forms that a first single-line search would have missed: the only name imported from the module
  anywhere is `_CoreMixin`. `manager_core.py` went from 290 to 232 lines.
- `manager_core.py`'s module docstring no longer claims per-workspace reconciliation (it now says the
  mixin holds the §44.1 startup sweep and only declares `reconcile()`); `manager.py`'s module map says
  the same; and `docs/DEVELOPMENT.md`'s parenthetical warning about the shadowed method is replaced,
  since the method no longer exists.
- PSCR2-010's guard now sees two production `containers.list()` sites rather than three, both pinned.
- `tests/workspace/test_pscr2_reconcile_declaration.py` asserts the concrete class defines its own
  `reconcile()` distinct from the mixin's, and that the mixin's raises `NotImplementedError` naming
  `workspace/manager.py`. **Discrimination verified:** against the pre-PSCR2-011 module the
  declaration test fails; the override test passes before and after, which is correct — it guards
  against the override ever being removed.
- Ruff format (321 files) PASS; Ruff lint PASS; strict mypy (103 files) PASS; the PSCR2-009
  live-server tests still pass against the live boundary.
- Full Docker-backed suite: `1444 passed in 672.35s`, zero failures, zero skips, run alongside CI
  `35321901297` (`3162a83`), which also passed.

---

## Phase 4 — Final acceptance and closure

### PSCR2-008 — Final regression, code-review, contract, and merge closure

**Goal:** close only after every touched boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [x] The checkout search returns only this project's root; a right-layout/wrong-name decoy is skipped.
- [x] The version-skew disposition is implemented per the recorded option.
- [x] The PSCR-002 docstring claims only what is guaranteed.
- [x] `containers_in_this_context()` states and pins `sparse=False`.
- [x] An uncalled `os.getcwd` reference fails the guard; an uncalled `record.cwd` does not.
- [x] The `.parents[<int>]` guard docstring states policy, reason, and alternative.
- [x] The write-mode heuristic's boundary is recorded in its docstring.
- [x] All four concurrency passages say the same accurate thing.
- [x] Every added regression test was shown to fail against the pre-fix code.
- [x] PSCR-001/003/010 behaviors unchanged; no assertion weakened.
- [x] Setup state remains secret-free; diagnostics remain fail-closed and non-secret.

#### Contract/version decision

- [x] Re-export schemas and diff against `schemas/tools.json`.
- [x] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCR2V-008

- [x] `uv run ruff format --check .` passes. — 319 files.
- [x] `uv run ruff check .` passes.
- [x] `uv run mypy src` passes in strict mode. — 103 source files.
- [x] MCP exported-schema contract check passes. — re-export byte-identical.
- [x] `contract_version` history gate passes.
- [x] `docs/TOOLS.md` drift gate passes.
- [x] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [x] Record whether the run overlapped a CI run; if so, record it as PSCR2-007 corroboration.
- [x] Record exact pytest count. — `1438 passed`.
- [x] Record exact warning count. — `0 warnings`.
- [x] Record exact coverage total/statements/misses. — `88%`, `9358` statements / `1125` misses.
- [x] Record exact implementation head SHA. — `e939975`, closure evidence commit recorded below.
- [x] Record exact CI run ID and job ID. — run `35317707321`, job `105512908047`.
- [x] Post-push `master` CI passes on the exact head SHA. — `7815a45`, conclusion **success**. (No merge-guard items: this series commits
      directly to `master`.)
- [x] Record post-push run/job/test/coverage evidence. — `1438 passed in 684.19s`; coverage
      `88%` (`9358` statements / `1125` misses).
- [x] Update this TODO status to complete only after post-push evidence is green.

---

**Closure evidence (2026-09-18)**

- Gates on head `e939975`: Ruff format (319 files) PASS; Ruff lint PASS; strict mypy (103 files)
  PASS. The contract was re-exported through CI's own command and diffed byte-for-byte against
  `schemas/tools.json`: **identical**. `contract_version` stays `0.10.3`; no bump was required.
- Every PSCR2 commit already has its own green CI: `7f13beb` (`35299275538`), `3e6d86d`
  (`35300212582`), `2790229` (`35300998277`), `ab977f7` (`35301106893`), `12b5dbc`
  (`35301149867`), `76eb963` (`35301198972`), `e939975` (`35301304743`) — all **success**.
- Closure suite on an idle host: `1438 passed in 669.53s`, `0 warnings`, coverage `88%` — `9358`
  statements / `1125` misses. Zero skips.

**The closure run was killed twice before it completed, and that changed PSCR2-007**

- Attempt 1 (02:56:50Z) and attempt 2 (02:57:43Z) were each terminated about 40 seconds in, at
  15–16% (`tests/filesystem` into `tests/git`), with **zero failures** so far. Both were
  `pytest --cov --cov-report=term-missing` and both overlapped CI `35300998277`'s own `--cov`
  run. The termination notice came from the Claude Code background-task memory guard. The kernel
  logged no out-of-memory event (the only OOM entries were memory-cgroup kills inside a container
  that `test_resources.py` deliberately caps at `256m`, timestamped *before* either attempt), swap
  was essentially unused, and 22–23 GiB was available. "Free" memory was about 2.4–3.0 GiB
  because page cache held most of RAM.
- A memory probe of the killed stretch was attempted and **failed to sample**: the probe's shell script
  backgrounded the whole `LOG=… &&` chain as one job, so the sampling loop never saw `$LOG` or
  `$PID`. It produced no memory data and none is claimed. It did establish one thing: that stretch
  alone, with `--cov`, completed (`395 passed in 16.11s`), so no test there deterministically
  triggers the kill.
- Attempt 3 ran after the operator chose to clear the CI backlog. The queue had drained on its own during an
  interruption, so there was nothing to cancel. On an **idle** host the same command completed.
- The pattern across this series: non-coverage suites overlapped CI three times without incident;
  both coverage runs that overlapped CI's coverage run were killed; the coverage run on an idle host
  completed. Three data points do not prove causation, and none of this is a correctness problem —
  no test failed in any run. But PSCR2-007's "safe but slower" was silent about memory, and
  `gates` runs exactly this `--cov` suite.
- **PSCR2-007's guidance was amended in this task**, in all four locations together: the CI runner
  shares memory as well as CPU and Docker, and a `--cov` run should use an idle host.
  `docs/DEVELOPMENT.md` records the evidence and says plainly that this is a limit of the agent
  harness rather than of the code; `gates` now waits for an idle runner before its `--cov` step;
  `lint-n-test`, which runs without coverage, keeps overlap acceptable and points to the
  explanation.

---

## Definition of done

- [x] The checkout search returns only this project's root, and a decoy with the right layout and
      wrong name is skipped.
- [x] The in-repo-virtualenv version skew has a recorded disposition, implemented per the chosen
      option, and the PSCR-002 docstring no longer claims more than is guaranteed.
- [x] `containers_in_this_context()` states and pins its `sparse=False` dependency.
- [x] An uncalled `os.getcwd` reference fails the working-directory guard; an uncalled `record.cwd`
      does not.
- [x] The `.parents[<int>]` guard's docstring states its policy and the alternative.
- [x] The write-mode heuristic's boundary is recorded, with its checkbox worded for the recording.
- [x] All four concurrency passages say the same accurate thing, changed in one commit.
- [x] Every regression test added here has been shown to fail against the pre-fix code.
- [x] Public MCP contract confirmed unchanged by re-export and diff.
- [x] Exact-head CI is green. — run `35317707321` on `7815a45`.
- [x] Post-push `master` CI is green. — same run; the push is the exact-head run.
- [x] Exact closure evidence is recorded in this file.
- [x] *(Post-closure, PSCR2-009; entry added by PSCR3-001.)* Live-server isolation is demonstrated in
      both directions on a real daemon, and the "not demonstrated" caveat is gone.
- [x] *(Post-closure, PSCR2-010; entry added by PSCR3-001.)* Every production `containers.list()`
      passes `sparse=False`, enforced by a repository-wide guard.
- [x] *(Post-closure, PSCR2-011; entry added by PSCR3-001.)* `_CoreMixin` only declares
      `reconcile()`; the shadowed implementation is removed and the arrangement is pinned by a test.

**Final acceptance evidence (2026-09-18)**

- Exact head `7815a45` — CI run `35317707321`, job `105512908047`, conclusion **success**: all
  gates plus the full Docker-backed suite, `1438 passed in 684.19s`, coverage `88%` (`9358`
  statements / `1125` misses). CI's numbers match the idle-host local closure run exactly, so the
  two corroborate rather than merely both being green.
- Public MCP tool/schema contract unchanged throughout; `contract_version` stays `0.10.3`.
- This status update is documentation-only and changes no code, so the evidence above covers the
  complete implementation of `PSCR2-001` through `PSCR2-008`.

**Post-closure additions — acceptance evidence (2026-09-18)**

- `PSCR2-009`–`PSCR2-011` were added after the original closure, on request. Exact head `68a37fd` —
  CI run `35323036386`, job `105529628610`, conclusion **success**: `1444 passed in 697.88s`,
  coverage `88%` (`9342` statements / `1108` misses), matching the local `1444 passed`.
- The statement count fell by 16 from `9358` because PSCR2-011 removed the shadowed reconcile body;
  misses fell by 17, since that body was never executed and so was never covered.
- The CI re-run of `35271467140` (`ed6132e`) passed on its second attempt, `1415 passed`, job
  `105516949482`, confirming its original 11 failures were collision artifacts. Attempt 1 remains in
  the run history.
- Public MCP contract unchanged; `contract_version` stays `0.10.3`.
- This status update is documentation-only; the CI evidence above covers the complete implementation.
