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

**Ordering.** A1 and A3 first, together — they are one fix. A1 guards two
capture sites; A3 is the only test that constrains the second of them, and
neither test can substitute for the other (see A1's own note). Landing A1
without A3 ships half a fix with a green suite. A2 is a small correctness fix
plus three explicit type decisions, independent. A4 is test infrastructure,
independent of everything and lowest priority.

---

## A1 — A lossy capture never becomes a command argument ✅
**Satisfies:** AR-001 · **Verified by:** AAT-001 (site 1) + AAT-003 (site 2)
**Commit:** `PLACEHOLDER`

- [x] `CapturedOutput` reports whether its decoding replaced any byte
- [x] **Site 1** (`tools/git.py:495`, pre-`add`) treats a lossy decode the
      way it already treats `stdout_truncated` — `prior_entries` becomes
      `None`, so no restore is attempted and `index_left_dirty` stays `true`
- [x] **Site 2** (`tools/git.py:529`, post-`add` re-capture) does the same,
      for its own independent write-back — see the table below
- [x] The read tools (`git_status`/`git_diff`/`git_log`) are left unchanged
- [x] Test **both** sites — they need two different fixtures (A3 is site 2's;
      it is not optional)

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
genuine U+FFFD is indistinguishable from a replaced byte), and have both
capture sites treat a lossy snapshot exactly as they already treat a
truncated one: skip, report `index_left_dirty: true`. Failing closed is
correct here by RR-005's own principle — a leaked stage is recoverable, a
destroyed one is not.

**Two sites, two different failures — guard both.** `git_commit` captures the
index twice and *each* capture feeds a different write-back:

| Site | Capture | Feeds | Failure when lossy |
|---|---|---|---|
| 1 (`:495`) | pre-`add` | `prior_entries` → `git update-index --cacheinfo` | phantom U+FFFD entry; caller's prior blob written there, real path keeps this call's `git add` content (the reproduction above) |
| 2 (`:529`) | post-`add` | `extra_paths` → `git rm --cached --ignore-unmatch` | corrupted path matches nothing, `--ignore-unmatch` exits **0**, so a file this call staged is left staged while `unstage_ok` reports success |

*Site 2's failure, reproduced against real git* — a **new, untracked**
non-UTF-8 file inside the pathspec, which the pre-`add` capture cannot see:

```
PRE-add  capture: b'...many/clean.txt\x00'                             LOSSY? no
POST-add capture: b'...many/clean.txt\x00...many/new\xffname.txt\x00'  LOSSY? YES
git rm --cached --ignore-unmatch -- ':(literal)many/new<U+FFFD>name.txt'
  exit=0                              <- reported success
  many/new\xffname.txt still staged   <- never unstaged
```

**A passing AAT-001 does not prove both sites are fixed.** The restore is
nested inside both gates, so failing *either* suppresses the phantom entry —
AAT-001 passes if only site 1 is guarded, only site 2 is, or both. Revert-verify
each guard **separately**: reverting site 1's must fail AAT-001, reverting
site 2's must fail AAT-003. If reverting one of them leaves the suite green,
that guard is unconstrained and the task is not done.

**Do not** extend this to `git_status`/`git_diff`/`git_log`. Their
replace-decoding is correct: their output is displayed, never fed back as an
argument, and `sanitize_for_client` already makes exactly this substitution
so the SDK's JSON serialization does not crash. Changing them would be an
over-correction, and AR-001's own scope note says so.

**Implemented as described.** `execution/service.py` gained a module-level
`decode_utf8_lossy(data: bytes) -> tuple[str, bool]` (beside `tail_text`):
strict decode attempted first, `errors="replace"` only on
`UnicodeDecodeError`, so a genuine U+FFFD already present in the source
bytes is never mistaken for one this function substituted. `CapturedOutput`
gained `stdout_lossy`/`stderr_lossy: bool = False`; `capture()` sets both via
the new helper. `tools/git.py`'s two `capture_index` call sites each gained
`and not <result>.stdout_lossy` alongside their existing
`not <result>.stdout_truncated` check — site 1 (line ~507) gates
`prior_entries`, site 2 (line ~557) gates the whole `current_entries`/
`extra_paths`/restore block, exactly mirroring how each already gates on
truncation.

**A genuine finding while writing the tests, not anticipated by the plan:**
this task's own draft checklist item ("Test both sites — they need two
different fixtures") undersold how tightly coupled the two guards are.
`build_restore_index_argv(prior_entries)` — the call that actually performs
the corrupting write — sits nested inside *both* `if` blocks, so my first
AAT-001 fixture (the one already written into this document: the *same* bad
path staged both before and after `add`) got caught by *either* guard and
proved nothing about site 1 in isolation. Revert-verifying site 1 alone
left it passing. Redesigned AAT-001 around a fixture only the pre-`add`
capture can see — a tracked non-UTF-8 path staged, then **deleted** from
the working tree before the call, so this call's own `git add` stages the
removal and the post-`add` re-capture no longer contains it at all. That
isolates site 1's guard as the *only* thing standing between `prior_entries`
and a corrupted restore. Documented in the test's own docstring so the next
reader doesn't repeat the mistake.

