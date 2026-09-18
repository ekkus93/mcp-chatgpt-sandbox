# Job Promotion on Overrun — Specification

**Status:** Implemented — see `docs/JOB_PROMOTION_TODO_2026-09-18.md` for evidence and recorded deviations
**Date:** 2026-09-18
**Baseline:** `13e44bd` (`master`, CI run `35360824436` green: 1455 passed, coverage 88%, `contract_version` `0.10.3`)
**Source requirement:** `docs/SPEC.md` §14.4; open checkbox `docs/TODO.md:353`
**Task series:** `JPO-nnn` (`docs/JOB_PROMOTION_TODO_2026-09-18.md`)
**Validation series:** `JPOV-nnn`

---

## 1. Purpose

`docs/SPEC.md` §14.4 permits — it does not require — the server to convert a
synchronous `exec` that outlives the synchronous threshold into a background
job instead of killing it:

> If the command exceeds the synchronous threshold, the server MAY convert it to
> a background job. `exec` therefore has two response shapes — a completed
> result, or a job handle identical to `exec_start`'s — and its `outputSchema`
> SHALL express the union explicitly, with a `promoted_to_job: true`
> discriminator on the second.

It was deferred in S11 because the job store did not exist. The job store,
runner, log capture, retention and reconciliation have all since landed
(S14–S16); `execution/service.py:24-30` records that promotion is "unblocked and
simply not built". It is the one open *feature* checkbox left in `docs/`.

The user-facing cost of not having it: a client that runs `pytest`, `npm ci` or
a build through `exec` loses the whole run at 30 s
(`execution.sync_timeout_seconds`) with `COMMAND_TIMEOUT`, and has to guess that
it should have used `exec_start` and start again from zero.

This spec defines promotion so that the work already done is kept.

## 2. Baseline and evidence

Every statement below was checked by command against `13e44bd`.

### 2.1 The sync path today

`tools/execution.py:338` `run_exec`:

1. authorises, requires `RUNNING`, resolves cmd/cwd/user, clamps the timeout to
   `sync_timeout_seconds`;
2. `service.start(...)` — `exec_create` only (`execution/service.py:136`);
3. `asyncio.wait_for(asyncio.to_thread(service.consume, exec_id, ...), timeout)`;
4. on `TimeoutError` (`tools/execution.py:414`): `kill_after_timeout(...)`
   (`:82`) then `CommandTimeoutError`.

`ExecutionService.consume` (`execution/service.py:159`) **itself** calls
`exec_start(stream=True, demux=True)` and buffers the output in memory inside
the worker thread. Nothing outside that thread holds the stream. There is
therefore nothing to "hand over" at the moment of overrun: the output produced
so far lives in a local variable of a blocked thread.

### 2.2 The job path today

`tools/execution.py:447` `run_exec_start` creates `state_dir/jobs/<job_id>/`,
`job_store.create(...)`, then `AsyncJobRunner.start_job`
(`execution/async_runner.py:116`), which calls
`service.start_and_open_stream` (`service.py:250`, returns
`(exec_id, stream, pid)`), builds a `LiveJob` (`async_runner.py:60`) with two
`StreamCapture`s, registers it in `self._live` (`:179`), and drains through
`service.drain_stream(exec_id, stream, on_stdout=, on_stderr=)`
(`service.py:273`) in `_run` (`:185`), which owns timeout, cancel, termination
failure and `mark_finished`.

**The seam this spec builds on:** `drain_stream` delivers output through
*callbacks*. The stream never has to change hands; only the destination the
callbacks write to has to change.

### 2.3 Things promotion gets for free once a real job record exists

- `exec_status` / `exec_output` / `exec_cancel` operate on `job_id` and the
  runner's `_live` map — no change needed provided the promoted job is
  registered as a `LiveJob`.
- Idle-stop skips a workspace with a non-terminal job
  (`workspace/manager_lifecycle.py:421-427`).
- An unclean shutdown marks it `lost` (`jobs/reconcile.py`).
- Retention prunes its logs (`jobs/retention.py`; `run_exec_start` also prunes
  inline at `tools/execution.py:529`).

### 2.4 Contract facts

