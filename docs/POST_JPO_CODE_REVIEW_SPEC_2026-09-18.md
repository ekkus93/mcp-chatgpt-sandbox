# Post-JPO Code Review — Specification

**Status:** Draft — not started
**Date:** 2026-09-18
**Reviewed baseline:** `613255f` (`master`; closure CI run `35376222496` green: 1484 passed, 88%)
**Source review:** `/code-review-todo docs/JOB_PROMOTION_TODO_2026-09-18.md`, 2026-09-18
**Task series:** `PJPO-nnn` (`docs/POST_JPO_CODE_REVIEW_TODO_2026-09-18.md`)
**Validation series:** `PJPOV-nnn`

---

## 1. Purpose

A review of the merged JPO series (`d9f4f24`–`613255f`, job promotion on overrun,
`docs/JOB_PROMOTION_SPEC_2026-09-18.md`) found the feature sound on its success paths
and one real defect on its failure path: a genuine disk-full error while promotion
switches output to the job's log files leaves the job record non-terminal. That is the
exact outcome JPO invariant I5 exists to prevent, and the test written to guard it
cannot observe it. The review also found three places where the written record says
something the code does not, one unverified client-compatibility assumption, and a
test nit.

This spec fixes the defect, makes the record true, and closes the verification gap.
It changes no tool contract.

## 2. Review baseline and evidence

Everything below was checked by command at `613255f`, not recalled.

### 2.1 F1 — fail-closed cleanup depends on the operation that failed

`tools/execution_promotion.py`, `_fail_half_created_job` (line 212) runs, inside a
single `contextlib.suppress(Exception)` (line 219):

```python
record = job_store.get(job_id)
if record.state in NON_TERMINAL_STATES:
    sink.stdout_capture.finish()
    sink.stderr_capture.finish()
    job_store.mark_finished(job_id, state=JobState.FAILED, ...)
```

`execution/log_capture.py`, `StreamCapture.attach` (line 85) assigns `self._path = path`
(line 92) *before* opening, writing and flushing the deferred head. When that I/O fails,
the capture is left half-attached: `_path` set, file open, head still in memory, data
still in the file object's buffer.

Reproduced with a real ENOSPC (`/dev/full`), not a monkeypatch:

```
promote raised: [Errno 28] No space left on device
after failed attach: _path = /dev/full | deferred head kept = 26 | file open = True
finish() raised: [Errno 28] No space left on device
mark_finished reached: None
```

`finish()` re-raises (closing the file flushes the stuck buffer), the suppress swallows
it, and `mark_finished` never runs. At that point the record is `queued` (created, not
yet adopted), which is in `NON_TERMINAL_STATES`: idle-stop
(`workspace/manager_lifecycle.py`, `sweep_idle_workspaces`) treats the workspace as busy
until a daemon restart reconciles the job to `lost`. The client received
`COMMAND_TIMEOUT` and has no `job_id`, so nothing it can call clears it. This is HR-021's
defect by another route — which `_fail_half_created_job`'s own docstring says it
prevents.

**Why the test missed it.** `test_jpov007_a_log_file_failure_during_promotion_leaves_no_live_job_record`
(`tests/workspace/test_jpo_exec_promotion.py:302`) replaces `StreamCapture.attach` with a
function that raises before touching any state, so a half-attached capture never exists
in the test.

A second, smaller consequence of the non-atomic `attach`: after a failed attach the
drain thread's next write goes to the broken file and raises inside `drain_stream`,
stopping the reader. Harmless here — the process is being killed anyway — but it is
one more path whose behaviour depends on the partially-applied state.

### 2.2 F2 — JPO-008 records a check whose conclusion is false

`docs/JOB_PROMOTION_TODO_2026-09-18.md:292` ticks "no blocking call on the loop" and
line 320 records "nothing blocking runs on the loop". But `sink.promote(...)`
(`execution_promotion.py:151`) runs on the event-loop thread and performs `mkdir`,
`open`, `write` and `flush` of up to `2 × max_output_bytes` (default 10 MiB each,
`config/models.py:244`) while holding the lock the drain thread needs. The same holds
for `finish()` in the fail-closed path.

This is deliberate and bounded: keeping the switch synchronous is what makes JPO's I4
argument hold (no `await` between the `drain.done()` check and `adopt_running`). The
defect is that the record says the opposite and the call site does not explain the
trade-off. Under VR-000b a ticked box must describe what is true.

### 2.3 F3 — a field count is wrong in three places

Diffing the `exec` output schema between `c7cc1b7` and `d9f4f24` (script): of eight
existing properties, **seven** became nullable; `pressure` was nullable before. The
record says eight in:

- `src/mcp_chatgpt_sandbox/schema_export.py:77` — the `0.11.0` rationale comment;
- `docs/JOB_PROMOTION_SPEC_2026-09-18.md:148` — D2-A, which also contradicts itself
  ("the eight existing fields … become nullable (as `pressure` already is)");
- `docs/JOB_PROMOTION_SPEC_2026-09-18.md:164` — D3.

(`:89`, "eight properties, all eight required", is correct.)

### 2.4 F4 — the `execution_support.py` docstring overstates the move

The module docstring says the move "is also what lets `tools/execution_promotion.py`
use these". That module imports only `kill_after_timeout` and `new_job_id`; its audit
goes through an `audit_started` callback built in `tools/execution.py`. The two audit
helpers were made public for a cross-module use that does not exist beyond
`tools/execution.py`. The docstring also does not mention `new_job_id`, added to the
module by JPO-006.

### 2.5 F5 — the schema shape was never exercised by the intended client

