# Review TODO

**Spec:** `docs/REVIEW_SPEC.md` (VR-001..VR-014, VAT-001..VAT-006)
**Source:** code review of `docs/REMEDIATION_TODO.md` R1–R27 plus the H36/H37
work that landed after it, 2026-08-28, against `8301c72`
**Status:** not started

Fourteen items: six correctness defects in shipped code, four overstated
claims, four stale references. Every one was reproduced against the running
code before being written down — each task below carries the reproduction so
the implementer can re-run it rather than trust this file.

**Not in this list:** the review's headline finding (`ExecutionService.capture`
truncating silently, crashing `git_log`/`git_status` and making `git_diff`
claim completeness) was already fixed as **H36a** (`5e00986`,
`docs/HARDENING_TODO.md`). V1 below is the same defect class reached through a
different truncation mechanism, and should follow H36a's shape.

## How to work this list

The discipline `docs/HARDENING_TODO.md` established and
`docs/REMEDIATION_TODO.md` extended, unchanged:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.** Every defect here
   shipped with a passing suite; four of them shipped *with* a test that was
   supposed to cover the area and chose an input that happened to work.
3. Run the new test alone, then the relevant suite, then the full suite.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> >
   <file>`, confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck. Check for Docker leftovers.
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION` per
   §33.2. **No task here is expected to need one** — if one does, that is a
   signal the fix changed a contract and should be re-examined.
7. One task per commit. No `Co-Authored-By` trailers.
8. **Re-run every claim before writing it in the task's write-up** (RR-033).
   This document exists partly because that rule was not followed in four
   places in the document it reviews.

**Ordering.** Phase 0 first, in descending *consequence* — not reachability;
the earlier wording said reachability and was wrong. The six defects are
independent of one another, so the order is a convenience, not a dependency.
V1 leads because it is the only one that can both crash a call untranslated and
put R4's index-restore guarantee at risk. V2 is the most easily *reached* — no
hostile input at all, see its own trigger condition below — but its worst case
is a wrong boolean and a client that loops. Phase 1 and Phase 2 are
documentation-only, independent of each other and of Phase 0, and can be done
in any order — or batched, since none of them touches code.

---

## Phase 0 — Correctness defects in shipped code

### V1 — A line-cropped capture never reaches a strict parser
**Satisfies:** VR-001 · **Verified by:** VAT-001

- [ ] `git_commit`'s pre-`add` index snapshot no longer parses a
      line-cropped `stdout_tail`
- [ ] The unguarded `parse_index_entries` call cannot raise an untranslated
      exception
- [ ] Correct R4's "no newlines in `-z` output" rationale in
      `docs/REMEDIATION_TODO.md`
- [ ] Test with >`response_tail_lines` newline-bearing paths

**Defect (reproduced).** `consume` crops `stdout_tail` to the last
`response_tail_lines` (200) **lines**; `stdout_truncated` reflects only the
*byte* cap, so it is `False` while the data is already cropped:

```
bytes: 16140  under 10MiB cap -> stdout_truncated = False
tail is a prefix of full?  False        tail dropped bytes: 3248
!! parse_index_entries: ValueError not enough values to unpack (expected 3, got 1)
```

Git does not escape newlines in `-z` output — R4's stated safety argument is
factually wrong. Verified against real git:

```
0000060   0  \t   a  \n   b   .   t   x   t  \0      <-- literal newline inside the path
```

**Shape of the fix.** H36a's precedent applies: the signal belongs at the
read, not inferred downstream. Either give this call site a capture path that
does not line-crop (`capture`, which H36a already made truthful, is the
obvious candidate — it is head-truncating and now reports it), or detect the
crop and fail closed. Prefer the former: it removes the failure mode rather
than reporting it, and `capture` is already the right shape for "parse this
whole output" as opposed to "show a human the tail".

Note the call site sits **outside** the `contextlib.suppress(Exception)` that
covers the rollback re-parse — so it is unguarded independently of which fix
is chosen.

**Do not** simply widen the `suppress`: R4's whole guarantee is that a failed
commit restores the caller's staged work or honestly reports
`index_left_dirty`, and swallowing the parse error silently would trade a
crash for the data-loss R4 exists to prevent.

### V2 — `truncated` reflects bytes withheld, not bytes requested
**Satisfies:** VR-002 · **Verified by:** VAT-002

- [ ] `truncated` compares `min(effective_end, total_size) - start` against
      `limit`
- [ ] Test a `byte_range` end past EOF with a `limit` smaller than the
      requested span

**Defect (reproduced)** — `max_read_bytes=500`, 10-byte file:

```
byte_range=(0,1000) -> content='0123456789' truncated=True    <- complete, but flagged
byte_range=(5,1000) -> content='56789'      truncated=True    <- complete, but flagged
```

