# Development Workspace MCP Server — Audit Specification

**Status:** Draft
**Version:** 0.1
**Date:** 2026-08-29
**Reviewed against:** `d894b4e`
**Source:** code review of `docs/REVIEW_TODO.md` V1–V8, after all eight closed

## 1. Purpose

This document specifies the corrections required by the code review of
2026-08-29, which audited the V1–V8 work that closed `docs/REVIEW_TODO.md`.
That review verified all eight tasks against the shipped code. Six of the
eight are correct and complete as written; V2, V3 and V4 were additionally
verified *beyond* their own tests (V2 against seven untested edge cases, V3
against `entrypoint.sh`'s full environment surface, V4 against `chown -R`'s
actual symlink behavior) and needed no change. What the audit found:

1. **One high-severity correctness defect**, client-reachable, confirmed
   end-to-end through the real MCP tool: `git_commit`'s index restore
   silently destroys a caller's staged work and reports success. It is the
   same defect V1 fixed, reached through a second mechanism V1 did not
   cover (AR-001).
2. **One incomplete fix**: V5 applied one of the two controls the branch it
   copied already had (AR-002).
3. **Two coverage gaps**, one in V1's own test, one systemic (AR-003,
   AR-004).

Requirements here are numbered `AR-nnn` and acceptance tests `AAT-nnn`, to
avoid collision with `docs/SPEC.md`'s `FR`/`AT`, `docs/HARDENING_SPEC.md`'s
`HR`/`HAT`, `docs/REMEDIATION_SPEC.md`'s `RR`/`RAT`, and
`docs/REVIEW_SPEC.md`'s `VR`/`VAT`. Verified zero collisions before
adoption.

## 2. Relationship to the other specifications

`docs/SPEC.md` remains the authority for what the system does. This document
is the authority for the defects listed here until its amendments (§6) are
applied, after which `docs/SPEC.md` governs again — the same precedence rule
`docs/HARDENING_SPEC.md` §2, `docs/REMEDIATION_SPEC.md` §2 and
`docs/REVIEW_SPEC.md` §2 established.

**Why this document exists at all, recorded honestly.**
`docs/REVIEW_TODO.md`'s own "Definition of done" states that a finding about
*that* document belongs in a correction paragraph there rather than in a
fifth review layer, on the grounds that the marginal layer costs more than
it catches. That reasoning was raised before this document was created and
was overridden deliberately, for a reason specific to AR-001: it is not a
documentation inaccuracy of the kind that paragraph anticipated, but a
client-reachable data-loss defect with its own reproduction, fix design,
and acceptance test. A correction paragraph cannot carry those. AR-002
through AR-004 are folded in here because they were found in the same pass,
not because each would independently justify a document. **This layer SHALL
NOT be extended**: a finding about *this* document goes in a correction
paragraph here, and the next defect of AR-001's severity belongs in
`docs/SPEC.md` and the main `docs/TODO.md`, not in a sixth file.

## 3. What the audit changed about the working assumptions

> **AR-000 (framing).** A string decoded from a command's output SHALL NOT be
> fed back into a command's argument list unless the decoding is known to
> have been faithful. `docs/REVIEW_SPEC.md`'s VR-000 established that a
> parser assuming *complete* input must not be fed from a capture that can
> truncate silently; AR-001 is the same rule one property further out —
> completeness is not sufficient, the bytes must also round-trip. A capture
> that is complete but lossy is still an unfaithful snapshot.

> **AR-000a (framing).** Where a control exists in two halves — a key-based
> check and a content-based check, say — extending the control to a new type
> SHALL extend both halves or record which half was deliberately omitted.
> AR-002 is `docs/REVIEW_SPEC.md` VR-000a ("every layer actually present")
> applied within a single function rather than across two branches.

---

## 4. Requirements

### AR-001 A lossy capture SHALL NOT be reconstructed into a command argument

**Severity: high — silent data loss, reported as success.**

`git_commit`'s failed-commit rollback (RR-005/HR-025) restores the caller's
pre-`add` index from a snapshot read through `ExecutionService.capture`,
which decodes with `errors="replace"` (`execution/service.py`). A staged path
that is not valid UTF-8 decodes to U+FFFD, and `build_restore_index_argv`
then writes the caller's prior blob to that *corrupted* path — creating a
phantom index entry while leaving this call's own `git add` content on the
real one. `restore_ok` is `True`, so the raised error reports
`index_left_dirty: false`.

