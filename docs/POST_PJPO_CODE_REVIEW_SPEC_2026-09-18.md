# Post-PJPO Code Review — Specification

**Status:** Implemented except F4 (ChatGPT check, user-performed, not yet done) — see the TODO
**Date:** 2026-09-18
**Reviewed baseline:** `c6bb465` (`master`; CI run `35402397991` green)
**Source review:** `/code-review-todo docs/POST_JPO_CODE_REVIEW_TODO_2026-09-18.md`, 2026-09-18
**Task series:** `PJPO2-nnn` (`docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md`)
**Validation series:** `PJPO2V-nnn`

---

## 1. Purpose

A review of the merged PJPO series (`83b8afb`–`c6bb465`) confirmed that the defect it
targeted is fixed: a real disk-full failure during promotion now leaves the job record
`failed`, proven end to end against the pre-fix code. It found no bug with a
user-visible consequence. It found residue:

- one docstring that states an invariant the code only holds on success;
- one evidence line that credits a test with cases that test does not exercise;
- two undocumented properties of the new tests.

This spec removes that residue. It also carries forward the one open verification item
from PJPO, which only the user can perform. It changes no tool contract.

## 2. Review baseline and evidence

Checked by command or probe at `c6bb465`.

### 2.1 F1 — `PromotableSink.promote` is not atomic across the two streams

`execution/promotable_sink.py`, `promote` (line 60):

```python
with self._lock:
    self.stdout_capture.attach(stdout_log_path)
    self.stderr_capture.attach(stderr_log_path)
```

Its docstring says: "there is no moment at which one stream is a job log and the other
still a buffer". PJPO-001 made each `attach` all-or-nothing, but not the pair. Probe
(stdout to a real file, stderr to `/dev/full`):

```
promote raised: No space left on device
stdout attached: True | stderr attached: False
stdout.log on disk: b'out\n'
second promote: RuntimeError capture is already attached to …/job/stdout.log
```

So after a stderr failure the sink is, permanently, exactly the state the docstring says
cannot exist, and `promote` cannot be retried.

**Consequence today: small.** The only caller is `run_promotable_exec`, whose
fail-closed path then kills the command and marks the record `failed`. `stdout.log`
holds real data; the buffered stderr never reaches a log, for a job that failed anyway;
`exec_output` on a missing stderr log reads as empty. No orphan, no stuck record. The
defect is a false statement of an invariant, plus a latent trap for any future caller
that handles the error differently.

### 2.2 F2 — PJPO-007's evidence credits PJPOV-001 with cases it does not test

`docs/POST_JPO_CODE_REVIEW_TODO_2026-09-18.md:234-236` says G2 holds "including when
`mkdir`/`open` fail … and when the head is empty … (PJPOV-001)". PJPOV-001's two tests
exercise only a failing `write`. The `mkdir` case was confirmed during the review by a
probe (`NotADirectoryError`, capture still deferred, head intact), and the empty-head
case is exercised incidentally by JPO tests with a silent stderr — but no test pins
either as a property of `attach`, and the citation is wrong.

### 2.3 F3 — undocumented properties of the PJPO tests

- `tests/execution/test_pjpo_fail_closed_promotion.py` and the rewritten
  `test_jpov007_a_log_file_failure_…` depend on `/dev/full`, which exists on Linux only.
  That matches the project (Docker-backed suite, self-hosted Linux runner, skips
  forbidden), but nothing says so; on another OS they would fail confusingly.
- `test_pjpov001_a_failed_attach_leaves_the_capture_deferred_and_intact` asserts on the
  private attributes `_path` and `_file` (lines 36–37). The same test already asserts
  the behaviour those attributes imply (a later `write` and `finish()` do not raise, the
  head is intact), and its sibling proves a later `attach` works.

### 2.4 F4 — carried forward: the schema is unverified with ChatGPT

PJPO-006 was recorded "not performed": whether ChatGPT's connector accepts and correctly
reads `exec`'s output schema (an object with `properties` and a two-branch `oneOf`) has
still not been observed. It needs the user.

### 2.5 Non-findings

- `attach` is all-or-nothing on `mkdir` failure as well as `write` failure (probe).
- PJPO-001's rejection of the "unwritable `jobs/` directory" test design holds: the
  pre-fix code did survive a `mkdir` failure (`_file` stayed `None`, so the old
  `finish()` had nothing to close and `mark_finished` ran).
