# Development Workspace MCP Server — Review Specification

**Status:** Draft
**Version:** 0.1
**Date:** 2026-08-28
**Reviewed against:** `8301c72`
**Source:** code review of `docs/REMEDIATION_TODO.md` R1–R27, plus the H36/H37
work that landed after it (`docs/HARDENING_TODO.md`)

## 1. Purpose

This document specifies the corrections required by the code review of
2026-08-28. That review verified all 27 remediation tasks against the shipped
code and found **no task where a claimed fix was absent** — R1–R27 are
substantively implemented. What it did find falls into three kinds:

1. **Six correctness defects in shipped code**, none of which any existing
   test covers. One of them (VR-001) is the same defect class the review's
   own headline finding turned out to be, reached by a different route.
2. **Four ticked checkboxes or write-up claims that overstate what was
   done** — the exact failure mode `docs/REMEDIATION_SPEC.md` RR-033 exists
   to catch, now recurring in the document that introduced RR-033.
3. **Four stale references** left by H36's deletion of the host-side Git
   path, including one the reviewer introduced while doing H36.

Requirements here are numbered `VR-nnn` and acceptance tests `VAT-nnn`, to
avoid collision with `docs/SPEC.md`'s `FR`/`AT`, `docs/HARDENING_SPEC.md`'s
`HR`/`HAT`, and `docs/REMEDIATION_SPEC.md`'s `RR`/`RAT`.

## 2. Relationship to the other specifications

`docs/SPEC.md` remains the authority for what the system does. This document
is the authority for the defects listed here until its amendments (§8) are
applied, after which `docs/SPEC.md` governs again — the same precedence rule
`docs/HARDENING_SPEC.md` §2 and `docs/REMEDIATION_SPEC.md` §2 established.

**Already fixed, not tracked here.** The review's headline finding —
`ExecutionService.capture` silently truncating, making `git_log`/`git_status`
raise untranslated `ValueError`/`IndexError` and `git_diff` report
`truncated: false` on an incomplete diff — was fixed before this document was
written (H36a, commit `5e00986`, `docs/HARDENING_TODO.md`). It is referenced
below only as the established precedent for VR-001, which is the same class.

## 3. What the review changed about the working assumptions

Three findings generalize past their own line numbers and SHALL be treated as
standing rules:

> **VR-000 (framing).** A parser that assumes complete input SHALL NOT be fed
> from a capture path that can truncate without saying so. The truncation
> signal SHALL originate at the read, not be inferred downstream: output whose
> length happens to equal the cap is indistinguishable from output that was
> cut. H36a established this for the byte cap; VR-001 applies it to the
> *line* cap, which is a second, independent truncation mechanism on the same
> data.

> **VR-000a (framing).** "Defense in depth" SHALL mean every layer actually
> present, not one layer plus a comment asserting a second. Two of this
> review's findings (VR-003, VR-004) are places where a control was applied to
> one branch or one block and the neighbouring one was left uncovered while
> prose claimed both.

> **VR-000b (framing).** A ticked checkbox SHALL describe work done, not work
> considered and declined. Where a task deliberately does not do what its own
> checkbox says, the checkbox SHALL be unticked or reworded — the reasoning
> belongs in the write-up, not in a tick.

---

## 4. Correctness defects

### VR-001 A line-cropped capture SHALL NOT reach a strict parser

`git_commit`'s index snapshot is read through `ExecutionService.consume`,
which crops `stdout_tail` to the last `response_tail_lines` (default 200)
**lines**. `ExecResult.stdout_truncated` reflects only the *byte* cap, so it
is `False` while the data is already cropped. `parse_index_entries` then
raises an untranslated `ValueError`.

*Reproduced:*

```
bytes: 16140  under 10MiB cap -> stdout_truncated = False
tail is a prefix of full?  False        tail dropped bytes: 3248
!! parse_index_entries: ValueError not enough values to unpack (expected 3, got 1)
```

`ValueError` is not a `WorkspaceError`, so `translate_workspace_errors` cannot
classify it and it reaches the client as an unstructured crash — the R5 defect
class again.

**R4's stated safety argument is factually wrong and SHALL be corrected.** It
reads: *"a NUL-separated listing has no newlines to split on, so a large
listing is truncated only by the byte cap, never silently by line count."*
Git does not escape newlines in `-z` output. Verified against real git:

```
0000060   0  \t   a  \n   b   .   t   x   t  \0      <-- literal newline inside the path
```