The `exec` output schema is a top-level object with `properties` *and* a two-branch
`oneOf`. It was validated with the Python MCP SDK and `jsonschema` only. The repository's
intended client is ChatGPT; whether its connector accepts and correctly consumes that
schema (both shapes) has not been observed.

### 2.6 F6 — nit

`test_jpo008_run_exec_refuses_the_flag_when_it_has_nothing_to_promote_with`
(`tests/workspace/test_jpo_exec_promotion.py:363`) binds an unused `_server_` (line 374).

### 2.7 Non-findings (checked, no action)

- The JPOV-006 idle-stop assertion (`stopped == []`) is not vacuous: a probe with the
  promoted job cancelled first saw the same sweep stop the workspace.
- No container leaked from the JPO test files.
- JPO's I4 "no `await` between check and adoption" holds.

## 3. Decisions

Each is recorded in the TODO — option, who chose it, why — before the dependent task.

### PD1 — how F1 is fixed

- **A. Both: terminalise first, and make `attach` failure-atomic (recommended).**
  `_fail_half_created_job` calls `mark_finished` before, and independently of, any
  capture I/O (separate suppress scopes). `attach` commits `_path` only after the head is
  written and flushed; on failure it closes and discards the partial file object and
  stays deferred. The record no longer depends on the disk, and the capture is never
  left half-attached.
- **B. Reorder only.** Fixes the record; leaves `attach` able to strand a capture in a
  state no other code expects.

### PD2 — how F2 is fixed

- **A. Keep the switch synchronous; correct the record and document it (recommended).**
  Moving the I/O off the loop introduces an `await` between the completion check and
  adoption, which reopens the window JPO's I4 comment closes. The cost is bounded
  (≤ 2 × `max_output_bytes`, once per promotion).
- **B. Move the flush to a thread.** Pre-create the directory and open both files in a
  worker, then switch under the lock. Needs a new argument for I4 (JPO-006's corrected
  wording already tolerates a job that finishes immediately), and more code, for a
  one-off stall of at most tens of milliseconds.

### PD3 — how F4 is fixed

- **A. Correct the docstring (recommended).** Private names imported across modules would
  be worse than public ones used from one place.
- **B. Also move the audit helpers back into `tools/execution.py`.** Undoes part of a
  verified pure move and costs line budget, for naming alone.

## 4. Governing invariants

- **G1.** After any failure inside promotion, the job record — if one was created — is
  terminal whenever the job store itself is still writable, regardless of what the
  filesystem is doing.
- **G2.** A `StreamCapture` is always either deferred (no path) or fully attached (path
  set, head written). There is no third state.
- **G3.** Every ticked box in the JPO record describes something true of the code.
- **G4.** No tool contract change: `schemas/tools.json` byte-identical to a fresh export,
  `CONTRACT_VERSION` stays `0.11.1`.
- **G5.** No weakened test or guard; every behavioural claim gets a test proven to
  discriminate.

## 5. Required remediation

1. **PJPO-001** — F1 per PD1: reorder the cleanup; make `attach` failure-atomic; replace
   the monkeypatched JPOV-007b failure with a real I/O failure.
2. **PJPO-002** — F2 per PD2: a narrative comment at the `sink.promote` call site;
   correct JPO-008's box and evidence visibly (struck-through claim plus correction,
   not silently rewritten).
3. **PJPO-003** — F3: correct the count in the three places. Comment-only in `src/`.
4. **PJPO-004** — F4 per PD3.
5. **PJPO-005** — F6.
6. **PJPO-006** — F5: a ChatGPT smoke check, performed by the user, recorded as evidence;
   left unticked if not performed.
7. **PJPO-007** — correct the JPO record's claims that F1 undermines (the I5 row of the
   JPO-008 table, JPO-010's closure), then closure: gates and exact-head CI.

## 6. Validation strategy

- **PJPOV-001** — unit, real I/O: a deferred capture attached to `/dev/full` raises, and
  afterwards is still deferred (`_path is None`, head intact, no open file); `finish()`
  does not raise. Proven by restoring the pre-fix `attach`.
- **PJPOV-002** — unit: `_fail_half_created_job` reaches `mark_finished` when capture
  I/O fails (the reproduction in §2.1, as a test). Proven against the pre-fix ordering.
- **PJPOV-003** — tool level, real Docker: promotion with a log path whose write fails
  for real → `COMMAND_TIMEOUT`, process gone, the job record `failed`. Proven against
  the pre-fix code. (A `state_dir` whose `jobs/` is not writable, or a job-dir path
  redirected to `/dev/full`, per what the implementer finds workable; record which.)
- **PJPOV-004** — the full suite and gates on an idle host; exact-head CI green; a fresh
  export leaves `schemas/tools.json` unchanged.

## 7. Contract-version decision

None required. No change touches a registered tool's name, description or schemas.
PJPO-003 edits a *comment* above `CONTRACT_VERSION`, not the value. G4 is verified, not
assumed, by re-exporting.

## 8. Non-goals

- Request cancellation during the synchronous wait (JPO spec §7, unchanged).
- Changing promotion's success-path behaviour.
- The pre-existing unsynchronised `finish()`-versus-late-write window in
  `AsyncJobRunner._run`'s timeout path, shared with every `exec_start` job.
- The trust model (own repos only).

## 9. Definition of done

PJPO-001–007 closed with evidence; PD1–PD3 recorded; G1–G5 hold; the reproduction in
§2.1 now reaches `mark_finished`; the JPO record corrected visibly; full gates green on
an idle host and on exact-head CI; F5 either observed or explicitly recorded as not
performed.