*Reproduced end-to-end through the real MCP tool* (a failed `git_commit`
naming the directory `many`, which contains `bad\xffname.txt` with prior
staged content distinct from HEAD):

```
INDEX BEFORE: 2 entries
INDEX AFTER:  3 entries                                   <- phantom entry created
  many/bad<U+FFFD>name.txt  28fef7a = v2-prior-staged     <- restored to the WRONG path
  many/bad\xffname.txt      818f8e5 = v3-this-calls-own-add  <- caller's prior work LOST
error details: {"index_left_dirty": false}                <- claims the restore succeeded
```

This is RR-005's own defect — "a failed commit destroys the caller's staged
work" — reintroduced through the encoding mechanism, in the code path V1
touched. V1 fixed the *line-crop* way a snapshot can be unfaithful and left
the *encoding* way.

**Reachability is narrower than it first appears, and SHALL be stated
precisely.** Naming a non-UTF-8 path *directly* is not reachable: the
surrogate-escaped argument is mangled on the way in and `git add` fails
closed with `COMMAND_FAILED` before any rollback runs. The defect requires
the client to name a **directory** pathspec that *contains* a non-UTF-8
file — the client never names the bad path itself. (`docs/REVIEW_TODO.md`'s
own VAT-001 test uses exactly this directory-pathspec shape.)

**Faithful round-trip is impossible and SHALL NOT be attempted.** Verified
against a real container: a surrogate-escaped argument passed to
`docker exec` arrives as U+FFFD, because the Docker exec API is JSON and
cannot carry a non-UTF-8 byte —

```
real file in container:      badM-^?name.txt          (cat -v; \377 = 0xff)
argv sent as '\udcff':       ls: cannot access '/tmp/bad'$'\357\277\275''name.txt'
                                                       (\357\277\275 = U+FFFD)
```

So decoding the capture with `surrogateescape` (as `run_git_metadata_read`
does host-side) would fix the *parse* and still corrupt the *restore*. The
daemon SHALL therefore **fail closed**: detect that the capture's decoding
was lossy and skip the rollback entirely, reporting `index_left_dirty: true`
rather than performing a restore it cannot perform faithfully. This matches
RR-005's own stated principle — a leaked stage is recoverable, guessing
wrong about what to destroy is not.

**The signal SHALL originate at the read.** `CapturedOutput` SHALL report
whether its decoding replaced any byte, alongside the
`stdout_truncated`/`stderr_truncated` H36a added for the same reason: a
caller cannot detect a lossy decode from the decoded string alone (a genuine
U+FFFD in a path is indistinguishable from a replaced byte), so the flag has
to come from the read itself.

**Scope: read tools are NOT affected and SHALL NOT be changed.**
`git_status`/`git_diff`/`git_log` decode the same way and are correct to:
their output is *displayed*, never fed back as an argument, and
`sanitize_for_client` already converts such bytes to U+FFFD deliberately so
the MCP SDK's JSON serialization does not crash. Only the write-back path —
a decoded string becoming a command argument — is unsafe. Applying this
requirement to the read tools would be an over-correction.

### AR-002 Redaction's content scan SHALL cover every type redacted by key

`_redact` (`errors.py`) redacts a `str` on either a secret-hinted key **or**
secret-shaped content (`_looks_like_secret`). V5 extended redaction to
`bytes` on the key hint only; the content scan still runs on `str` alone.

*Reproduced:*

```
{"blob": "ghp_abcdefghijklmnopqrstuvwx"}   ->  {"blob": "[REDACTED]"}          str: caught
{"blob": b"ghp_abcdefghijklmnopqrstuvwx"}  ->  {"blob": b"ghp_abcdefghij..."}  bytes: LEAKS
```

V5 satisfied `docs/REVIEW_SPEC.md` VR-005's literal wording ("SHALL redact a
secret-flagged `bytes` value") while reproducing VR-000a's own anti-pattern.
The daemon SHALL either run the value-pattern scan against `bytes` as well,
or narrow V5's stated scope to "key-flagged only" — what is not acceptable
is a claim broader than the behavior.

**Three neighbouring types SHALL be decided explicitly, not left
undiscovered.** Verified current behavior:

```
{"secret": bytearray(b"ghp_abc")}  ->  unchanged        (leaks)
{"secret": memoryview(b"ghp_abc")} ->  unchanged        (leaks)
{"secret": frozenset({"hunter2"})} ->  unchanged        (leaks)
{"secret": NamedTuple("hunter2",1)} -> ('[REDACTED]',1) (redacted, but type degraded to tuple)
```