The daemon SHALL either use a capture path that does not line-crop, or detect
the crop and fail closed. The call site (`tools/git.py`, the pre-`add`
capture) additionally sits **outside** the `contextlib.suppress(Exception)`
that covers the rollback re-parse, so it is unguarded on both counts.

Reachability requires >200 newline-bearing paths matching the client's
pathspecs. Exotic, but this codebase treats hostile filenames as in scope
(HR-022's entire `surrogateescape` effort), and R4's own guarantee is about
not destroying a caller's staged work.

### VR-002 `truncated` SHALL reflect bytes withheld, not bytes requested

`filesystem/service.py`'s `byte_range` branch computes
`truncated = span_len > limit` from the client's *requested* span, ignoring
how much of the file actually exists. A client that receives the file's entire
remaining content is still told the response was truncated, and one paging on
that flag never terminates.

*Reproduced* (`max_read_bytes=500`, a 10-byte file):

```
byte_range=(0,1000) -> content='0123456789' truncated=True    <- complete, but flagged
byte_range=(5,1000) -> content='56789'      truncated=True    <- complete, but flagged
```

`truncated` SHALL compare the bytes actually withheld —
`min(effective_end, total_size) - start` against `limit` — not the requested
span. The defect requires both `end - start > min(max_bytes, max_read_bytes)`
and `total_size - start <=` that limit — the client asking for a wider span
than its own cap, on a file with less than a cap's worth remaining. Ordinary
pagination is unaffected, including a last page whose `end` runs past EOF
(verified directly against the default 10 MiB limit), which is why no existing
test caught it.

### VR-003 Daemon-controlled variables SHALL be re-asserted in every branch

`build_workspace_run_kwargs` merges `extra_env` first and daemon values last —
in the **non-root** branch. The **root** branch sets only `MCP_WORKSPACE_PATH`
and `MCP_CACHE_PATHS`, so `MCP_UID`, `MCP_GID`, `MCP_SUDO`, `MCP_SSH_MODE`,
and `SSH_AUTH_SOCK` pass through from `extra_env` unopposed.

*Reproduced* (`container.user: root`):

```
root-mode environment: {'MCP_UID': '0', 'SSH_AUTH_SOCK': '/evil',
                        'MCP_WORKSPACE_PATH': '/workspace', 'MCP_CACHE_PATHS': ''}
```

The docstring at that site claims *"ordering alone still stops a
project-supplied `MCP_UID`/`MCP_GID`/`SSH_AUTH_SOCK`"* — false in root mode,
because the daemon never sets them there for ordering to act on. Only
`project_config.py`'s allowlist rejects them, leaving exactly one layer in the
branch where HR-006's own rationale ("neither control is sufficient alone")
asked for two. This is the same argument R20 used to justify fixing
`MCP_CACHE_PATHS`; it applies unchanged here.

The daemon SHALL either set every daemon-controlled variable in both branches,
or the comment SHALL be corrected to state which branch it actually describes.
`MCP_USER` is never daemon-set in either branch and SHALL be assessed the same
way.

### VR-004 The entrypoint's `.ssh` provisioning SHALL NOT follow symlinks

R19 hardened the entrypoint's **cache loop** against symlinked directories
(refuses on `[ -L ]`, plain `mkdir`, never `mkdir -p`). The `.ssh` block
immediately below it does `mkdir -p "$HOME_DIR/.ssh"`, redirects into
`"$HOME_DIR/.ssh/config"`, then `chmod`/`chown -R` — all as **root**, before
the identity drop, with no symlink checks at all.

`caches[].path` reaches `$HOME` and is project-authored. Verified accepted:

```
'$HOME/.ssh'  -> '.ssh'        '.ssh' -> '.ssh'
'$HOME/.'     -> '.'           '.'    -> '.'
```

So a project can mount a **persistent, container-writable** cache volume over
`$HOME/.ssh` (or all of `$HOME`), plant `config -> <target>` in one run, and
have the next run's root phase follow it — truncating and `chmod 600`-ing the
target, with `chown -R` following the directory symlink.

**Severity is posture-dependent and SHALL be stated honestly.** Under the
default `container.sudo: true`, the client already has container root through
`exec`, so this grants nothing new. Under `container.sudo: false` — a
documented, supported hardening posture (§74) — it is a genuine escalation to
container root via the entrypoint's own root phase. It is contained within the
container in both cases; it is not a host escape.

The `.ssh` block SHALL apply the same symlink refusal the cache loop already
does, or `caches[].path` SHALL reject paths that collide with daemon-managed
home subdirectories.

### VR-005 Redaction SHALL cover every container type it can encounter

`redact_secrets` recurses into `dict` and `list` only, and redacts only `str`
leaves. Verified:

```
{'secret': ('hunter2',)}  ->  {'secret': ('hunter2',)}     <- tuple: leaks
{'token': b'ghp_abc'}     ->  {'token': b'ghp_abc'}        <- bytes: leaks
{'secret': {'a':'hunter2'}} -> {'secret': {'a':'[REDACTED]'}}   (dict: correct)
{'secret': ['x']}         ->  {'secret': ['[REDACTED]']}       (list: correct)
```

R18's rationale — "only a string literally *can be* a secret" — is false for
`bytes`. Reachability is low today (error `details` are JSON-shaped in
practice), but the requirement R18 states is about the redactor's own
completeness, and a future caller passing a token as `bytes` would silently
defeat it. Redaction SHALL recurse `tuple`/`set` and SHALL redact `bytes`
under a secret-flagged key, or R18's stated scope SHALL be narrowed to match
what it actually does.

### VR-006 An unexplained pid absence SHALL be logged

`pid_of` warns when the retry budget is exhausted, but its
`except docker.errors.DockerException: return None` branch returns silently.
Both produce the same `None`, and R16's requirement is that an absence be
*surfaced*. The Docker-failure branch SHALL log at the same level, so the two
absences are distinguishable in the daemon's own logs.

---

## 5. Overstated claims (RR-033 class)

Each SHALL be corrected in `docs/REMEDIATION_TODO.md`. None requires a code
change; all four are the document describing itself inaccurately.

### VR-007 R21's second checkbox is ticked for work explicitly declined

> `- [x] Until then, give each run a unique repository id…`

`tests/workspace/conftest.py`'s `repository` fixture still hard-codes
`id="example"`. R21's own body says the rename was *"considered and
deliberately not done"*, arguing creation-tracking made it unnecessary. That
argument is sound; the tick is not. SHALL be unticked or reworded per VR-000b.

### VR-008 R23 claims "exact" input-class matching where it substituted

`docs/REMEDIATION_SPEC.md`'s RR-026 table specifies **unreachable Docker
daemon** for the failed-termination row. The cited test
(`test_rr013_failed_termination_via_a_real_exec_inspect_failure`) removes the
container so `exec_inspect` returns 404. R23's write-up asserts *"all seven
were already exercising the **exact** adversarial input class the table
demands."*

The substitute is arguably stronger than the specified input — a real 404
beats a mocked unreachable daemon — but it is a substitution presented as an
exact match. SHALL be restated as the substitution it is, with the reasoning.

### VR-009 R27's closing paragraph generalizes past its own evidence

> "the tool body is now unit-testable **without constructing a running
> `MCPServer`**"

True for `open_workspace` and `run_patch_file`. **Not** true for `run_exec` or
`run_git_commit` — both tests call `create_server`. Each per-module write-up
discloses this honestly; only the generalizing summary above them does not.
SHALL be scoped to the modules it actually holds for.

### VR-010 R25's checkbox overstates the check by one word

> `- [x] Reject a **non-increasing** `contract_version``

CI rejects only a *decrease* (`sort -V | tail -1`); an unchanged version
passes, which is the correct behavior — every no-op commit would otherwise
fail. The CI step's own comment says "decrease" accurately. Only the checkbox
and RR-028's wording overstate. SHALL be reworded to "decrease".

---

## 6. Stale references left by H36

### VR-011 R1 has no supersession note

H36 deleted `run_git_host_side` and with it `_discover_code_executing_overrides`,
`_host_side_git_env`, `_CODE_EXECUTING_CONFIG_KEY_RE`, both env-scrub tuples,
and all four of R1's tests. R1's entire implementation is gone. R2 and R26
carry supersession notes; R1 — the most completely superseded task in the
document — does not. SHALL get one.

### VR-012 A test docstring cites a function H36 deleted

`tests/git/test_remote.py` justifies importing a private helper by citing
`git/service.py`'s `_discover_code_executing_overrides` as precedent. That
function no longer exists — H36 step 7 wrote the justification and H36 step 8
deleted the thing it pointed at. Introduced by the reviewer during H36 and
SHALL be corrected.

### VR-013 R22 and R24 describe tests H36 deleted or rewrote

R22's second checkbox describes a `run_git_host_side` test H36 deleted (the
property survives in the successor in-container test). R24's first item names
`test_rr027_*_in_the_real_argv` and a `subprocess.run`-spy mechanism that H36
replaced with direct argv inspection. Both still read as current. SHALL be
annotated the way R2 and R26 already are.

