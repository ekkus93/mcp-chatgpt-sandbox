# Post-PJPO2 Code Review — Specification

**Status:** Draft — not started
**Date:** 2026-09-18
**Reviewed baseline:** `d6d0105` (`master`; CI run `35407788748` green)
**Source review:** `/code-review-todo docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md`, 2026-09-18
**Task series:** `PJPO3-nnn` (`docs/POST_PJPO2_CODE_REVIEW_TODO_2026-09-18.md`)
**Validation series:** `PJPO3V-nnn`

---

## 1. Purpose

A review of the merged PJPO2 series (`8cf3661`–`d6d0105`) found it sound: no bug, and
`PromotableSink.promote` now switches both streams or neither, proven against the
pre-fix code. It found three small things, none reachable from any production caller:

- a new public method with an ordering contract that nothing enforces;
- a defensive line of code that a test appears to pin but does not;
- a true statement in the closure evidence that no test backs.

This spec closes them. It also carries forward the one verification only the user can
perform. It changes no tool contract.

## 2. Review baseline and evidence

Checked by command or probe at `d6d0105`.

### 2.1 F1 — `commit_attach` accepts a second commit

`execution/log_capture.py`: `prepare_attach` (line 108) refuses a capture that is
already attached; `commit_attach` (line 138) does not check anything. Probe:

```python
f = c.prepare_attach(m1)
c.commit_attach(m1, f)
c.commit_attach(m2, None)  # accepted silently
c.write(b"y")
c.finish()
# m1: b'x' | m2: b'y' | first file closed: False
```

The capture silently moved to a second path and the first file object was never closed.
No production code does this — `attach` and `promote` are the only callers and both
pair the calls correctly — but the two methods are public, and the contract between
them ("commit exactly what prepare returned, once, with no write in between") lives
only in a docstring. One existing test depends on `prepare_attach` by name:
`test_jpov002_a_chunk_arriving_during_promotion_waits_for_it` monkeypatches it
(`tests/execution/test_jpo_promotable_sink.py:116,129`).

### 2.2 F2 — the close of the prepared stdout file is not pinned

`execution/promotable_sink.py:93`: when stderr's prepare fails, `promote` explicitly
closes the stdout file it had already prepared. `test_pjpo2v001_a_stderr_failure_…`
asserts `_open_descriptors_to(first_stdout) == []`, and
`docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md:83` cites that as checking "no descriptor
open on the prepared stdout file". Mutation: with the three-line close removed, both
atomicity tests still pass — CPython's reference counting closes the orphaned file
object as the failed call unwinds, so the descriptor check cannot tell whether the
explicit close ran.

The close is still right: it does not depend on refcounting, on the interpreter, or on
nothing retaining the traceback (a logged exception would keep the frame, and the file,
alive). The defect is that the record implies a test pins it.

### 2.3 F3 — an evidence claim with no test

`docs/POST_PJPO_CODE_REVIEW_TODO_2026-09-18.md:209-210` says a second `promote` after a
successful one "still raises 'already attached' from the first `prepare_attach`, before
any I/O". True — confirmed by probe (`RuntimeError`, the second job directory never
created) — but no test covers it, and the series' own H2 asked that every claimed
property be pinned by a named test.

### 2.4 F4 — carried forward: the schema is unverified with ChatGPT

PJPO-006 and PJPO2-004 are both recorded "not performed". Whether ChatGPT's connector
accepts and correctly reads `exec`'s output schema (an object with `properties` and a
two-branch `oneOf`) has still not been observed. It needs the user.

### 2.5 Non-findings

- H1 holds on failure of either stream; `promote` is retryable with no output lost.
- The retargeted JPO concurrency test still discriminates (lock removed → fails).
- No private-attribute assertions remain in the PJPO test files.
- No contract drift: fresh export unchanged, `contract_version` `0.11.1`.

## 3. Decision

Recorded in the TODO — option, who chose it, why — before PJPO3-001.

**Decided: RD1-A**, by the user, 2026-09-18.

### RD1 — how F1 is fixed

- **A. Guard, keep the methods public (recommended).** `commit_attach` raises
  `RuntimeError` if the capture is already attached, mirroring `prepare_attach`. One
  line; no rename; the existing test that patches `prepare_attach` is untouched.
- **B. Guard and make both methods private** (`_prepare_attach` / `_commit_attach`).
  States the intent — only `attach` and `PromotableSink.promote` may use them — but
  `PromotableSink` is another class in another module, so it would be calling private
  methods across a module boundary, which PJPO's PD3-A already declined to do for the
  audit helpers; and the JPO concurrency test would need renaming again.

The guard does not make a *mismatched* commit impossible (a file prepared for one path
committed under another); that would need a token object, which is more machinery than
two correct callers justify. Say so in the docstring rather than pretend otherwise.

## 4. Governing invariants

- **K1.** A `StreamCapture` is attached at most once: `attach`, `prepare_attach` and
  `commit_attach` all refuse an already-attached capture, before any I/O or state change.
- **K2.** Every defensive line and every evidence claim in scope is pinned by a test that
  fails without it.
- **K3.** PJPO2's outcomes are preserved (H1, retryability); its tests pass unchanged.
- **K4.** No tool contract change; `CONTRACT_VERSION` stays `0.11.1`, verified by export.
- **K5.** Records are corrected visibly, originals kept.

## 5. Required remediation

1. **PJPO3-001** — F1 per RD1.
2. **PJPO3-002** — F2: a test that fails when the explicit close is removed; correct the
   PJPO2-001 evidence line visibly.
3. **PJPO3-003** — F3: a test for re-promoting after success.
4. **PJPO3-004** — F4: the ChatGPT smoke check, user-performed; unticked if not done.
5. **PJPO3-005** — closure: self-review, gates on an idle host, exact-head CI.

## 6. Validation strategy

- **PJPO3V-001** — the §2.1 probe as a test: a second `commit_attach` raises, the capture
  still writes to the first path, nothing is created at the second. Proven against the
  pre-fix `commit_attach`.
- **PJPO3V-002** — capture the file object `prepare_attach` returns for stdout (wrapping
  the real method, not replacing its behaviour), fail stderr's prepare for real
  (`/dev/full`), and assert the captured object's `.closed` is true *while the test
  still holds a reference to it* — which is what defeats refcounting. Proven by removing
  the close. If it cannot be made to discriminate, say so and reword the evidence
  instead of claiming a pin.
- **PJPO3V-003** — after a successful `promote`, a second one raises "already attached",
  creates nothing at the second paths, and later output still goes to the first logs.
  Expected to pass on the current code (it pins a property, not a regression); record
  that rather than contrive a failure.
- **PJPO3V-004** — full suite and gates on an idle host, foreground; exact-head CI; fresh
  export unchanged.

## 7. Contract-version decision

None. Nothing registered changes. K4 is verified by re-exporting.

## 8. Non-goals

- A token/handle type making mismatched prepare/commit pairs impossible.
- Unlinking a prepared log file when its sibling fails (PJPO2 non-goal, unchanged).
- Moving promotion's file I/O off the event loop (PJPO PD2-A stands).
- The trust model (own repos only).

## 9. Definition of done

PJPO3-001–003 and PJPO3-005 closed with evidence; RD1 recorded; K1–K5 hold; the
PJPO2-001 evidence line corrected; gates green on an idle host and on exact-head CI;
PJPO3-004 performed, or recorded as not performed.