**Exact trigger, verified against the default config** (10 MiB
`max_read_bytes`). The defect needs *both*
`end - start > min(max_bytes, max_read_bytes)` **and**
`total_size - start <=` that same limit — the client asking for a span wider
than its own cap, on a file with less than a cap's worth left. Ordinary
pagination does **not** trip it; a chunked reader's last page, including one
whose `end` runs past EOF, was checked directly and reports correctly:

```
byte_range=(950,1050) max_bytes=100 -> got   50B, truncated=False   (correct)
byte_range=(0,20000000)             -> got 1000B, truncated=True    <- WRONG
```

The reachable idiom is "read the rest of the file" written as a large explicit
`end` instead of `end=None` (which resolves to `total_size` and is correct).
A client paging on the flag then never terminates — confirmed on a 1000-byte
file:

```
iter 0: start=   0 got=1000B truncated=True
iter 1: start=1000 got=   0B truncated=True    <- past EOF, still flagged
iter 2: start=1000 got=   0B truncated=True    ... forever
```

Write VAT-002 against this condition, not against the smaller `max_read_bytes`
the original reproduction used — a test that only shrinks the config limit
proves less than one that uses the default and an oversized explicit `end`.

**Check while here:** whether `docs/SPEC.md` §12.2's description of
`truncated` for a `byte_range` read needs the same correction, or already
says what the fixed code will do.

### V3 — Daemon-controlled variables are re-asserted in every branch
**Satisfies:** VR-003 · **Verified by:** VAT-003

- [ ] The root branch sets every daemon-controlled variable the non-root
      branch does, or its comment is corrected to say which branch it covers
- [ ] Assess `MCP_USER`, never daemon-set in either branch
- [ ] Test with `container.user: root` and a hostile `extra_env`

**Defect (reproduced)** — `container.user: root`:

```
root-mode environment: {'MCP_UID': '0', 'SSH_AUTH_SOCK': '/evil',
                        'MCP_WORKSPACE_PATH': '/workspace', 'MCP_CACHE_PATHS': ''}
```

The docstring claims ordering stops exactly these; in root mode the daemon
never sets them, so there is nothing for ordering to act on. Only
`project_config.py`'s allowlist rejects them — one layer, in the branch where
HR-006's own "neither control is sufficient alone" asked for two. This is
R20's argument, unchanged, applied to the branch R20 didn't look at.

**Decide explicitly, and record the decision:** setting `MCP_UID`/`MCP_GID` in
root mode may be wrong on its own terms (root mode deliberately does *not*
create a user or drop privileges). If so, the correct fix is to set them to a
value that preserves root-mode semantics, or to strip daemon-controlled keys
from `extra_env` in both branches rather than overwrite them. Do not
mechanically copy the non-root branch's values across.

### V4 — The entrypoint's `.ssh` provisioning does not follow symlinks
**Satisfies:** VR-004 · **Verified by:** VAT-004

- [ ] The `.ssh` block refuses a symlinked `$HOME/.ssh`, matching the cache
      loop R19 already hardened
- [ ] Or `caches[].path` rejects collisions with daemon-managed home
      subdirectories
- [ ] Test with a cache volume mounted at `.ssh` containing a symlinked
      `config`

**Defect (reproduced).** `caches[].path` is project-authored and reaches
`$HOME`:

```
'$HOME/.ssh'  -> '.ssh'        '.ssh' -> '.ssh'
'$HOME/.'     -> '.'           '.'    -> '.'
```

The `.ssh` block runs as **root**, before the identity drop, with `mkdir -p`,
a redirect into `$HOME_DIR/.ssh/config`, `chmod`, and `chown -R` — none of the
`[ -L ]` refusals the cache loop directly above it applies. A persistent cache
volume mounted there survives between runs, so a symlink planted in one run is
followed by the next run's root phase.

**Where the test goes.** A shell-script change needs a real container, not a
host-side unit test. R19's own hardening left the precedent to copy:
`tests/workspace/test_default_image.py`'s
`test_rr030_a_symlink_planted_in_a_cache_volume_is_not_followed` — it plants a
symlink inside a persistent cache volume on one run and asserts the next run
refuses. VAT-004 is that test with `.ssh`/`config` substituted for the cache
path.

**Severity, stated honestly in the write-up:** under the default
`container.sudo: true` the client already has container root through `exec`,
so this grants nothing new. Under `container.sudo: false` — supported, and
listed in §74 — it is a real escalation to container root. Contained within
the container either way; not a host escape. Say this plainly rather than
inflating or dismissing it.

### V5 — Redaction covers every container type it can encounter
**Satisfies:** VR-005 · **Verified by:** VAT-005

- [ ] Recurse `tuple`/`set`, or narrow R18's stated scope to match reality
- [ ] Redact `bytes` under a secret-flagged key
- [ ] Test both

**Defect (reproduced):**

```
{'secret': ('hunter2',)}  ->  {'secret': ('hunter2',)}     <- tuple: leaks
{'token': b'ghp_abc'}     ->  {'token': b'ghp_abc'}        <- bytes: leaks
```