- Every tool in `schemas/tools.json` has an output schema with top-level
  `"type": "object"` (checked: the set of top-level types is `{"object"}`). The
  MCP `Tool.outputSchema` type requires it.
- `exec`'s output schema today: eight properties, **all eight `required`**,
  `pressure` already nullable.
- `promoted_to_job` appears nowhere in `src/`, `tests/`, `schemas/` or
  `docs/TOOLS.md`.
- `docs/TOOLS.md` and its drift checks cover tool names and descriptions only;
  no tooling inspects the *shape* of an output schema
  (`tests/test_schema_export.py`, `tests/test_docs_tools.py`).
- `docs/SPEC.md` §33.2: patch = new optional input field, new output field,
  loosened constraint, description change; minor = new tool; major = removed /
  renamed field, tightened constraint, changed meaning.

### 2.5 Probe results (`mcp` 2.x `MCPServer`, run 2026-09-18)

| Return annotation | Exported schema | Structured content | Verdict |
|---|---|---|---|
| `-> ExecOutput \| ExecStartOutput` | `{"properties": {"result": {"anyOf": […]}}}` | wrapped in `{"result": …}` | **Rejected.** Moves `exit_code` to `result.exit_code` for every existing client. |
| pydantic `RootModel` discriminated on `promoted_to_job` | top-level `oneOf` + `discriminator`, **no `type: object`** | flat | **Rejected.** Violates §2.4; a strict MCP client may refuse the tool. |
| one flat pydantic model, `json_schema_extra={"oneOf": [two branches]}` | `type: object`, `properties`, plus `oneOf` | flat; unused fields emitted as `null` | **Viable.** |

Two further facts from the third probe, both of which shape §5:

- the SDK does **not** validate a returned value against the `oneOf` branches —
  a value with `promoted_to_job: true` and no `job_id` was returned without
  error. The invariant has to be enforced by the model itself;
- unused fields are serialised as `null`, so a branch that says
  `required: ["job_id"]` is satisfied by `job_id: null` unless the branch also
  pins the type.

### 2.6 Structural constraint

`tools/execution.py` is 797 lines and is capped at `< 800` by
`tests/test_refactored_module_size_guard.py:6`. The feature cannot be added to
that file as-is.

## 3. Design decisions

Each is a recorded decision: the option chosen, by whom, and why, is written
into the TODO before the dependent task starts.

### D1 — When does promotion happen?

- **A. Per-call opt-in (recommended).** New optional `exec` input
  `promote_on_timeout: bool = false`. Absent or false → today's behaviour,
  byte for byte, through today's code path. A client only ever sees the second
  response shape if it asked for it.
- **B. Server config opt-in.** `execution.promote_sync_overrun: bool = false`.
  One switch for the operator, but the *client* is then handed a shape it never
  asked for — whether it copes is outside the server's knowledge.
- **C. Always.** Simplest surface, and a changed meaning of `exec` for every
  existing caller: a major bump, and `COMMAND_TIMEOUT` from `exec` effectively
  disappears.

Why A: the hazard in this feature is a client that reads `exit_code` from a
response that does not have one. Under A that can only happen to a client that
set the flag.

### D2 — Output schema shape

- **A. One flat object with a `oneOf` (recommended)** — §2.5 row 3.
  `promoted_to_job: bool` is added and required. The eight existing fields stay
  in `required` and become nullable (as `pressure` already is); `job_id`,
  `state`, `started_at` are added, required and nullable. The `oneOf` has two
  branches keyed on `promoted_to_job` `const false` / `const true`, each pinning
  the non-null types of its own fields and `null` for the other branch's.
- **B. Flat nullable object, no `oneOf`.** Simpler; does not meet §14.4's
  "SHALL express the union explicitly".
- **C. Discriminated `RootModel`.** Rejected in §2.5.

Why "required and nullable" rather than "optional": removing a name from
`required` is a removal from the consumer's point of view; widening its type to
allow `null` on a branch only opted-in callers reach is not.

### D3 — `contract_version`

Under D1-A + D2-A the change is: one new optional input field (patch), four new
output fields (patch), and a type widening of eight output fields that is
unobservable without opting in. §33.2 has no row for the last. **Recommended:
`0.11.0`** — a second response shape is a larger event than a patch even though
no tool was added; it is not major because no existing caller's observable
contract changes. Record the reasoning next to the bump.

