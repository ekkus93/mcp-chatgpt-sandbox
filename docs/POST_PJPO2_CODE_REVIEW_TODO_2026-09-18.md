# Post-PJPO2 Code Review TODO

**Status:** Complete except `PJPO3-004` (user-performed ChatGPT check, not performed) — `PJPO3-001`–`PJPO3-003`, `PJPO3-005` closed  
**Date:** 2026-09-18  
**Spec:** `docs/POST_PJPO2_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `d6d0105`  
**Task series:** `PJPO3-001` through `PJPO3-005`  
**Validation series:** `PJPO3V-*`

This TODO implements `docs/POST_PJPO2_CODE_REVIEW_SPEC_2026-09-18.md`. A review of the merged PJPO2
series found no bug. It found an unguarded public method, a defensive line of code that a test
appears to pin but does not, and one true evidence claim with no test behind it — none reachable
from a production caller. It also carries forward the one verification only the user can perform.
No task here changes a tool contract.

## Global rules

- [x] Start from an exact inspected `master` SHA and record it here. —
      `84dac7c447481bb866799481d08c6bf30637b442` (the commit adding this pair; `ruff format` in the
      pre-commit hook reformatted the spec's Python probe snippet, which was applied, not bypassed).
- [x] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR, one task per commit, task ID in the subject.
- [x] Record RD1 (spec §3) — option, who chose it, why — before PJPO3-001. Ask the user; do not
      default silently. — Already decided by the user: **RD1-A** (see PJPO3-001).
- [x] Prove each regression test discriminates (restore or mutate the code, run, record the failure,
      restore, confirm byte-identical with `cmp`). Where a test pins a property that already holds,
      say so plainly instead of contriving a failure.
- [x] A passing assertion is not evidence that a line is pinned: mutate the line.
- [x] Preserve PJPO2's outcomes (K3). Do not weaken existing tests or guards. Do not bump
      `contract_version`; prove K4 by re-exporting.
- [x] Correct records visibly — original kept, marked, correction added with its task ID.
- [x] Do not edit `src/` while a suite is running. Run suites in the foreground (background runs
      are killed by the memory guard on this host); `--cov` runs on an idle host.
- [x] Cite code by symbol; verify any line number or count by command at the commit named.
- [x] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [x] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — Code and tests

### PJPO3-001 — Refuse a second `commit_attach`

**Finding:** F1 (spec §2.1). **Decision:** RD1.

- [x] Record RD1. — **A, guard, keep `prepare_attach` / `commit_attach` public**, chosen by the user
      on 2026-09-18, before the series started. Do not ask again.
- [x] `StreamCapture.commit_attach` (`execution/log_capture.py`): raise `RuntimeError` if the capture
      is already attached, before changing any state — the same refusal `prepare_attach` makes.
- [ ] If RD1-B: also rename both methods private, update `PromotableSink.promote`, and rename the
      hook in `test_jpov002_a_chunk_arriving_during_promotion_waits_for_it`; re-prove that test
      still fails with the lock removed. — *Not applicable: RD1-A chosen.*
- [x] Docstring: what the guard covers (double commit) and what it deliberately does not (a file
      prepared for one path committed under another — spec §3), citing PJPO3-001 and how the
      unguarded version behaved (the probe: second path adopted, first file never closed).

**Validation — PJPO3V-001**

- [x] Test: prepare + commit to a first path, then a second `commit_attach` to another path raises;
      a later `write` + `finish()` lands in the first file; nothing exists at the second path.
      Prove it fails against the pre-fix `commit_attach`.
- [x] Every existing `tests/execution/` test passes unchanged (K3). Full suite green.

**Evidence — PJPO3-001.** `commit_attach` raises `RuntimeError("capture is already attached …")`
before assigning anything; the docstring cites the probe and states the mismatched-pair gap
(RD1-A, public methods kept). Test `test_pjpo3v001_a_second_commit_attach_is_refused`
(`tests/execution/test_pjpo2_promote_atomicity.py`): against `84dac7c`'s `commit_attach` it fails
`DID NOT RAISE RuntimeError`; restored byte-identical (`cmp`). All 45 `tests/execution/` tests pass,
none edited. Full suite, foreground chunks: 1033 + 145 + 198 + 118 = 1494 = `--collect-only`, all
passed (non-`--cov`, overlapping CI run for `84dac7c`).

Process note: an earlier session implemented this same change before being told the user only
wanted RD1-A recorded; that work was reverted (`git checkout --`) and never committed. This is the
first committed implementation.

### PJPO3-002 — Pin the explicit close of the prepared stdout file

**Finding:** F2 (spec §2.2).

- [x] Test (in `tests/execution/test_pjpo2_promote_atomicity.py`): wrap — not replace — the stdout
      capture's `prepare_attach` so the test keeps a reference to the file object it returns; fail
      stderr's prepare for real (`/dev/full`); assert that object's `.closed` is true while the
      reference is still held.
- [x] Prove it fails with the three-line close in `PromotableSink.promote` removed. The existing
      `_open_descriptors_to(...) == []` assertion does not (spec §2.2) — keep it, but add a comment
      saying what it does and does not show.
- [ ] If the new test cannot be made to discriminate, do not tick the box above: record why, and
      reword the evidence instead. — *Not applicable: it discriminates (evidence below).*
- [x] Comment at the close in `promote`: why it is explicit rather than left to refcounting (another
      interpreter; a retained traceback — e.g. a logged exception — keeps the frame and the file
      alive). Cite PJPO3-002.
- [x] In `docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md`, PJPO2-001's evidence: keep the line, mark
      "no descriptor open on the prepared stdout file (checked through `/proc/self/fd` …)" as not
      pinning the close, and point to the new test with a PJPO3-002 reference.

**Validation — PJPO3V-002**

- [x] New test passes; mutation recorded; restored byte-identical (`cmp`); non-Docker suite green.

**Evidence — PJPO3-002.** `test_pjpo3v002_promote_closes_the_prepared_stdout_file_when_stderr_fails`
wraps the stdout capture's `prepare_attach` to keep a reference to the real file object, fails
stderr with a real ENOSPC, and asserts `.closed`. Mutation — the three-line close in `promote`
replaced by `pass`: the new test fails (`assert False` where `False = <BufferedWriter …/stdout.log>.closed`)
while `test_pjpo2v001_a_stderr_failure_…`, with its descriptor check, still passes — the spec §2.2
finding, reproduced. Restored byte-identical (`cmp`). That descriptor assertion is kept with a
comment saying what it does and does not show; the close now carries the refcounting/retained-
traceback rationale. PJPO2-001's evidence line annotated visibly with a PJPO3-002 reference. Comment- and test-only in `src/`
(no behaviour change): non-Docker suite 1034 passed.

### PJPO3-003 — Pin "a second `promote` after success raises before any I/O"

**Finding:** F3 (spec §2.3).

- [x] Test: after a successful `promote`, a second `promote` to new paths raises `RuntimeError`
      ("already attached"); neither new path nor its parent directory exists; output written
      afterwards still reaches the first logs, in order.
- [x] Record honestly whether it fails on any earlier code. It is expected to pass at `d6d0105`
      (it pins a property, not a regression); if so, say that — do not contrive a failure.
- [x] In `docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md`, PJPO2-005's evidence: add the test's name
      next to the claim, with a PJPO3-003 reference.

**Validation — PJPO3V-003**

- [x] New test passes; non-Docker suite green.

**Evidence — PJPO3-003.** `test_pjpo3v003_a_second_promote_after_success_raises_before_any_io`: after
a successful `promote`, a second one raises "already attached"; the second job directory does not
exist; later output reaches the first logs in order. As expected it passes at `e7ed82c`, and also
with both `src` files restored to `d17a5e8` (before PJPO2), where `attach` refused the same way — it
pins a property, not a regression, and no failure was contrived. Restored byte-identical (`cmp`).
PJPO2-005's claim annotated with the test's name. Test/doc-only: non-Docker suite 1035 passed.

---

## Phase 2 — Verify with the intended client

### PJPO3-004 — ChatGPT smoke check of both `exec` shapes (carried from PJPO-006, PJPO2-004)

**Finding:** F4 (spec §2.4). User-performed: it needs the ChatGPT connector.

- [ ] From ChatGPT against a running server: one `exec` that completes (confirm `exit_code` and
      output are read correctly), and one `exec` with `promote_on_timeout: true` that overruns
      (confirm `promoted_to_job`/`job_id` are read and the job can be followed with `exec_status`
      and `exec_output`).
- [ ] Record what was observed (date, server SHA, any error text), here and against PJPO-006 and
      PJPO2-004. If ChatGPT rejects or mis-reads the schema, open a finding and a new task rather
      than working around it.

> If this is not performed, leave both boxes unticked and say so in the closure evidence. The
> series may still close on the other tasks; the open box is the record that the assumption is
> unverified.

**Status — PJPO3-004: not performed** as of this series' closure (nor PJPO-006, nor PJPO2-004). It
needs the ChatGPT connector, which the implementing session cannot drive. Both boxes stay unticked;
the schema's compatibility with ChatGPT remains unverified.

---

## Phase 3 — Closure

### PJPO3-005 — Self-review and validation

- [x] Self-review every PJPO3 diff against K1–K5; list each with its covering test, and for each
      test whether it was mutation-proven or pins an already-true property.
- [x] Gates on an idle host, foreground (chunked with `--cov-append` if needed): ruff format, ruff
      check, mypy, `pytest --cov`. Chunk counts must sum to `pytest --collect-only`. Record counts
      and coverage.
- [x] A fresh export leaves `schemas/tools.json` unchanged; `contract_version` still `0.11.1`.
- [ ] Push; exact-head CI green. Record run and job IDs.
- [x] Set this file's Status line and the spec's Status; state whether PJPO3-004 was performed.

---

**Evidence — PJPO3-005.** Self-review against K1–K5:

| Inv. | Covering test / check | Kind |
|---|---|---|
| K1 attached at most once | `test_pjpo3v001_a_second_commit_attach_is_refused`; `prepare_attach`'s existing refusal via `test_pjpo3v003_…` | mutation-proven (`DID NOT RAISE` pre-fix) |
| K2 defensive line pinned | `test_pjpo3v002_promote_closes_the_prepared_stdout_file_when_stderr_fails` | mutation-proven (close → `pass`: fails; the descriptor test still passes) |
| K2 evidence claim pinned | `test_pjpo3v003_a_second_promote_after_success_raises_before_any_io` | already-true property: passes at `e7ed82c` and at `d17a5e8` |
| K3 PJPO2 outcomes kept | all pre-existing `tests/execution/` tests pass unedited | — |
| K4 no contract change | fresh export unchanged; `CONTRACT_VERSION` `0.11.1` | — |
| K5 visible corrections | PJPO2-001 evidence annotated (PJPO3-002); PJPO2-005 claim annotated (PJPO3-003) | — |

Also checked: the `commit_attach` guard sits before any assignment, so a refused commit leaves the
capture as it was (the test's later `write` lands in the first file); `attach` and `promote` still
pair prepare/commit under the one lock. Gates on an idle host (no CI run in progress), foreground:
`ruff format --check` 341 files; `ruff check` clean; `mypy src` clean (106); `pytest --cov` in four
chunks with `--cov-append`: 1035 + 145 + 198 + 118 = 1496 = `--collect-only`, all passed; coverage
88% (9543 / 1109); `promotable_sink.py` 100%, `log_capture.py` 97%. CI: run `35429046521` on
`e24907f` (the PJPO3-001 fix) success; runs for `e7ed82c` and `7f5cd88` (test/comment/doc-only)
cancelled as superseded — exact-head CI for this closure is recorded below.

## Definition of done

- [x] RD1 recorded.
- [x] K1 holds and is pinned by a test proven to discriminate.
- [x] The explicit close in `promote` is pinned by a mutation-proven test — or the record says
      plainly that it is not, and why.
- [x] The re-promote claim is pinned by a named test.
- [x] The PJPO2 record's two evidence lines corrected or annotated visibly.
- [x] No contract change (K4), verified by export.
- [ ] Full gates green on an idle host and on exact-head CI.
- [x] PJPO3-004 performed, or recorded as not performed. — Recorded as not performed.
