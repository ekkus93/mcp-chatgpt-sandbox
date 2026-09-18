# Post-PSCR Code Review TODO

**Status:** Open — `PSCR2-001` through `PSCR2-008` not started  
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

- [ ] Parse the candidate `pyproject.toml` with `tomllib` and require
      `[project] name == "mcp-chatgpt-sandbox"`.
- [ ] Skip a candidate with the right layout and the wrong name; continue the upward search.
- [ ] Keep the search self-validating: the returned root still holds `docker/default/`.
- [ ] Decide whether "found but foreign" needs a distinct operator message from "not found"; record
      the decision and its reasoning.
- [ ] Tolerate an unreadable or malformed `pyproject.toml` by skipping that candidate, not by raising
      an unrelated parse error out of context resolution.
- [ ] Keep `validate_default_image_build_context()` as the single normalization/refusal point.

**Validation — PSCR2V-001**

- [ ] Test: resolution still succeeds from the real source checkout.
- [ ] Test (decoy): a synthesized ancestor with `pyproject.toml` + `docker/default/` but the wrong
      `[project] name` is skipped, and the search reports no checkout.
- [ ] Test: a decoy *below* the real root does not shadow it — the search reaches the correct
      project root.
- [ ] Test: a malformed `pyproject.toml` on a candidate is skipped without an unrelated error.
- [ ] Discrimination: the decoy test fails against the pre-PSCR2-001 search.
- [ ] Existing `tests/setup/test_pscr_docker_context_policy.py` remains green.

### PSCR2-002 — Dispose of the in-repo-virtualenv version skew

**Goal:** the package version and the checkout version cannot silently disagree.

- [ ] Choose Option A (document only), B (detect and fail closed), or C (refuse the case).
- [ ] Record the chosen option and its rationale in this file.
- [ ] If Option B: after PSCR2-001 finds the root, compare `mcp_chatgpt_sandbox.__version__` against
      the found `pyproject.toml`'s `[project] version`; on mismatch raise `SetupDockerError` naming
      both versions and the remedy.
- [ ] If Option B: confirm an editable install cannot trigger the check (same source, same version by
      construction) and say so in the docstring.
- [ ] Reword the PSCR-002 docstring sentence "the context it names is genuinely usable" to state what
      is actually guaranteed under the chosen option.
- [ ] Leave the same-version-dirty-checkout case (ordinary Dockerfile development) untouched.
- [ ] Update `docs/DEVELOPMENT.md`'s troubleshooting entry to describe the chosen behavior.

**Validation — PSCR2V-002**

- [ ] Test: matching versions resolve normally.
- [ ] Test (if B): mismatched versions fail closed with both versions in the message.
- [ ] Test (if B): the message names the remedy and contains no secrets.
- [ ] Test: the current real checkout (editable install) passes the check.
- [ ] Discrimination (if B): the mismatch test fails against the pre-PSCR2-002 code.

---

## Phase 2 — Guard and helper fidelity

### PSCR2-003 — Make the sparse-object dependency explicit

**Goal:** `containers_in_this_context()` cannot break at a distance from a `sparse=True` change.

- [ ] Pass `sparse=False` explicitly in the helper's `containers.list(...)` call.
- [ ] State in the docstring that the namespace filter reads `.labels`, which docker-py refuses on
      sparse objects, and that this is why the argument is explicit.

**Validation — PSCR2V-003**

- [ ] Test: the helper's `list` call carries `sparse=False` (assert on the recorded call kwargs in
      the existing fake client).
- [ ] Existing `tests/workspace/test_pscr_namespace_scoped_assertions.py` remains green.

### PSCR2-004 — Close the uncalled `os.getcwd` reference gap

**Goal:** the working-directory guard sees a passed `os.getcwd`, without flagging a field named `cwd`.

- [ ] Extend `_working_directory_reads()` to flag an `ast.Attribute` whose value is the name `os` and
      whose attr is `getcwd` or `getcwdb`, whether or not it is called.
- [ ] Do **not** extend to bare `.cwd` attribute references; keep those call-only.
- [ ] Keep the existing call-node detection unchanged.

**Validation — PSCR2V-004**

- [ ] Test: an uncalled `os.getcwd` reference (e.g. passed as an argument) is flagged.
- [ ] Test: an uncalled `record.cwd` attribute reference is **not** flagged.
- [ ] Test: the existing positive cases (`Path.cwd()`, `os.getcwd()`, aliased `P.cwd()`) still flag.
- [ ] Test: the existing negative case (prose mention) still passes.
- [ ] Discrimination: the uncalled-reference test fails against the pre-PSCR2-004 detector.
- [ ] `tests/setup/test_setup_static_audit.py` remains green.

### PSCR2-005 — State the `.parents[<int>]` guard's policy

**Goal:** a reader who trips the guard learns the policy from the guard.

- [ ] Rewrite `test_pscrv_009_production_has_no_layout_dependent_build_context`'s docstring to state
      it forbids **every** integer subscript of `.parents` in production as a standing policy.
- [ ] State why: two such sites were each silently wrong for an installed layout.
- [ ] State the alternative: compute ancestor paths from an explicit, documented anchor and record
      the decision.
- [ ] No code change.

**Validation — PSCR2V-005**

- [ ] The guard still reports zero offenders.
- [ ] Docstring review confirms it names the policy, the reason, and the alternative.

### PSCR2-006 — Record the write-mode heuristic's boundary

**Goal:** the receiver-agnostic check is documented as a known boundary, not left to be rediscovered.

- [ ] Record in `_opens_for_writing()`'s docstring that the check is receiver-agnostic.
- [ ] Name the false-positive class (`tarfile.open`, `gzip.open`, and similar).
- [ ] State that no presentation module currently calls `.open(` at all.
- [ ] State that the fix — receiver typing — is outside what an AST guard can do.
- [ ] No code change. (Worded under VR-000b for the recording, which is done; fixing the heuristic
      is declined, and no checkbox claims it.)

**Validation — PSCR2V-006**

- [ ] `tests/setup/test_setup_static_audit.py` remains green.
- [ ] Docstring review confirms the boundary, the class, the current exposure, and the reason.

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