R18's rationale — "only a string literally *can be* a secret" — is false for
`bytes`. Reachability is low (error `details` are JSON-shaped in practice), so
**narrowing R18's claim is an acceptable outcome** if recursion turns out to
cost more than it buys; what is not acceptable is leaving the claim broader
than the behavior.

### V6 — An unexplained pid absence is logged
**Satisfies:** VR-006 · **Verified by:** VAT-006

- [ ] `pid_of`'s `DockerException` branch logs before returning `None`
- [ ] Test that the log record is emitted

**Defect.** `pid_of` warns when the retry budget is exhausted but returns
silently on `DockerException`. Both produce `None`; R16 requires an absence be
*surfaced*, and one of the two paths is invisible in the daemon's own logs.

Smallest task here. Worth doing with V1 or on its own; not worth a phase.

---

## Phase 1 — Overstated claims

All four are `docs/REMEDIATION_TODO.md` describing itself inaccurately. No
code changes. Reasonable to land as **one commit** — they are the same class
of correction and splitting them adds ceremony without adding review value.

### V7 — Correct the four overstatements
**Satisfies:** VR-007, VR-008, VR-009, VR-010

- [ ] **R21 checkbox 2** — untick or reword. `tests/workspace/conftest.py`'s
      `repository` fixture still hard-codes `id="example"`; R21's own body
      says the rename was "considered and deliberately not done". The
      reasoning is sound and stays; the tick goes.
- [ ] **R23's "exact"** — RR-026 specifies *unreachable Docker daemon*; the
      cited test removes the container so `exec_inspect` 404s. Restate as the
      substitution it is. The substitute is arguably stronger — say that too,
      rather than only conceding the mismatch.
- [ ] **R27's closing paragraph** — "without constructing a running
      `MCPServer`" holds for `open_workspace` and `run_patch_file`, not for
      `run_exec` or `run_git_commit` (both call `create_server`). Scope it to
      the two it holds for; the per-module write-ups above it are already
      accurate and need no change.
- [ ] **R25 checkbox 2** — "Reject a **non-increasing** `contract_version`";
      CI rejects only a *decrease*, which is correct behavior (an unchanged
      version must pass). Reword to "decrease". The CI step's own comment is
      already accurate.
- [ ] Amend `docs/REMEDIATION_SPEC.md` RR-028's wording (V10's half) and
      RR-026's failed-termination row (V8's half) to match

---

## Phase 2 — Stale references left by H36

Also documentation-only. Also reasonably **one commit**.

### V8 — Annotate what H36 superseded
**Satisfies:** VR-011, VR-012, VR-013, VR-014

- [ ] **R1** — add the supersession note it lacks. H36 deleted
      `run_git_host_side`, `_discover_code_executing_overrides`,
      `_host_side_git_env`, `_CODE_EXECUTING_CONFIG_KEY_RE`, both env-scrub
      tuples, and all four of R1's tests. R1's implementation is entirely
      gone; R2 and R26 already carry notes, R1 — the most completely
      superseded task in the document — does not.
- [ ] **`tests/git/test_remote.py`** — its docstring justifies importing a
      private helper by citing `_discover_code_executing_overrides` as
      precedent. That function no longer exists: H36 step 7 wrote the
      justification, H36 step 8 deleted its referent. Self-inflicted during
      H36 and worth fixing on those grounds alone.
- [ ] **R22 checkbox 2 / R24 item 1** — both describe tests H36 deleted or
      rewrote (`run_git_host_side`'s `diff.external` test; the
      `test_rr027_*_in_the_real_argv` `subprocess.run`-spy pair, replaced by
      direct argv inspection). Both properties survive in successors.
      Annotate the way R2 and R26 already are.
- [ ] **`docs/HARDENING_TODO.md` H35 prose** — still asserts S15's pid race
      "was re-checked … and found accurate today … there is nothing left to
      correct", two paragraphs below the table R26 corrected in that same
      file, and contradicting `docs/TODO.md`'s R26-corrected text. §16 scoped
      R26 to `docs/TODO.md`, so this is outside R26's letter — but it leaves a
      disproven claim standing in a file R26 edited.

---

## Definition of done

All six correctness defects are closed and covered by VAT-001..VAT-006; the
full suite passes with no Docker-dependent test skipped; a suite run leaves
foreign Docker state untouched; no ticked checkbox in `docs/REMEDIATION_TODO.md`
overstates what the code does; and no task write-up in this file carries a
claim that was not re-run against the code before being written (RR-033).

**One deliberate asymmetry, recorded so it is not mistaken for an oversight:**
this document's Phase 1 and Phase 2 correct *other documents'* accuracy. If
working this list produces a finding about **this** document, it goes in the
same place — a correction paragraph here — rather than a fifth review layer.
Three layers of review documents (hardening → remediation → review) is already
the point at which the marginal layer costs more than it catches; the next
finding belongs in the task it corrects, not in a new file.
