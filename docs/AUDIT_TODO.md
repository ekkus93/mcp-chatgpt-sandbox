# Audit TODO

**Spec:** `docs/AUDIT_SPEC.md` (AR-001..AR-004, AAT-001..AAT-004)
**Source:** code review of `docs/REVIEW_TODO.md` V1–V8, 2026-08-29, against
`d894b4e`
**Status:** not started

Four items: one high-severity correctness defect, one incomplete fix, and
two coverage gaps. Every one was reproduced against the running code before
being written down — each task below carries the reproduction so the
implementer can re-run it rather than trust this file.

**What the audit did *not* find, recorded so the scope is honest.** V1, V6,
V7 and V8 verified correct as written. V2, V3 and V4 verified correct
*beyond* their own tests and needed no change:

- **V2** — re-derived `truncated` against seven untested edge cases (start
  past EOF, `start == EOF`, negative start, inverted range, exactly-at-limit,
  one-past-limit, `end=None` under a small limit). Correct in all seven.
- **V3** — grepped every environment variable `entrypoint.sh` actually reads
  and compared against `_DAEMON_CONTROLLED_ENV_KEYS`: the seven `MCP_*` vars
  match exactly, plus `SSH_AUTH_SOCK`. Complete.
- **V4** — the worry was that `chown -R` would still follow symlinks planted
  elsewhere inside `.ssh`. It does not: GNU `chown -R` defaults to `-P`,
  confirmed empirically to operate on symlinks as symlinks and never descend
  into a symlinked directory. The two guards are sufficient scope.

## How to work this list

The discipline `docs/HARDENING_TODO.md` established and
`docs/REMEDIATION_TODO.md`/`docs/REVIEW_TODO.md` extended, unchanged:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.**
3. Run the new test alone, then the relevant suite, then the full suite.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> >
   <file>`, confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck. Check for Docker leftovers.
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION` per
   §33.2. **No task here is expected to need one** — A1 changes *when*
   `index_left_dirty` is `true`, not the field or its meaning.
7. One task per commit. No `Co-Authored-By` trailers.
8. **Re-run every claim before writing it in the task's write-up** (RR-033).

**Ordering.** A1 first and alone — it is the only real defect, and the only
one that loses data. A2 is a small correctness fix plus three explicit
type decisions. A3 and A4 are test/infrastructure work, independent of both
and of each other.

---

## A1 — A lossy capture never becomes a command argument
**Satisfies:** AR-001 · **Verified by:** AAT-001

- [ ] `CapturedOutput` reports whether its decoding replaced any byte
- [ ] `git_commit`'s rollback skips the restore when the snapshot decoded
      lossily, and reports `index_left_dirty: true`
- [ ] The read tools (`git_status`/`git_diff`/`git_log`) are left unchanged
- [ ] Test a failed commit on a directory pathspec containing a non-UTF-8
      path

**Defect (reproduced end-to-end through the real MCP tool).** A failed
`git_commit` naming the directory `many`, which contains `bad\xffname.txt`
with prior staged content distinct from HEAD:

```
INDEX BEFORE: 2 entries
INDEX AFTER:  3 entries                                   <- phantom entry created
  many/bad<U+FFFD>name.txt  28fef7a = v2-prior-staged     <- restored to the WRONG path
  many/bad\xffname.txt      818f8e5 = v3-this-calls-own-add  <- caller's prior work LOST
error details: {"index_left_dirty": false}                <- claims the restore succeeded
```

`ExecutionService.capture` decodes with `errors="replace"`, so a non-UTF-8
staged path becomes U+FFFD; `build_restore_index_argv` then writes the
caller's prior blob to that corrupted path, leaving this call's own `git add`
content on the real one. `restore_ok` is `True`, so `index_left_dirty` is
reported `false`. This is RR-005's own defect through a second mechanism —
the one V1 did not cover.

**Reachability, stated precisely.** Naming a non-UTF-8 path *directly* is
**not** reachable — the argument is mangled on the way in and `git add` fails
closed with `COMMAND_FAILED` before any rollback runs. The defect needs the
client to name a **directory** pathspec containing such a file, so the client
never names the bad path itself. Do not simplify the test to a direct path:
it will pass for the wrong reason.

**Do not attempt a faithful round-trip.** Decoding with `surrogateescape`
(as `run_git_metadata_read` does host-side) fixes the parse and still
corrupts the restore, because the Docker exec API is JSON and cannot carry a
non-UTF-8 byte. Verified against a real container:

```
real file in container:  badM-^?name.txt          (cat -v; \377 = 0xff)
argv sent as '\udcff':   ls: cannot access '/tmp/bad'$'\357\277\275''name.txt'
                                                  (\357\277\275 = U+FFFD)
```

**Shape of the fix.** H36a's precedent, one property further out: the signal
belongs at the read. Add a lossy-decode flag to `CapturedOutput` beside
`stdout_truncated` (a caller cannot detect this from the decoded string — a
genuine U+FFFD is indistinguishable from a replaced byte), and have
`git_commit`'s rollback treat a lossy snapshot exactly as it already treats a
truncated one: skip the restore, report `index_left_dirty: true`. Failing
closed is correct here by RR-005's own principle — a leaked stage is
recoverable, a destroyed one is not.

