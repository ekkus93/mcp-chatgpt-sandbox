# Job Promotion on Overrun TODO

**Status:** Not started — `JPO-001` through `JPO-010` open  
**Date:** 2026-09-18  
**Spec:** `docs/JOB_PROMOTION_SPEC_2026-09-18.md`  
**Baseline:** `13e44bd`  
**Task series:** `JPO-001` through `JPO-010`  
**Validation series:** `JPOV-*`

This TODO implements `docs/JOB_PROMOTION_SPEC_2026-09-18.md`: `docs/SPEC.md` §14.4's optional
conversion of a synchronous `exec` that outlives the synchronous threshold into a background job,
keeping the running process and its output. It closes the open checkbox at `docs/TODO.md:353`.

## Global rules

- [ ] Start from an exact inspected `master` SHA and record it here.
- [ ] Follow the project's commit convention: direct conventional commits to `master`, no branch or
      PR, one task per commit, task ID in the subject.
- [ ] Record decisions D1–D4 (spec §3) — option, who chose it, why — before starting the first task
      that depends on each. Ask the user; do not default silently.
- [ ] Invariant I1: with the flag absent or false, `exec` takes today's code path. Any diff to that
      path needs a stated reason in the commit.
- [ ] Add a regression test for every behavioral claim and prove it discriminates (run it against
      the pre-change code or with the guarded behaviour disabled; record the failure).
- [ ] Do not weaken existing tests or guards. Do not raise the `tools/execution.py` line budget.
- [ ] Do not edit `src/` while a suite is running. Do not pipe a background pytest through `tail`.
- [ ] Run `--cov` suites on an idle host; non-coverage runs may overlap CI.
- [ ] Cite code by symbol; verify any line number or count by command at the commit named.
- [ ] Word every checkbox for what was done, never for what was considered and declined (VR-000b).
- [ ] Record exact SHA/run/job evidence as each phase closes.

---

## Phase 1 — Decide and prove the contract shape

### JPO-001 — Record D1–D4 and prove the output schema on the real server

**Goal:** the contract shape is settled by evidence before any execution code changes.