Under D1-B or D1-C it is a changed meaning: `1.0.0`-class, to be decided
explicitly, not defaulted.

### D4 — The promoted job's deadline

- **A. `default_job_timeout_seconds`, measured from the command's original
  start (recommended).** The job is the same process; its clock started when the
  process did. `started_at` in the response is the original start time.
- **B. A fresh full job timeout starting at promotion.** Lets a command run
  `sync + job` seconds in total, a limit that exists nowhere in config.

With promotion on, `timeout_seconds` keeps its clamp to `sync_timeout_seconds`
and means "how long to wait synchronously before promoting".

## 4. Governing invariants

- **I1 — Default path untouched.** With `promote_on_timeout` false or absent,
  `run_exec` executes the same `start` + `consume` + kill-on-timeout path as at
  `13e44bd`, and returns the same fields with `promoted_to_job: false`.
- **I2 — One process, never restarted.** Promotion adopts the running exec. The
  command is never re-run; the `exec_id` and pid before and after are the same.
- **I3 — No lost output.** Every byte produced before promotion that the sync
  buffer retained is in the job's log, in order, before any byte produced after.
  `stdout_total_bytes` / `stderr_total_bytes` count every byte produced since
  the process started, including any dropped by a cap; any gap is marked the way
  `StreamCapture` already marks one.
- **I4 — Exactly one outcome.** A call returns a completed result, a job
  handle, or an error. A command that finishes as the deadline fires yields a
  completed result and creates **no** job record. No job record is created for a
  command *observed* finished at the deadline. (Corrected by JPO-006: the first
  wording, "never for a process that has already exited", promised an atomicity
  between the process and the daemon that does not exist; a process exiting just
  after the check becomes a job that finishes immediately and correctly.)
- **I5 — Fail closed.** If promotion cannot be completed (job directory, job
  store, registration), the process is killed exactly as on a timeout today and
  the call raises `COMMAND_TIMEOUT`; no half-created job record is left
  non-terminal and no process is left running unowned.
- **I6 — A promoted job is an ordinary job.** `exec_status`, `exec_output`
  (from offset 0, including pre-promotion output), `exec_cancel`, the deadline,
  idle-stop protection, restart reconciliation and retention behave exactly as
  for an `exec_start` job. Its `JobRecord.command` and `cwd` match what
  `exec_start` would have recorded.
- **I7 — Bounded memory.** The pre-promotion buffer is bounded per stream by the
  same limit the sync path uses today. Promotion does not add an unbounded
  buffer, and the buffer is released once flushed.
- **I8 — Thread safety.** The sink is written from the drain thread and switched
  from the event loop. The switch and every write are serialised by one lock; a
  chunk is delivered to exactly one destination.
- **I9 — Schema truthfulness.** The output model rejects, at construction, any
  value that does not match exactly one branch (§2.5: the SDK will not).
- **I10 — Secrets.** The promotion audit record and any error `details` follow
  the existing redaction rules; `env` values never appear.

## 5. Required work

### 5.1 Switchable sink (`execution/promotable_sink.py`, new)

A small class with `on_stdout(chunk)` / `on_stderr(chunk)` suitable for
`drain_stream`, in one of two modes:

- **buffering** — bounded in-memory capture equivalent to what `consume`
  retains, counting total bytes;
- **promoted** — after `promote(stdout_capture, stderr_capture)`: under the
  lock, flush each buffer into its `StreamCapture`, carry over the byte counts
  (I3 — extend `StreamCapture` minimally if it needs a way to account for bytes
  it never saw), release the buffers, and route every later chunk straight to
  the captures.

Plus `result()` producing the same `ExecResult` tails `consume` produces, for
the completed-in-time case. Why not write to log files from the start for every
opted-in call: it would create a job directory and two files for every quick
command, and give retention something to prune for calls that were never jobs.

### 5.2 Runner adoption (`execution/async_runner.py`)

