# Post-JPO Code Review TODO

**Status:** In progress — `PJPO-001` closed  
**Date:** 2026-09-18  
**Spec:** `docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md`  
**Reviewed baseline:** `613255f`  
**Task series:** `PJPO-001` through `PJPO-007`  
**Validation series:** `PJPOV-*`

This TODO implements `docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md`. A review of the merged JPO
series (job promotion on overrun) found one real defect — a genuine disk-full failure during
promotion leaves the job record non-terminal, pinning its workspace against idle-stop until a
restart — plus three inaccurate statements in the record, an unverified client-compatibility
assumption, and a test nit. No task here changes a tool contract.

## Global rules

- [x] Start from an exact inspected `master` SHA and record it here. —
      `08073af707fd497b44afd750757cf727dd9eb278`.
- [ ] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR, one task per commit, task ID in the subject.
- [x] Record PD1–PD3 (spec §3) — option, who chose it, why — before the task that depends on each.
      Ask the user; do not default silently. — All three, and the commit granularity for
      PJPO-003–005 (separate), asked of the user before PJPO-001 started.
- [ ] Add a regression test for every behavioural claim and prove it discriminates (restore the
      pre-fix code, run, record the failure, restore, confirm byte-identical with `cmp`).
- [ ] Prefer a real failure to a monkeypatch wherever the defect is about partially-applied state:
      that is exactly what JPOV-007b's monkeypatch hid.
- [ ] Do not weaken existing tests or guards. Do not bump `contract_version`; prove G4 by
      re-exporting.
- [ ] Correct the JPO record visibly — keep the original claim, mark it, add the correction and its
      task ID. Never rewrite history silently.
- [ ] Do not edit `src/` while a suite is running. Run `--cov` suites on an idle host, in the
      foreground (background runs are killed by the memory guard on this host).
- [ ] Cite code by symbol; verify any line number or count by command at the commit named.
- [ ] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [ ] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — The defect

### PJPO-001 — Make promotion's fail-closed cleanup independent of the failed I/O

**Finding:** F1 (spec §2.1). **Decision:** PD1.

- [x] Record PD1. — **A, terminalise first *and* make `attach` failure-atomic**, chosen by the user:
      reordering alone would leave `attach` able to strand a capture half-attached.
- [x] `_fail_half_created_job` (`tools/execution_promotion.py`): call `job_store.mark_finished`
      before any capture I/O, in its own suppress scope; finish each capture in its own scope
      after. Byte counts and `truncated` are in-memory counters and need no file, so the record
      loses nothing by going first.
- [x] `StreamCapture.attach` (`execution/log_capture.py`, if PD1-A): set `_path` only after the head
      is written and flushed. On failure, close and drop the partial file object (suppressing a
      second error from the close), leave `_path` `None` and the deferred head intact, and re-raise.
- [x] Update `_fail_half_created_job`'s docstring: say what it now guarantees (G1) and what remains
      best-effort (a job store that is itself broken — restart reconciliation stays the backstop).
- [x] Narrative comment at each change citing PJPO-001 and saying how the previous order failed:
      `finish()` re-raised the ENOSPC while closing, and the shared suppress skipped
      `mark_finished`.

**Validation — PJPOV-001, PJPOV-002, PJPOV-003**

- [x] PJPOV-001 (unit, real I/O): a deferred capture holding data, attached to `/dev/full`, raises;
      afterwards `_path is None`, `buffered_head()` unchanged, no open file; `finish()` does not
      raise. Prove it fails against the pre-fix `attach`.
- [x] PJPOV-002 (unit): the spec §2.1 reproduction as a test — `/dev/full` promotion failure, then
      `_fail_half_created_job` with a recording job store — reaches `mark_finished` with
      `state=FAILED`. Prove it fails against the pre-fix ordering.
- [x] PJPOV-003 (tool level, real Docker): replace `test_jpov007_a_log_file_failure_during_promotion_leaves_no_live_job_record`'s
      monkeypatch of `StreamCapture.attach` with a real write failure (e.g. the job log path
      resolving to `/dev/full`, or an unwritable `jobs/` directory — record which, and why the other
      was rejected). Still asserts `COMMAND_TIMEOUT`, the process gone, the record `failed`. Prove
      it fails against the pre-fix code, which the monkeypatched version never could.