**Do not** extend this to `git_status`/`git_diff`/`git_log`. Their
replace-decoding is correct: their output is displayed, never fed back as an
argument, and `sanitize_for_client` already makes exactly this substitution
so the SDK's JSON serialization does not crash. Changing them would be an
over-correction, and AR-001's own scope note says so.

## A2 — Redaction's content scan covers every type it redacts by key
**Satisfies:** AR-002 · **Verified by:** AAT-002

- [ ] Run the value-pattern scan against `bytes`, or narrow V5's stated scope
      to key-flagged only
- [ ] Decide `bytearray` explicitly — handle it or record the exclusion
      in code, as `frozenset` already is
- [ ] Decide `memoryview` and namedtuple-type-preservation the same way
- [ ] Test whichever contract is chosen

**Defect (reproduced):**

```
{"blob": "ghp_abcdefghijklmnopqrstuvwx"}   ->  {"blob": "[REDACTED]"}          str: caught
{"blob": b"ghp_abcdefghijklmnopqrstuvwx"}  ->  {"blob": b"ghp_abcdefghij..."}  bytes: LEAKS
```

`_redact` redacts a `str` on a secret-hinted key **or** secret-shaped content
(`_looks_like_secret`); V5 extended redaction to `bytes` on the key hint
only. V5 met VR-005's literal wording while reproducing VR-000a's own
anti-pattern — a control applied to one half and not its neighbour.

**Three neighbouring types, current behavior verified:**

```
{"secret": bytearray(b"ghp_abc")}   ->  unchanged         (leaks)
{"secret": memoryview(b"ghp_abc")}  ->  unchanged         (leaks)
{"secret": frozenset({"hunter2"})}  ->  unchanged         (leaks — deliberate, documented)
{"secret": NamedTuple("hunter2",1)} ->  ('[REDACTED]', 1) (redacted; type degraded to tuple)
```

`frozenset`'s exclusion is already argued in V5's own code comment and may
stand. `bytearray` is `bytes`'s mutable twin — exactly what V5's rationale
covers — and was neither handled nor documented; that asymmetry is the part
worth fixing regardless of which way the others go.

**Narrowing is an acceptable outcome** for any of these, as it was for V5:
what is not acceptable is a claim broader than the behavior. If the scan is
narrowed instead of extended, amend `docs/REVIEW_SPEC.md` VR-005 to match.

## A3 — The rollback call site is covered by a parser-tripping path
**Satisfies:** AR-003 · **Verified by:** AAT-003

- [ ] Exercise `git_commit`'s post-`add` re-capture with a path that
      stresses the parser, not an ordinary ASCII one

**Gap.** V1 converted two call sites to `capture_index`, but VAT-001 commits
*successfully* and so reaches only the first. The failure-branch re-capture
is covered today solely by
`test_rr005_a_failed_commit_restores_prior_staged_content_not_head`, whose
path trips neither the line crop nor the decode.

Likely folds into A1's own test, since AAT-001 already requires a *failed*
commit — check whether one test satisfies both before writing a second.
Doing so is fine and preferable; leaving the site untested is not.

## A4 — Every acceptance-test series is citation-audited
**Satisfies:** AR-004 · **Verified by:** AAT-004

- [ ] Extend `tests/test_acceptance_test_coverage.py` to `HAT`/`RAT`/`VAT`,
      or record the omission in that module as deliberate
- [ ] Extend the existing meta-test (citation-scrubbing) to whatever is added

**Gap.** The audit reads `docs/SPEC.md` and matches `### AT-(\d{3})` only.
All six `VAT`s are cited today — verified — but nothing would catch a
deletion, and the same holds for `HAT` and `RAT`.

Pre-existing and systemic, not introduced by V1–V8. Lowest priority here;
listed because the audit surfaced it and no other document owns it. If the
`HAT`/`RAT` series turn out to have uncited entries from earlier phases,
**record them rather than back-filling tests under this task** — that is a
separate body of work and folding it in would make this task unbounded.

---

## Definition of done

AR-001 is closed and covered by AAT-001; AR-002's chosen contract is
implemented and matches what `docs/REVIEW_SPEC.md` claims; the rollback call
site is exercised; the coverage audit either covers every series or says why
not. The full suite passes with no Docker-dependent test skipped, a suite run
leaves foreign Docker state untouched, and no write-up here carries a claim
that was not re-run against the code before being written (RR-033).

**This is the last review layer.** `docs/REVIEW_TODO.md`'s own closing note
argued that a fifth layer costs more than it catches; this document exists
anyway because AR-001 is a client-reachable data-loss defect needing a
reproduction, a fix design and an acceptance test, none of which fit in a
correction paragraph — see `docs/AUDIT_SPEC.md` §2 for that decision in full.
That justification does not generalize. A finding about **this** document
belongs in a correction paragraph here; the next defect of AR-001's severity
belongs in `docs/SPEC.md` and the main `docs/TODO.md`, where ordinary work is
tracked, not in a sixth file.