### VR-014 H35's prose contradicts H35's own corrected table

R26 corrected H35's *table* row about S15's pid race, and corrected
`docs/TODO.md` to say the race is real (closed by R16). H35's **prose**, two
paragraphs below its own table in the same file, still asserts the race *"was
re-checked … and found accurate today … there is nothing left to correct."*
§16 scoped R26's edit to `docs/TODO.md`, so this is outside R26's letter — but
it leaves a disproven claim standing in a file R26 did edit. SHALL be
corrected.

---

## 7. Acceptance tests

- **VAT-001** — A `git ls-files -z --stage` listing containing more than
  `response_tail_lines` newline-bearing paths SHALL NOT produce an
  untranslated exception; `git_commit`'s failure path SHALL either restore
  faithfully or report `index_left_dirty` (VR-001).
- **VAT-002** — `read_file` with a `byte_range` whose end exceeds the file
  size SHALL report `truncated: false` when the whole remainder was returned,
  for a `max_read_bytes` smaller than the requested span (VR-002).
- **VAT-003** — With `container.user: root` and an `extra_env` carrying
  `MCP_UID`/`MCP_GID`/`SSH_AUTH_SOCK`, the resulting container environment
  SHALL NOT carry the project-supplied values (VR-003).
- **VAT-004** — With `$HOME/.ssh` (or `$HOME/.`) mounted as a persistent cache
  volume containing `config` as a symlink to another path, a container start
  SHALL NOT write through that symlink (VR-004).
