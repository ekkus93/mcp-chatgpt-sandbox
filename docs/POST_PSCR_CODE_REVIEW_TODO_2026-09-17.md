# Post-PSCR Code Review TODO

**Status:** In progress — `PSCR2-001`–`PSCR2-006` closed; `PSCR2-007` next  
**Date:** 2026-09-17  
**Spec:** `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`  
**Reviewed baseline:** `134795429eae988f927e43ec97cc04c5f98b867a`  
**Task series:** `PSCR2-001` through `PSCR2-008`  
**Validation series:** `PSCR2V-*`

This TODO implements `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`. It closes one correctness gap,
four heuristic boundaries, and one falsified document found by adversarially re-reading the merged
`PSCR-001`–`PSCR-012` work. All twelve PSCR tasks were confirmed substantively implemented; these
are the residue, held to the same standard: every fix gets a regression test proven to discriminate,
every declined fix is recorded as declined, and no document claims more than the code does.

## Global rules

- [ ] Start from an exact inspected `master` SHA.
- [ ] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR. (This rule is stated correctly here; the PSCR TODO carried a `ralph/` branch rule from an
      older template that contradicted `CLAUDE.md`, and had to be corrected during closure.)
- [ ] Keep `ServerConfig`, `GitPolicyConfig`, and `load_config()` authoritative.
- [ ] Preserve the PSCR-001 checkout-only Docker context policy; do not reopen Option A.
- [ ] Preserve PSCR-003: a present configured image validates the Docker step without a context.
- [ ] Preserve reviewed config-write semantics and the SWPH-006 lock design unchanged.
- [ ] Preserve leaf-private directory semantics as documented in `docs/CONFIGURATION.md`.
- [ ] Preserve G3R-015 namespacing and PSCR-010's scoped assertions; weaken no assertion.
- [ ] Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- [ ] Add a focused regression test for every correctness defect, and prove it discriminates by
      running it against the pre-fix code.
- [ ] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [ ] Do not weaken existing tests or architecture guards; a replacement must be strictly stronger.
- [ ] Do not bump `contract_version` unless the public MCP contract actually changes.
- [ ] Until `PSCR2-007` disposes of it, treat overlapping a local suite with a CI run as advisory-avoid:
      PSCR-010 made it safe, but the self-hosted runner is shared. If it happens, record it as
      corroborating evidence, not as a violation.
- [ ] Record exact SHA/run/job evidence as each phase closes.

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

- [ ] Choose Option A (remove the rule) or Option B (downgrade to advisory); record the choice.
- [ ] Rewrite `CLAUDE.md`'s "Never run the suite while another copy…" bullet.
- [ ] Rewrite `docs/DEVELOPMENT.md`'s "Full-suite Docker isolation warning" section.
- [ ] Rewrite `.claude/skills/gates/SKILL.md`'s collision paragraph in Step 1.
- [ ] Rewrite `.claude/skills/lint-n-test/SKILL.md`'s collision paragraph in Step 2.
- [ ] If Option B: each rewrite states that runs no longer collide (citing PSCR-010's proof), that
      the self-hosted runner is shared so overlap is slower, and that overlap is safe.
- [ ] Change all four locations in **one** commit so they cannot disagree with each other.
- [ ] Leave the historical record alone: the PSCR TODO's global-rule entry and PSCR-003 incident
      note describe what was true when the violation happened.

**Validation — PSCR2V-007**

- [ ] Grep: no remaining passage claims concurrent runs adopt or reconcile each other's containers.
- [ ] All four locations agree with each other and with PSCR-010's recorded proof.
- [ ] The `gates` and `lint-n-test` skills still describe the Docker-reachable and image-present
      preconditions unchanged.

---

## Phase 4 — Final acceptance and closure

### PSCR2-008 — Final regression, code-review, contract, and merge closure

**Goal:** close only after every touched boundary is re-verified against implementation and tests.

#### Final code-review checklist

- [ ] The checkout search returns only this project's root; a right-layout/wrong-name decoy is skipped.
- [ ] The version-skew disposition is implemented per the recorded option.
- [ ] The PSCR-002 docstring claims only what is guaranteed.
- [ ] `containers_in_this_context()` states and pins `sparse=False`.
- [ ] An uncalled `os.getcwd` reference fails the guard; an uncalled `record.cwd` does not.
- [ ] The `.parents[<int>]` guard docstring states policy, reason, and alternative.
- [ ] The write-mode heuristic's boundary is recorded in its docstring.
- [ ] All four concurrency passages say the same accurate thing.
- [ ] Every added regression test was shown to fail against the pre-fix code.
- [ ] PSCR-001/003/010 behaviors unchanged; no assertion weakened.
- [ ] Setup state remains secret-free; diagnostics remain fail-closed and non-secret.

#### Contract/version decision

- [ ] Re-export schemas and diff against `schemas/tools.json`.
- [ ] Record the explicit decision and whether a `contract_version` bump was required.

#### Complete validation — PSCR2V-008

- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy src` passes in strict mode.
- [ ] MCP exported-schema contract check passes.
- [ ] `contract_version` history gate passes.
- [ ] `docs/TOOLS.md` drift gate passes.
- [ ] Full Docker-backed `uv run pytest --cov --cov-report=term-missing` passes.
- [ ] Record whether the run overlapped a CI run; if so, record it as PSCR2-007 corroboration.
- [ ] Record exact pytest count.
- [ ] Record exact warning count.
- [ ] Record exact coverage total/statements/misses.
- [ ] Record exact implementation head SHA.
- [ ] Record exact CI run ID and job ID.
- [ ] Post-push `master` CI passes on the exact head SHA. (No merge-guard items: this series commits
      directly to `master`.)
- [ ] Record post-push run/job/test/coverage evidence.
- [ ] Update this TODO status to complete only after post-push evidence is green.

---

## Definition of done

- [ ] The checkout search returns only this project's root, and a decoy with the right layout and
      wrong name is skipped.
- [ ] The in-repo-virtualenv version skew has a recorded disposition, implemented per the chosen
      option, and the PSCR-002 docstring no longer claims more than is guaranteed.
- [ ] `containers_in_this_context()` states and pins its `sparse=False` dependency.
- [ ] An uncalled `os.getcwd` reference fails the working-directory guard; an uncalled `record.cwd`
      does not.
- [ ] The `.parents[<int>]` guard's docstring states its policy and the alternative.
- [ ] The write-mode heuristic's boundary is recorded, with its checkbox worded for the recording.
- [ ] All four concurrency passages say the same accurate thing, changed in one commit.
- [ ] Every regression test added here has been shown to fail against the pre-fix code.
- [ ] Public MCP contract confirmed unchanged by re-export and diff.
- [ ] Exact-head CI is green.
- [ ] Post-push `master` CI is green.
- [ ] Exact closure evidence is recorded in this file.