`frozenset` is already a documented, deliberate exclusion in V5's own code
comment. `bytearray` is not, and is `bytes`'s mutable twin — exactly the type
V5's own rationale covers. Each SHALL be either handled or recorded as a
deliberate exclusion with its reason, in code.

### AR-003 The rollback call site SHALL be covered by a parser-tripping path

V1 converted **two** call sites to `capture_index` — the pre-`add` snapshot
and the post-`add` re-capture in the failure branch — but VAT-001 commits
*successfully*, so it exercises only the first. The second is reached only by
a failed commit, and is covered today solely by
`test_rr005_a_failed_commit_restores_prior_staged_content_not_head`, which
uses an ordinary ASCII path that trips neither the line crop nor the decode.
A test SHALL exercise the rollback call site with a path that actually
stresses the parser.

### AR-004 Every acceptance-test series SHALL be citation-audited

`tests/test_acceptance_test_coverage.py` enforces that every `AT-nnn` in
`docs/SPEC.md` is cited somewhere under `tests/`. No equivalent audit exists
for `HAT-nnn` (`docs/HARDENING_SPEC.md`), `RAT-nnn`
(`docs/REMEDIATION_SPEC.md`), or `VAT-nnn` (`docs/REVIEW_SPEC.md`). All six
`VAT`s are cited today — verified — but nothing would catch a deletion.

This is pre-existing and systemic, not introduced by V1–V8; it is recorded
here because the audit surfaced it and no other document owns it. The audit
SHALL cover every acceptance-test series, or the omission SHALL be recorded
in the audit module itself as deliberate.

---

## 5. Acceptance tests

- **AAT-001** — A failed `git_commit` naming a directory pathspec that
  contains a non-UTF-8 path SHALL NOT add an index entry, SHALL NOT move the
  caller's prior staged blob to a different path, and SHALL report
  `index_left_dirty: true` (AR-001).
- **AAT-002** — `redact_secrets` SHALL redact a secret-shaped `bytes` value
  under a key that hints at nothing, matching the `str` branch's behavior for
  the identical content — or V5's scope SHALL be narrowed and this test
  SHALL assert the narrowed contract instead (AR-002).
- **AAT-003** — The failed-commit rollback SHALL restore faithfully, or
  report `index_left_dirty: true`, when the index snapshot contains a path
  that would break a naive parse (AR-003).
- **AAT-004** — Removing every citation of one `VAT` SHALL make the coverage
  audit fail for it, the same way `test_meta_removing_every_citation_for_an_
  at_makes_the_audit_fail_for_it` already proves for `AT` (AR-004).

## 6. Amendments to other documents

- **`docs/REVIEW_TODO.md`** — V1's write-up, to record that the fix covered
  the line-crop mechanism only, with a pointer to AR-001 (the "Correction"
  convention this project already uses); V5's write-up, likewise for AR-002.
- **`docs/REVIEW_SPEC.md`** — VR-001, to note that faithfulness (AR-000) is
  the property VR-000 generalizes to; VR-005, if V5's scope is narrowed
  rather than extended.
- **`docs/SPEC.md`** — no change expected. No tool contract changes: AR-001
  alters *when* `index_left_dirty` is `true`, which §17.4 already defines as
  "the index could not be restored faithfully", and this is precisely such a
  case. No `CONTRACT_VERSION` bump is expected for any task in this document.

## 7. Explicitly out of scope

Recorded so a later reader does not mistake omission for oversight:

- **Non-UTF-8 paths cannot be named directly through any structured Git
  tool.** The argument is mangled to U+FFFD at the Docker exec boundary, so
  `git add`/`git commit` fail closed with `COMMAND_FAILED`. Supporting such
  paths as *inputs* would require a transport that is not JSON, and is a
  feature, not a defect fix.
- **`git_status`/`git_diff`/`git_log` report U+FFFD for non-UTF-8 paths.**
  Deliberate (see AR-001's scope note) — `sanitize_for_client` exists to make
  exactly this substitution safe for JSON output.
- **A `set` of secrets collapses to one element when redacted.** Correct
  behavior: it hides cardinality rather than leaking it.
- **`docs/REVIEW_TODO.md`'s remaining unticked items** (R2's two
  deploy-key-blocked assertions, R21's deliberately-declined rename, R27's
  assessed-and-skipped module, H1's concurrent-suite-run collision half).
  All pre-existing, all already recorded as blocked or deliberate.