- **VAT-005** — `redact_secrets` SHALL redact a secret-flagged `bytes` value
  and SHALL recurse into `tuple`/`set` (VR-005).
- **VAT-006** — A `DockerException` from `exec_inspect` inside `pid_of` SHALL
  emit a log record (VR-006).

VR-007 through VR-014 are documentation corrections and have no acceptance
test; they are verified by reading the corrected text against the code it
describes.

---

## 8. Amendments to other documents

- **`docs/REMEDIATION_TODO.md`** — R21 checkbox 2 (VR-007), R23's "exact"
  claim (VR-008), R27's closing paragraph (VR-009), R25 checkbox 2 (VR-010),
  R1 supersession note (VR-011), R22/R24 annotations (VR-013), and R4's
  incorrect newline rationale (VR-001).
- **`docs/REMEDIATION_SPEC.md`** — RR-028's "non-increasing" wording
  (VR-010); RR-026's failed-termination row, to record the substitution
  (VR-008).
- **`docs/HARDENING_TODO.md`** — H35's prose claim about S15 (VR-014).
- **`docs/SPEC.md`** — §12.2, if VR-002's fix changes the documented meaning
  of `truncated` for a `byte_range` read. No other section is affected; no
  tool contract changes, so no `CONTRACT_VERSION` bump is expected for any
  task in this document.

## 9. Explicitly out of scope

Recorded so they are not silently dropped, and so a later reader does not
mistake omission for oversight:

- **`git_log`'s `max_entries` is unbounded above.** H36a converted the
  resulting crash into a clean typed error. Adding an upper bound is a
  contract-visible decision (§17.3 says "bounded log" without a number) and
  belongs in a spec change, not a defect fix.
- **Binary classification inspects only the first `max_read_bytes`.** A file
  whose first window decodes as text is reported `is_binary: false` regardless
  of what follows. Inherent to H29's bounded read; R13 never claimed
  otherwise.
- **`reauthorized_job` looks the job up before authorizing.** A deauthorized
  caller can distinguish "no such job" from "job exists, not authorized" by
  error code. Negligible, and no requirement states otherwise.
- **`reconcile`'s narrow stale-`oom_killed` path.** Reachable only after a
  crash between `_recreate`'s `container_id=None` save and `_run_container`.
  Outside R15's enumerated transitions.
- **Concurrent suite runs collide on hard-coded container/volume names.**
  Already tracked as a known gap in H1; VR-007 corrects only the checkbox that
  claims it was addressed, not the gap itself.