`AsyncJobRunner.adopt_running(job_id, *, exec_id, stream, drain_future,
stdout_capture, stderr_capture, deadline, …)`: register a `LiveJob`, and finish
the job through the **same** finalisation `_run` uses (timeout → `TIMED_OUT`,
cancel, termination failure, `mark_finished`, `_live.pop`). Factor the shared
tail out of `_run`; do not duplicate it. Why not a second drain: the stream is
single-consumer and already being read.

### 5.3 Promotion coordinator (`tools/execution_promotion.py`, new)

Holds the opted-in path so `tools/execution.py` stays under budget (§2.6):

1. `start_and_open_stream`; start the drain in a thread with the sink;
2. wait for the drain up to the sync timeout;
3. finished → build the completed `ExecOutput` from `sink.result()` (I4);
4. not finished → create job dir and captures, `job_store.create` /
   `mark_starting` / `mark_running(pid=)`, `sink.promote(...)`,
   `runner.adopt_running(...)`, audit, inline `prune_job_logs`, return the job
   handle;
5. any failure in step 4 → I5.

The finished-vs-deadline race (I4) is resolved by checking the drain future
*after* the wait returns and before any job state is created.

### 5.4 Contract

`ExecOutput` per D2; input `promote_on_timeout` per D1; `exec`'s description
states both shapes and how to follow a promoted job. Then the `tool-contract`
steps: re-export `schemas/tools.json`, bump `CONTRACT_VERSION`
(`schema_export.py:76`) per D3, update `docs/TOOLS.md`.

### 5.5 Documentation

`docs/SPEC.md` §14.4 gains the input flag and the deadline rule (D4);
`execution/service.py:24-30` is rewritten to describe what now exists;
`docs/TODO.md:353` is ticked **only at closure** (VR-000b: a tick describes
work done).

## 6. Validation strategy

Docker-backed, against the real test image, no mocks of the exec stream for the
behavioural tests. Each regression test is proven to discriminate by running it
against the pre-change code (or with the behaviour it guards disabled) and
recording the failure.

- **JPOV-001** probe record: D2 schema exported by the real server has
  `type: object`, the `oneOf`, flat structured content.
- **JPOV-002** sink unit tests: ordering across the switch, counts, bound,
  concurrent writer vs `promote` (I3, I7, I8).
- **JPOV-003** default path: flag absent/false + overrun → `COMMAND_TIMEOUT`,
  process killed, no job record, no job directory (I1).
- **JPOV-004** in-time with flag: completed result, `promoted_to_job: false`, no
  job record (I4).
- **JPOV-005** promotion: a command that prints a marker, sleeps past the sync
  timeout, prints a second marker and exits 0 → handle returned; `exec_status`
  reaches `succeeded` with exit code 0; `exec_output` from offset 0 contains both
  markers in order; pid unchanged (I2, I3, I6).
- **JPOV-006** promoted job: `exec_cancel` works; deadline from original start
  → `timed_out` (D4); idle-stop sweep skips the workspace (I6).
- **JPOV-007** fail closed: job-store / job-dir failure injected at promotion →
  `COMMAND_TIMEOUT`, process gone, no non-terminal record (I5).
- **JPOV-008** model rejects mixed/empty branch values (I9).
- **JPOV-009** audit record links exec to `job_id`; a secret in `argv`/`env` is
  not present in it (I10).
- **JPOV-010** full gates on an idle host; exact-head CI green; schema
  byte-identical to export; version monotonic.

## 7. Non-goals

- Changing `exec_start`, the job state machine, retention or reconciliation.
- Promotion for anything other than `exec` (Git tools, setup, etc.).
- Making promotion the default (D1-C) — a separate, later decision with its own
  version bump.
- Handling MCP request cancellation / client disconnect during the sync wait
  differently from today.
- Streaming partial output in the promoted response. The handle is "identical to
  `exec_start`'s" (§14.4); output is read with `exec_output`.
- The untrusted-code angle (trust model: own repos only).

## 8. Definition of done

All `JPO` tasks ticked with evidence; D1–D4 recorded; invariants I1–I10 each
covered by a named test proven to discriminate; `tools/execution.py` < 800
lines; schema, version and `docs/TOOLS.md` consistent; `docs/TODO.md:353`
ticked; full gates green locally on an idle host and on exact-head CI.