- [ ] Record D1 (when promotion happens). Recommended: A, per-call `promote_on_timeout: bool = false`.
- [ ] Record D2 (schema shape). Recommended: A, one flat object with a two-branch `oneOf`.
- [ ] Record D3 (`contract_version`). Recommended under D1-A/D2-A: `0.11.0`, with the reasoning.
- [ ] Record D4 (promoted job's deadline). Recommended: A, `default_job_timeout_seconds` from the
      original start.
- [ ] Write the new `ExecOutput` model (`tools/execution_models.py`) per D2, including a validator
      that rejects any value not matching exactly one branch (I9), with each `oneOf` branch pinning
      non-null types for its own fields and `null` for the other's (spec §2.5).
- [ ] Confirm against the real server (not a probe server): top-level `type: object`, the `oneOf`
      present, structured content flat — `exit_code` still at the top level, not under `result`.
- [ ] `run_exec` returns `promoted_to_job=False`; no other behaviour change in this task.

**Validation — JPOV-001, JPOV-008**

- [ ] Test: exported `exec` output schema has `type: object`, `promoted_to_job` required, a
      two-branch `oneOf` keyed on `const false` / `const true`.
- [ ] Test: the model raises on (a) `promoted_to_job=True` without `job_id`, (b) `False` without
      `exit_code`, (c) fields from both branches set. Prove it discriminates by removing the validator.
- [ ] Existing `exec` tests pass unchanged apart from the added field.

### JPO-002 — Contract export for the new shape

**Depends on:** JPO-001. Follow the `tool-contract` skill.

- [ ] Add the input field per D1 (if D1-A) with a description that says what it does and what the
      caller must do with a promoted response. It is accepted but inert until JPO-006 — say so in
      the commit, and do not advertise promotion in the tool description yet.
- [ ] Re-export `schemas/tools.json`; bump `CONTRACT_VERSION` per D3; record the §33.2 reasoning
      next to the bump.
- [ ] `docs/TOOLS.md` unchanged unless the `exec` description changes (it does in JPO-007).

**Validation**

- [ ] Schema export test, contract-version monotonicity check and `docs/TOOLS.md` drift test pass.

> If shipping an inert flag for several commits is unwanted, fold JPO-002 into JPO-006 and record
> that here. The order above exists so the contract diff is reviewed on its own.

---

## Phase 2 — Mechanism

### JPO-003 — Switchable sink

**Goal:** spec §5.1. New module `execution/promotable_sink.py`; nothing calls it yet.

- [ ] Buffering mode: per-stream bounded capture with the same retention rule `consume` applies
      (`max_output_bytes`), counting every byte produced.
- [ ] `promote(stdout_capture, stderr_capture)`: under one lock, flush buffers into the captures in
      order, carry the byte counts over, release the buffers, route later chunks to the captures.
- [ ] If `StreamCapture` cannot account for bytes it never received (dropped by the buffer's cap
      before promotion), extend it minimally so `total_bytes` and the omitted-bytes marker stay
      truthful (I3). State why the extension is needed in a comment citing this task.
- [ ] `result()` returns the same `ExecResult` tails/flags `consume` would for the same bytes.
- [ ] Comment the non-obvious choice: why buffer-then-flush rather than log files from the start
      (spec §5.1).

**Validation — JPOV-002**

- [ ] Ordering: bytes written before and after `promote` appear in the log in order, none twice.
- [ ] Counts: totals include pre-promotion bytes, including bytes beyond the buffer cap.
- [ ] Bound: buffer never exceeds the cap; it is released after `promote` (I7).
- [ ] Concurrency: a writer thread hammering `on_stdout` while `promote` runs loses and duplicates
      nothing (I8). Prove it discriminates by removing the lock.
- [ ] Parity: for identical input, `result()` equals what `consume` returns.

### JPO-004 — Runner adoption

**Goal:** spec §5.2. `AsyncJobRunner.adopt_running(...)`.

- [ ] Factor the finalisation tail out of `_run` (timeout → `TIMED_OUT`, cancel, termination
      failure, `mark_finished`, `_live.pop`) so `start_job` and `adopt_running` share it. No
      behaviour change for `exec_start` jobs.
- [ ] `adopt_running` registers a `LiveJob` for an already-draining exec and completes it through
      the shared tail, with the deadline per D4.
- [ ] No second reader is ever opened on the stream; say why in a comment.

**Validation**

- [ ] Every existing async-runner and `exec_start`/`exec_status`/`exec_cancel` test passes unchanged.
- [ ] Unit test: an adopted job reaches `succeeded` / `failed` with the right exit code, reaches
      `timed_out` at the deadline, and is cancellable.

### JPO-005 — Make room in `tools/execution.py`

**Goal:** spec §2.6. The coordinator is 797 lines against a `< 800` guard.

- [ ] Create `tools/execution_promotion.py` for the opted-in path.
- [ ] If wiring still does not fit, move a cohesive existing helper group out of
      `tools/execution.py` in a **behaviour-free** commit of its own (pure move, imports updated).
- [ ] Record the before/after line counts by command.

**Validation**

- [ ] `tests/test_refactored_module_size_guard.py` passes; full suite green on the pure move.

---

## Phase 3 — Behaviour

### JPO-006 — Promote on overrun

**Depends on:** JPO-001…005. Spec §5.3.

- [ ] Opted-in path: `start_and_open_stream`, drain in a thread through the sink, wait up to the
      resolved sync timeout.
- [ ] Finished in time → completed result from `sink.result()`; **no** job record or directory (I4).
- [ ] Overrun → check the drain future once more; if it has finished, return the completed result
      (I4). Otherwise create the job directory and captures, `job_store.create` /
      `mark_starting` / `mark_running(pid=)`, `sink.promote`, `runner.adopt_running`, inline
      `prune_job_logs`, and return `promoted_to_job=True` with `job_id`, `state`, `started_at`
      (original start, D4).
- [ ] `JobRecord.command` and `cwd` equal what `exec_start` records for the same input (I6).
- [ ] Any failure during promotion → kill exactly as the timeout path does, leave no non-terminal
      record, raise `COMMAND_TIMEOUT` (I5).
- [ ] Flag absent/false → the `13e44bd` path, untouched (I1).
- [ ] Narrative comment at the race check and at the fail-closed block: what the obvious
      alternative was and why it is wrong.

**Validation — JPOV-003 … JPOV-007** (Docker-backed; use a short `sync_timeout_seconds` via config,
never a sleep-and-hope margin)

- [ ] JPOV-003: flag false + overrun → `COMMAND_TIMEOUT`, process gone, no job record, no job dir.
- [ ] JPOV-004: flag true, finishes in time → completed shape, no job record.
- [ ] JPOV-005: marker, sleep past the threshold, marker, exit 0 → handle; `exec_status` reaches
      `succeeded`/0; `exec_output` from offset 0 has both markers in order; pid unchanged (I2, I3).
- [ ] JPOV-005b: non-zero exit after promotion → `failed` with that exit code.
- [ ] JPOV-006: promoted job is cancellable; hits `timed_out` at the D4 deadline; the idle-stop
      sweep skips its workspace while it runs.
- [ ] JPOV-007: injected job-dir and job-store failures → `COMMAND_TIMEOUT`, process gone, no
      non-terminal record.
- [ ] Prove JPOV-005 discriminates (e.g. disable the pre-promotion flush → first marker missing).

### JPO-007 — Audit, description, documentation

- [ ] Audit record at promotion linking the exec to `job_id`, following the existing
      `_audit_command_started` pattern and redaction rules (I10). Verify the event naming against
      `audit.py` before choosing a name.
- [ ] `exec` tool description states both shapes and that a promoted job is followed with
      `exec_status` / `exec_output` / `exec_cancel`. Re-export the schema; bump the version again
      only if §33.2 requires it (a description change is a patch) and record which.
- [ ] `docs/TOOLS.md` row for `exec` updated to match.
- [ ] `docs/SPEC.md` §14.4: add the input flag and the deadline rule.
- [ ] Rewrite the "not built" paragraph in `execution/service.py`'s module docstring to describe
      what exists.

**Validation — JPOV-009**

- [ ] Test: the promotion audit record carries `workspace_id` and `job_id`; a secret placed in
      `argv` and in `env` does not appear in it.
- [ ] Schema export, version and `docs/TOOLS.md` drift checks pass.

---

## Phase 4 — Closure

### JPO-008 — Self code review

- [ ] Re-read every JPO diff against invariants I1–I10; list each invariant with the test that
      covers it.
- [ ] Check: thread/loop boundaries (sqlite connection is thread-affine — job-store calls stay on
      the event loop thread), no blocking call on the loop, no leaked drain thread on any error path.
- [ ] Check: no new pytest skip, retry or widened timeout; no `Path.cwd()` / `.parents[n]` in
      production code; module budgets.
- [ ] Record findings, fixed or not, honestly.

### JPO-009 — Full validation

- [ ] `/gates` on an idle host: `ruff format --check`, `ruff check`, `mypy src`, `pytest --cov`.
      Record counts and coverage.
- [ ] Push; exact-head CI green. Record run and job IDs.
- [ ] Schema byte-identical to export; `contract_version` monotonic and equal to D3's value.

### JPO-010 — Close the record

- [ ] Tick `docs/TODO.md:353` with a pointer to this series (only now — VR-000b).
- [ ] Set this file's Status line to Complete; set the spec's Status to Implemented.
- [ ] Note any deviation from the spec, and any slip made during the series, in the task evidence.

---

## Definition of done

- [ ] D1–D4 recorded.
- [ ] I1–I10 each covered by a named test proven to discriminate.
- [ ] `tools/execution.py` under 800 lines.
- [ ] `schemas/tools.json`, `CONTRACT_VERSION`, `docs/TOOLS.md`, `docs/SPEC.md` §14.4 consistent.
- [ ] `docs/TODO.md:353` ticked.
- [ ] Full gates green on an idle host and on exact-head CI.