**New tests, watched fail first — and revised once, for real reasons.**
`tests/execution/../workspace/test_git_service_container.py` (primitive
level, alongside `test_capture_reports_truncation`):
`test_capture_reports_lossy_decode` — a real container `printf`s a literal
`\377` byte via `printf`'s own `\NNN` escape, `capture()` asserted
`stdout_lossy: true`, `stdout_truncated: false` (well under the byte cap),
and a clean `echo` asserted `stdout_lossy: false`.
`tests/workspace/test_git_commit_tool.py`:

- `test_at_aat001_a_non_utf8_path_present_only_pre_add_is_not_restored_as_a_phantom`
  — the isolating fixture above (staged-then-deleted). Asserts
  `index_left_dirty: true` and no phantom entry in the post-failure index.
- `test_at_aat003_a_new_non_utf8_path_the_calls_own_add_stages_is_left_honestly_dirty`
  — AR-003's own fixture: a **new, untracked** non-UTF-8 file inside the
  directory pathspec, invisible to the pre-`add` capture, staged only by
  this call's own `git add`. Asserts `index_left_dirty: true`.

**Revert-verified each guard separately, per this task's own instruction —
not just once.**
1. Reverted site 1's `and not capture_result.stdout_lossy` alone
   (site 2 intact): AAT-001 failed with `"index_left_dirty": false` — the
   exact lie AR-001 describes. AAT-003 still passed, confirming it is
   genuinely independent of site 1.
2. Restored, confirmed both pass. Reverted site 2's
   `and not current_result.stdout_lossy` alone (site 1 intact): AAT-003
   failed the same way; AAT-001 still passed.
3. Restored fully (diffed against the pre-task backup: clean). Both new
   tests, plus `test_rr005_a_failed_commit_restores_prior_staged_content_
   not_head` and `test_at_vat001_a_line_cropped_index_snapshot_does_not_
   crash_the_commit`, pass together.

**Suites.** `tests/workspace/test_git_commit_tool.py
tests/workspace/test_git_service_container.py tests/git/`: 223 passed, no
Docker leftovers. Full unscoped `tests/`: 997 passed, 0 failed, 537.80s, no
Docker leftovers. Lint/format clean; `mypy src`: no issues in 53 files. No
schema/contract change — `index_left_dirty`'s type and meaning are
unchanged (§17.4 already defines it as "the index could not be restored
faithfully"); no `CONTRACT_VERSION` bump.

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

## A3 — The post-`add` re-capture is independently constrained ✅
**Satisfies:** AR-003 · **Verified by:** AAT-003
**Commit:** `PLACEHOLDER`

- [x] Exercise `git_commit`'s post-`add` re-capture with a fixture the
      pre-`add` capture cannot see — a **new, untracked** non-UTF-8 file
      inside the directory pathspec
- [x] Confirm it fails when only site 2's guard is reverted

**Gap.** V1 converted two call sites to `capture_index`, but VAT-001 commits
*successfully* and so reaches only the first. The failure-branch re-capture
is covered today solely by
`test_rr005_a_failed_commit_restores_prior_staged_content_not_head`, whose
path trips neither the line crop nor the decode.

**Do this task with A1, not after it — and do not fold it into A1's test.**
An earlier draft of this task said the two "likely fold into one test" and
suggested checking whether a single test satisfies both. That was wrong, and
is corrected here: it is provably impossible. AAT-001's bad path is staged
*before* the call, so site 1's gate fires and short-circuits before site 2
runs; A3's must be untracked until this call's own `git add` stages it, so
site 1's capture is clean and only site 2's gate can fire. One fixture cannot
be in both states. Since the restore is nested inside both gates, AAT-001
passes with either guard alone — so without this test, half of A1's fix is
unconstrained and reverting it leaves the suite green (the RR-027
anti-pattern). See A1's own table and AR-001.

**Landed as one commit with A1, per this task's own instruction above** —
`test_at_aat003_a_new_non_utf8_path_the_calls_own_add_stages_is_left_
honestly_dirty` (`tests/workspace/test_git_commit_tool.py`) is A1's own
AAT-003, and both checklists here point at the same implementation, tests,
and revert-verification described under A1. See A1's write-up for the full
account, including the correction this task's own warning above predicted:
AAT-001's *original* draft fixture (before A3 was written) would have
proven nothing about site 1 in isolation, exactly the mirror-image mistake
this task exists to prevent for site 2.

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