- [x] Full suite green.

**Evidence — PJPO-001.** `_fail_half_created_job` now calls `mark_finished` first under its own
suppress, then finishes each capture in its own scope. `StreamCapture.attach` opens and writes the
head into a local file object and commits `_file`/`_path` only after the flush succeeds; on failure
it closes that object (the repeated ENOSPC from the close suppressed) and re-raises, leaving the
capture deferred with its head intact. Tests: `tests/execution/test_pjpo_fail_closed_promotion.py`
(4 — PJPOV-001 ×2 with real `/dev/full` writes; PJPOV-002 ×2), and
`test_jpov007_a_log_file_failure_during_promotion_leaves_no_live_job_record` rewritten (PJPOV-003):
the job's `stdout.log` is pre-created as a symlink to `/dev/full` and only `new_job_id` is pinned.
The unwritable-`jobs/` alternative was rejected in the test's docstring: `mkdir` fails before any
file opens — the one failure the old code already survived — so it would not discriminate.
Discrimination: with both `src` files restored to `08073af` (`git show HEAD:<file>`), all 5 fail;
the tool-level test fails with `('job_diskfull', 'queued') == ('job_diskfull', 'failed')` — the
review's reproduction, end to end. Both files restored byte-identical (`cmp`). Full suite, foreground
chunks: 1027 + 145 + 198 + 118 = 1488 = `--collect-only`, all passed (overlapping CI's `--cov` run
`35398231267`, non-`--cov` here).

---

## Phase 2 — Make the record true

### PJPO-002 — Document the synchronous switch; correct JPO-008's loop claim

**Finding:** F2 (spec §2.2). **Decision:** PD2.

- [x] Record PD2. — **A, keep the switch synchronous**, chosen by the user.
- [x] If PD2-A: a narrative comment at the `sink.promote(...)` call site in
      `run_promotable_exec`: it does bounded file I/O (≤ 2 × `max_output_bytes`) on the event loop
      while holding the sink lock, deliberately, because moving it to a thread puts an `await`
      between the `drain.done()` check and `adopt_running`; cite PJPO-002 and JPO's I4.
- [ ] If PD2-B: implement per spec §3 PD2-B, restate the I4 argument in the comment, and add a test
      for the case it newly admits (a command that finishes during the switch becomes a job that
      completes at once with its real exit code). — *Not applicable: PD2-A chosen.*
- [x] In `docs/JOB_PROMOTION_TODO_2026-09-18.md`, JPO-008: keep the ticked check and its evidence
      line, mark the words "no blocking call on the loop" / "nothing blocking runs on the loop" as
      incorrect, and add the correction with a PJPO-002 reference.

**Validation**

- [x] Comment/doc-only under PD2-A: ruff, mypy, the non-Docker suite, and a fresh export unchanged.
      Under PD2-B: the full suite.

**Evidence — PJPO-002.** Comment above `sink.promote(...)` in `run_promotable_exec`. JPO-008's check
and evidence line in `docs/JOB_PROMOTION_TODO_2026-09-18.md` kept, the wrong claim struck through
and the correction added with a PJPO-002 reference. Comment/doc only: ruff, mypy clean; non-Docker
suite 1027 passed; a fresh `export-schemas` leaves `schemas/tools.json` unchanged.

### PJPO-003 — Correct the nullable-field count

**Finding:** F3 (spec §2.3).

- [x] `src/mcp_chatgpt_sandbox/schema_export.py`, the `0.11.0` comment: "eight existing fields
      became nullable" → seven, noting `pressure` already was. Comment only — `CONTRACT_VERSION`
      unchanged.
- [x] `docs/JOB_PROMOTION_SPEC_2026-09-18.md` D2-A and D3: correct "eight" to "seven" visibly
      (original kept, marked, PJPO-003 cited). Leave the correct "eight properties" statement in
      §2.4 alone.
- [x] Re-derive the number by command (the schema diff between `c7cc1b7` and `d9f4f24`) and record
      the command's output as evidence.

**Validation**

- [x] A fresh `export-schemas` leaves `schemas/tools.json` unchanged (G4); non-Docker suite green.

