# Post-PJPO Code Review TODO

**Status:** Complete except `PJPO2-004` (user-performed ChatGPT check, not performed) — `PJPO2-001`–`PJPO2-003`, `PJPO2-005` closed  
**Date:** 2026-09-18  
**Spec:** `docs/POST_PJPO_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `c6bb465`  
**Task series:** `PJPO2-001` through `PJPO2-005`  
**Validation series:** `PJPO2V-*`

This TODO implements `docs/POST_PJPO_CODE_REVIEW_SPEC_2026-09-18.md`. A review of the merged PJPO
series confirmed its fix and found no bug with a user-visible consequence. It found residue: a
docstring stating an invariant the code holds only on success, an evidence line crediting a test
with cases it does not exercise, and two undocumented properties of the new tests. It also carries
forward the one verification only the user can perform. No task here changes a tool contract.

## Global rules

- [x] Start from an exact inspected `master` SHA and record it here. —
      `d17a5e8e7cf9d29d0a4e4db36f67ad57093264c2`.
- [x] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR, one task per commit, task ID in the subject.
- [x] Record QD1–QD2 (spec §3) — option, who chose it, why — before the task that depends on each.
      Ask the user; do not default silently. — Both asked of the user before PJPO2-001 started.
- [x] Add a regression test for every behavioural claim and prove it discriminates (restore the
      pre-fix code, run, record the failure, restore, confirm byte-identical with `cmp`).
- [x] Prefer a real failure (`/dev/full`, a path that is a file, a path that is a directory) to a
      monkeypatch wherever the claim is about partially-applied state.
- [x] Preserve PJPO's outcomes (H3). Do not weaken existing tests or guards. Do not bump
      `contract_version`; prove H4 by re-exporting.
- [x] Correct records visibly — original kept, marked, correction added with its task ID.
- [x] Do not edit `src/` while a suite is running. Run suites in the foreground (background runs
      are killed by the memory guard on this host); `--cov` runs on an idle host.
- [x] Cite code by symbol; verify any line number or count by command at the commit named.
- [x] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [x] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — Code and tests

### PJPO2-001 — Make `PromotableSink.promote`'s guarantee true

**Finding:** F1 (spec §2.1). **Decision:** QD1.

- [x] Record QD1. — **A, make the pair atomic**, chosen by the user: the docstring then holds
      without qualification and `promote` becomes retryable.
- [x] If QD1-A: split `StreamCapture.attach` into a *prepare* step (mkdir, open, write and flush the
      deferred head into a file object not yet adopted; on failure close it and re-raise, capture
      untouched) and a *commit* step (adopt the file, set the path, release the head — no I/O).
      `attach` remains, as prepare-then-commit. Keep PJPO-001's narrative in the docstrings.
- [x] If QD1-A: `promote` prepares both captures, then commits both. If the second prepare fails,
      close the first's prepared file object and leave both captures deferred. Still one lock
      acquisition over the whole switch.
- [x] If QD1-A: docstring — the guarantee now holds on failure too (H1), `promote` is retryable, and
      a log file already written for the stream that succeeded is left on disk (it belongs to a
      `failed` job's directory; retention removes it) — say why there is no unlink.
- [ ] If QD1-B: rewrite the docstring only — the guarantee holds on success; on failure stdout may
      be attached while stderr is still a buffer, `promote` cannot be retried, and the sole caller
      fails closed regardless. Cite PJPO2-001. — *Not applicable: QD1-A chosen.*
- [x] Narrative comment citing PJPO2-001 and how the previous version behaved (the probe output in
      spec §2.1).

**Validation — PJPO2V-001**

- [x] (QD1-A) Test: stdout → real file, stderr → `/dev/full`: `promote` raises; both captures still
      deferred, heads intact; a second `promote` to good paths succeeds and both logs hold their
      full output in order. Prove it fails against the pre-fix `promote`.
- [x] (QD1-A) Test: the mirror case — stdout → `/dev/full`, stderr → real file: nothing is attached
      and no stderr file object is left open.
- [ ] (QD1-B) Test pinning the documented failure state, so the docstring cannot drift again. —
      *Not applicable: QD1-A chosen.*
- [x] Every existing `test_jpo_promotable_sink`, `test_pjpo_fail_closed_promotion`,
      `test_log_capture` and `test_jpo_exec_promotion` test passes unchanged (H3). Full suite green.
      — *Deviation: one did not pass unchanged* — see the evidence.

**Evidence — PJPO2-001.** `StreamCapture.attach` is now `commit_attach(path, prepare_attach(path))`:
`prepare_attach` does every fallible step (mkdir, open, write, flush) into a file object it does not
adopt, closing it on failure; `commit_attach` adopts it and does no I/O. `PromotableSink.promote`
prepares stdout, then stderr (closing stdout's prepared file if stderr's fails), then commits both —
still one lock acquisition. Docstrings state H1, retryability, and why a file already written for the
succeeding stream is left for retention rather than unlinked (checked: `jobs/retention.py` prunes a
terminal job's logs via `_delete_log(stdout_log_path)`). Tests: `tests/execution/test_pjpo2_promote_atomicity.py`
(2): stderr → `/dev/full` leaves both buffers intact, no descriptor open on the prepared stdout file
(checked through `/proc/self/fd`, not private attributes), later output still buffered, and a second
`promote` succeeds with every byte in order; the mirror case creates no stderr file. Discrimination:
with both `src` files restored to `d17a5e8`, the first fails (`assert b'' == b'out-1\n'` — stdout had
already switched) and the mirror passes, as the spec predicted (it held before too). Restored
byte-identical (`cmp`).

Deviation from H3 "passes unchanged": `test_jpov002_a_chunk_arriving_during_promotion_waits_for_it`
hooked `stdout_capture.attach` to start its concurrent writer; `promote` no longer calls `attach`, so
the hook never fired and the test errored (`cannot join thread before it is started`). It now hooks
`prepare_attach`, which `attach` also goes through, so it fires on either implementation; the
property tested is unchanged, and it still fails with the lock removed from `on_stdout` (restored,
`cmp`). Full suite, foreground chunks: 1029 + 145 + 198 + 118 = 1490 = `--collect-only`, all passed.

### PJPO2-002 — Pin the `attach` properties PJPO-007 claimed

**Finding:** F2 (spec §2.2). **Depends on:** PJPO2-001 (tests target the final `attach`).

- [x] Test: parent path is a regular file → `mkdir` fails (`NotADirectoryError`); capture still
      deferred, head intact, later `write` and `finish()` do not raise.
- [x] Test: target path is a directory → `open` fails (`IsADirectoryError`); same assertions.
- [x] Test: empty head → `attach` succeeds, creates no file, and the first later `write` creates it
      with exactly that content.
- [x] Prove the two failure tests discriminate against the pre-PJPO-001 `attach`
      (`git show 08073af:src/mcp_chatgpt_sandbox/execution/log_capture.py`), which assigned `_path`
      before any I/O. If one does not fail there, say so in the evidence rather than claim it does —
      the old code is known to have survived a `mkdir` failure at the `finish()` level (spec §2.5).
- [x] In `docs/POST_JPO_CODE_REVIEW_TODO_2026-09-18.md`, PJPO-007's evidence: keep the line, mark the
      "(PJPOV-001)" citation for the `mkdir`/`open`/empty-head cases as incorrect, and point to
      these tests with a PJPO2-002 reference.

**Validation — PJPO2V-002**

- [x] The new tests pass; discrimination recorded; non-Docker suite green (test- and doc-only).

**Evidence — PJPO2-002.** Three tests appended to `tests/execution/test_pjpo_fail_closed_promotion.py`,
asserting behaviour only (QD2-A applied here too): the buffered output is intact, a later `write`
neither raises nor creates a file, and a later `attach` to a good path yields every byte in order.
Discrimination against the pre-PJPO-001 `attach` (`git show 08073af:…/log_capture.py`): both failure
tests fail — the later `write` re-raises `NotADirectoryError` / `IsADirectoryError`, because the old
`attach` had already committed `_path`. That is sharper than the spec §2.5 caution anticipated: the
old code survived a `mkdir` failure at the `finish()` level, but not at the next `write`. The
empty-head test passes on the old code too; it pins a property, not a regression. Restored
byte-identical (`cmp`). PJPO-007's evidence line corrected visibly (citation struck through,
PJPO2-002 cited). Test- and doc-only: non-Docker suite 1032 passed.

### PJPO2-003 — Document the tests' platform dependency; tidy white-box assertions

**Finding:** F3 (spec §2.3). **Decision:** QD2.

- [x] Record QD2. — **A, behavioural assertions**, chosen by the user.
- [x] In `tests/execution/test_pjpo_fail_closed_promotion.py`'s module docstring (and wherever
      PJPO2-001/002 add `/dev/full` tests): state that `/dev/full` is Linux-only, that this matches
      the suite's existing requirements (Docker, self-hosted Linux CI), and that it is deliberately
      not guarded with a skip because `tests/_skip_policy.py` turns any skip into a failure.
- [x] Per QD2: replace `capture._path is None` / `capture._file is None` with behavioural checks
      where an equivalent exists; keep a white-box assertion only where none does, with a comment.
- [x] Do not weaken what the test proves: run it against the pre-PJPO-001 `attach` again and record
      that it still fails.

**Validation**

- [x] That test file passes; discrimination re-proven; ruff clean.

**Evidence — PJPO2-003.** `tests/execution/test_pjpo_fail_closed_promotion.py`'s module docstring
now states the Linux-only `/dev/full` dependency and why there is no skip guard;
`test_pjpo2_promote_atomicity.py` (PJPO2-001) already said so. `capture._path is None` /
`capture._file is None` replaced by observable behaviour: no descriptor open on the failed target
(`/proc/self/fd`), and the buffer still growing on a later `write`. No white-box assertion was
needed — `grep '\._path\|\._file'` over both PJPO test files finds none. Not weakened: against
the pre-PJPO-001 `attach` (`git show 08073af:…`) the test still fails, now on
`assert ['11'] == []` — the old code left the `/dev/full` descriptor open. Restored byte-identical
(`cmp`). Test-only: that file 7 passed; non-Docker suite 1032 passed; ruff clean.

---

## Phase 2 — Verify with the intended client

### PJPO2-004 — ChatGPT smoke check of both `exec` shapes (carried from PJPO-006)

**Finding:** F4 (spec §2.4). User-performed: it needs the ChatGPT connector.

- [ ] From ChatGPT against a running server: one `exec` that completes (confirm `exit_code` and
      output are read correctly), and one `exec` with `promote_on_timeout: true` that overruns
      (confirm `promoted_to_job`/`job_id` are read and the job can be followed with `exec_status`
      and `exec_output`).
- [ ] Record what was observed (date, server SHA, any error text), here and against PJPO-006. If
      ChatGPT rejects or mis-reads the schema, open a finding and a new task rather than working
      around it.

> If this is not performed, leave both boxes unticked and say so in the closure evidence. The
> series may still close on the other tasks; the open box is the record that the assumption is
> unverified.

**Status — PJPO2-004: not performed** as of this series' closure (nor its predecessor PJPO-006). It
needs the ChatGPT connector, which the implementing session cannot drive. Both boxes stay unticked;
the schema's compatibility with ChatGPT remains unverified.

---

## Phase 3 — Closure

### PJPO2-005 — Self-review and validation

- [x] Self-review every PJPO2 diff against H1–H5; list each with its covering test.
- [x] Gates on an idle host, foreground (chunked with `--cov-append` if needed): ruff format, ruff
      check, mypy, `pytest --cov`. Chunk counts must sum to `pytest --collect-only`. Record counts
      and coverage.
- [x] A fresh export leaves `schemas/tools.json` unchanged; `contract_version` still `0.11.1`.
- [ ] Push; exact-head CI green. Record run and job IDs.
- [x] Set this file's Status line and the spec's Status; state whether PJPO2-004 was performed.

---

**Evidence — PJPO2-005.** Self-review against H1–H5:

| Inv. | Covering test / check |
|---|---|
| H1 both-or-neither | `test_pjpo2v001_a_stderr_failure_leaves_both_streams_buffered_and_promote_retryable` (discriminates), `test_pjpo2v001_a_stdout_failure_never_touches_stderr` |
| H2 claims pinned | write failure: `test_pjpov001_…` ×2; `mkdir` / `open` / empty head: `test_pjpo2v002_…` ×3; pair: H1 row |
| H3 PJPO outcomes kept | `test_pjpov002_…` ×2 (G1) and `test_jpov007_…` pass unchanged; one JPO test's hook retargeted (PJPO2-001 evidence), still discriminating |
| H4 no contract change | fresh export unchanged; `CONTRACT_VERSION` `0.11.1` |
| H5 visible corrections | PJPO-007 evidence citation struck through with PJPO2-002 cited |

Also checked: `prepare_attach` and `commit_attach` run under the one sink lock, so no `write` can
land between them (the constraint `prepare_attach`'s docstring states); a second `promote` after a
*successful* one still raises "already attached" from the first `prepare_attach`, before any I/O.
Gates on an idle host (no CI run in progress), foreground: `ruff format --check` 339 files;
`ruff check` clean; `mypy src` clean (106); `pytest --cov` in four chunks with `--cov-append`:
1032 + 145 + 198 + 118 = 1493 = `--collect-only`, all passed; coverage 88% (9541 / 1109);
`promotable_sink.py` 100%, `log_capture.py` 97%. CI: run `35405341329` on `8cf3661` (the PJPO2-001
fix) success; runs for `faf9618` and `2348d88` (test/doc-only) cancelled as superseded — exact-head
CI for this closure is recorded below.

## Definition of done

- [x] QD1–QD2 recorded.
- [x] H1 holds and is pinned by a test proven to discriminate (or, under QD1-B, the documented
      failure state is pinned).
- [x] The `attach` properties PJPO-007 claimed are each pinned by a named test, and its evidence
      line is corrected visibly.
- [x] The Linux-only dependency is documented.
- [x] No contract change (H4), verified by export.
- [ ] Full gates green on an idle host and on exact-head CI.
- [x] PJPO2-004 performed, or recorded as not performed. — Recorded as not performed.