- Record corrections in the JPO documents are visible, with originals kept.
- No contract drift: fresh export unchanged, `contract_version` `0.11.1`.

## 3. Decisions

Recorded in the TODO — option, who chose it, why — before the dependent task.

### QD1 — how F1 is fixed

- **A. Make the pair atomic (recommended).** Give `StreamCapture` a two-step attach:
  *prepare* (mkdir, open, write and flush the head into a file object that is not yet
  adopted) and *commit* (adopt the prepared file, set the path, release the head — no
  I/O, cannot fail). `promote` prepares both, and only then commits both; if either
  prepare fails, the prepared file objects are closed and both captures stay deferred.
  The docstring becomes true without qualification, and `promote` becomes retryable.
  `attach` stays as prepare-then-commit for one capture.
- **B. Scope the docstring.** Say the guarantee holds on success, and state what a
  failure leaves behind. No code change. Cheaper; leaves the trap in place, documented.

Under A, a file already written for a stream whose sibling failed is left on disk, as it
is today; the job directory belongs to a `failed` record and retention removes it. State
that in the docstring rather than adding unlink logic.

### QD2 — how F3's private-attribute assertions are handled

- **A. Replace with behavioural assertions where they add nothing (recommended)**, and
  keep at most one white-box assertion if a behavioural equivalent does not exist (e.g.
  "no file object is left open"), with a comment saying why.
- **B. Leave as is**, with a comment acknowledging the coupling.

## 4. Governing invariants

- **H1.** After `PromotableSink.promote` returns *or raises*, the two captures are in the
  same mode: both attached, or both deferred with their heads intact. (QD1-A; under
  QD1-B this is replaced by an accurate description of the failure state.)
- **H2.** Every property claimed for `attach`/`promote` in a docstring or evidence line is
  pinned by a named test.
- **H3.** PJPO's outcomes are preserved: G1 (record terminal first) and G2 (no
  half-attached capture) still hold, and their tests still pass unchanged or stronger.
- **H4.** No tool contract change; `CONTRACT_VERSION` stays `0.11.1`, verified by export.
- **H5.** Records are corrected visibly, originals kept.

## 5. Required remediation

1. **PJPO2-001** — F1 per QD1.
2. **PJPO2-002** — F2: tests for `attach` on `mkdir` failure, on `open` failure, and with
   an empty head; correct PJPO-007's evidence line visibly.
3. **PJPO2-003** — F3: document the Linux-only dependency; private-attribute assertions
   per QD2.
4. **PJPO2-004** — F4: the ChatGPT smoke check, user-performed; unticked if not done.
5. **PJPO2-005** — closure: self-review, gates on an idle host, exact-head CI.

## 6. Validation strategy

- **PJPO2V-001** (QD1-A) — stdout attaches to a real file, stderr to `/dev/full`:
  `promote` raises; both captures still deferred with heads intact; a second `promote`
  to good paths succeeds and both logs hold their full output. Proven to fail against
  the pre-fix `promote`. Plus the mirror case (stdout fails, stderr would succeed).
- **PJPO2V-002** — `attach` with a parent path that is a regular file (`mkdir` fails),
  with a path that is a directory (`open` fails), and with an empty head (no file
  created until the first write; path committed). The first two proven against the
  pre-PJPO-001 `attach` (`git show 08073af:…`), which assigned `_path` first.
- **PJPO2V-003** — full suite and gates on an idle host, foreground; exact-head CI; fresh
  export unchanged.

## 7. Contract-version decision

None. Nothing registered changes. H4 is verified by re-exporting, not assumed.

## 8. Non-goals

- Unlinking a log file written for one stream when its sibling fails.
- Moving promotion's file I/O off the event loop (PJPO PD2-A stands).
- Request cancellation during the synchronous wait (JPO spec §7).
- Portability of the suite beyond Linux.
- The trust model (own repos only).

## 9. Definition of done

PJPO2-001–003 and PJPO2-005 closed with evidence; QD1–QD2 recorded; H1–H5 hold; the
PJPO-007 evidence line corrected; gates green on an idle host and on exact-head CI;
PJPO2-004 performed, or recorded as not performed.