**Evidence — PJPO-003.** Re-derived by script (the `exec` output schema at `c7cc1b7` vs `d9f4f24`):
`existing: 8; became nullable: 7 [exit_code, stderr_tail, stderr_total_bytes, stderr_truncated,
stdout_tail, stdout_total_bytes, stdout_truncated]; already nullable: [pressure]`. Corrected in the
`schema_export.py` `0.11.0` comment (noting the old wording) and visibly in the JPO spec's D2-A and
D3 (struck through, PJPO-003 cited). `CONTRACT_VERSION` untouched; a fresh `export-schemas` leaves
`schemas/tools.json` unchanged; ruff, mypy clean; non-Docker suite 1027 passed.

### PJPO-004 — Correct the `execution_support.py` docstring

**Finding:** F4 (spec §2.4). **Decision:** PD3.

- [ ] Record PD3. Recommended: A — docstring only.
- [ ] Say what the promotion module actually imports from here (`kill_after_timeout`,
      `new_job_id`), why the audit helpers are public (imported by `tools/execution.py` from a
      different module), and mention `new_job_id` and the task that added it (JPO-006).
- [ ] If PD3-B: move the audit helpers back as a behaviour-free commit of their own, and confirm
      `tools/execution.py` stays under 800 lines by `wc -l`.

**Validation**

- [ ] ruff, mypy, non-Docker suite (PD3-A); full suite (PD3-B).

### PJPO-005 — Test nit

**Finding:** F6 (spec §2.6).

- [ ] `test_jpo008_run_exec_refuses_the_flag_when_it_has_nothing_to_promote_with`: drop the unused
      binding (`_`, or bind only what is used).

**Validation**

- [ ] That test file passes.

> PJPO-003, PJPO-004 and PJPO-005 are record-only and may share one commit if the user prefers;
> record that choice here if so.

---

## Phase 3 — Verify with the intended client

### PJPO-006 — ChatGPT smoke check of both `exec` shapes

**Finding:** F5 (spec §2.5). User-performed: it needs the ChatGPT connector, which this session
cannot drive.

- [ ] From ChatGPT against a running server: one `exec` that completes (confirm `exit_code` and
      output are read correctly), and one `exec` with `promote_on_timeout: true` that overruns
      (confirm `promoted_to_job`/`job_id` are read and the job can be followed with `exec_status`
      and `exec_output`).
- [ ] Record what was observed (date, server SHA, any error text). If ChatGPT rejects or
      mis-reads the schema, open a finding and a new task here rather than working around it.

> If this is not performed, leave both boxes unticked and say so in the closure evidence. The
> series may still close on PJPO-001–005 and PJPO-007; the open box is the record that the
> assumption is unverified.

---

## Phase 4 — Closure

### PJPO-007 — Correct the JPO closure record and validate

- [ ] In `docs/JOB_PROMOTION_TODO_2026-09-18.md`: the JPO-008 invariant table's I5 row cites both
      `test_jpov007_…` tests; add that the log-file one did not exercise a real failure until
      PJPO-001. JPO-010 / Definition of done: note that I5 was not met at `613255f` and was met
      from PJPO-001's commit. Visible corrections, originals kept.
- [ ] Self-review every PJPO diff against G1–G5.
- [ ] `/gates` on an idle host, in the foreground (chunked with `--cov-append` if needed): ruff
      format, ruff check, mypy, `pytest --cov`. Record counts and coverage; the chunk counts must
      sum to `pytest --collect-only`.
- [ ] Push; exact-head CI green. Record run and job IDs.
- [ ] A fresh export leaves `schemas/tools.json` unchanged; `contract_version` still `0.11.1`.
- [ ] Set this file's Status line and the spec's Status; state whether PJPO-006 was performed.

---

## Definition of done

- [ ] PD1–PD3 recorded.
- [ ] The spec §2.1 reproduction reaches `mark_finished` (PJPOV-002), and a real I/O failure at tool
      level leaves the record `failed` (PJPOV-003), both proven to discriminate.
- [ ] G2 holds: no half-attached `StreamCapture` after a failed `attach` (PJPOV-001).
- [ ] The JPO record's three inaccurate statements corrected visibly.
- [ ] No contract change (G4), verified by export.
- [ ] Full gates green on an idle host and on exact-head CI.
- [ ] PJPO-006 performed, or recorded as not performed.
