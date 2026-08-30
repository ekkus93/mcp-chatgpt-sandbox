# Gap TODO

**Spec:** `docs/GAP_SPEC.md` (GR-001..GR-011, GAT-001..GAT-008)
**Source:** code review of `docs/REVIEW_TODO.md` V1–V8 plus the five
file-splitting commits that followed `docs/AUDIT_TODO.md`, 2026-08-29,
against `fca1199`
**Status:** not started

Nine tasks. Two are regressions this project's own refactoring
introduced — one currently breaking CI, one making a test pass while
verifying nothing; two close a security control that stops short of the
threat model its own code comment states; the rest are correctness, test,
and reference gaps.

**V1–V8 were verified and are not re-opened here.** All six correctness
defects are genuinely closed and all four documentation corrections check
out. Where a task below touches an area V1–V8 covered (G4 extends AR-001's
guard; G6 adds the test half of VR-004 that VAT-004 missed), it says so
explicitly rather than implying the earlier work was wrong.

## How to work this list

Unchanged from `docs/HARDENING_TODO.md` / `docs/REMEDIATION_TODO.md` /
`docs/REVIEW_TODO.md` / `docs/AUDIT_TODO.md`, with one addition at step 5
that this list exists partly because of:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.**
3. Run the new test alone, then the relevant suite, then the full suite.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> >
   <file>`, confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck — **using `uv run`, never a bare binary**:
   ```
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   ```
   A bare `ruff`/`mypy` on `PATH` may be a different version with a
   different verdict. G1 exists because that happened; until G8 lands
   there is no hook to catch it for you. Check for Docker leftovers.
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION`
   per §33.2. **No task here is expected to need one** — if one does, that
   is a signal the fix changed a contract and should be re-examined.
   Specifically for G4, which looks like it might: §33.2 predicates a bump
   on *a change to `schemas/tools.json`*, and that file carries only each
   tool's `input_schema`/`output_schema` — it enumerates no error
   conditions at all (verified). A new failure mode therefore does not
   reach it. Run the drift check anyway rather than reasoning from this
   paragraph.
7. One task per commit. No `Co-Authored-By` trailers.
8. **Re-run every claim before writing it in the task's write-up**
   (RR-033).

**Ordering.** Task numbers are stable identifiers, not an execution
order. Recommended order: **G1, then G9, then G2/G3**, then the rest.

G1 first: it is trivial, and until it lands the gate that validates every
task after it is red. **G9 immediately after** — it is the other
`05fe306` regression, it is the reason a green full-suite run currently
proves less than it appears to, and closing its logging-state leak makes
every subsequent task's revert-verification trustworthy. G2/G3 next, in
descending consequence — they are the only findings here with a security
impact. G4–G7 are independent of one another and of everything above; any
order. G8 last, because its own guard is most useful once there is nothing
left to trip over — though moving it earlier is defensible if the work is
spread over multiple sessions.

**G2 and G3 are complementary halves of one vulnerability, not
duplicates.** G2 closes the input; G3 closes the mechanism. Either alone
reduces the exposure; only both together mean a future config surface
cannot reopen it. HR-006's own "neither control is sufficient alone"
reasoning is the precedent. Land them as separate commits (each is
independently testable) but do not stop after one.

---

## Phase 0 — The repository does not pass its own gate

### G1 — Restore the lint gate ✅
**Satisfies:** GR-001 · **Verified by:** GAT-001
**Commit:** `c9e953e`

- [x] `uv run ruff check .` exits 0
- [x] `uv run ruff format --check .` exits 0
- [x] No behavior change — imports reordered only (the 3 `05fe306` files);
      whitespace-only inside an illustrative code sample (the 2
      unrelated `docs/` files, see below)

**Defect (reproduced).** At `fca1199`:

```
$ uv run ruff check .
I001 Import block is un-sorted or un-formatted
  --> src/dev_workspace_mcp/workspace/manager_core.py:14:1
  --> src/dev_workspace_mcp/workspace/manager_lifecycle.py:16:1
  --> src/dev_workspace_mcp/workspace/records.py:20:1
Found 3 errors. [*] 3 fixable with the `--fix` option.
```

All three files were created by `05fe306`. The pre-split `manager.py`
passes — verified by extracting it and running the pinned ruff against it:

```
$ git show 05fe306^:src/dev_workspace_mcp/workspace/manager.py > /tmp/presplit.py
$ uv run ruff check --no-cache /tmp/presplit.py
All checks passed!
```

**Root cause.** Two ruff binaries:

```
$ ruff --version           # bare, on PATH
ruff 0.12.0
$ uv run ruff --version    # project-pinned
ruff 0.16.4
```

`05fe306` ran the bare one. Its `--fix` **actively rewrote** the import
blocks into the order 0.16.4 rejects — moving `from docker.errors …`
below the first-party `dev_workspace_mcp` imports. This was not a skipped
check; it was a wrong edit made by the wrong tool.

**Shape of the fix.** `uv run ruff check --fix` on the three files. The
resulting diff restores the third-party-before-first-party grouping the
rest of the codebase already uses — confirmed with `--diff` on
`records.py`:

```diff
-from dev_workspace_mcp.workspace.naming import WorkspaceMode
 from docker.models.containers import Container
+
+from dev_workspace_mcp.workspace.naming import WorkspaceMode
```

**Do not** hand-edit the import blocks, and **do not** run the bare
binary again to "check" the result — the whole defect is that the two
tools disagree. Re-verify with `uv run` only.

**A second, unrelated failure surfaced while re-verifying the requirement
over the whole repo.** `uv run ruff format --check .` also fails on
`docs/REMEDIATION_SPEC.md` and `docs/REMEDIATION_TODO.md` — the pinned
0.16.4 reformats a Python code sample embedded in both:

```diff
-    return                                   # <- silent success
+    return  # <- silent success
```

**Confirmed pre-existing, not a `05fe306` regression:**

```
$ git worktree add /tmp/wt f9ead84^ -q
$ (cd /tmp/wt && uv run ruff format --check .)
2 files would be reformatted, 148 files already formatted
```

`f9ead84^` predates all five file-splitting commits. Folded into this
task anyway, per `docs/GAP_SPEC.md` GR-001's own note on why: it blocks
GR-001's literal, whole-repo acceptance criterion, and the fix is a
two-line, zero-semantic-content whitespace collapse inside an
illustrative code sample — `uv run ruff format docs/REMEDIATION_SPEC.md
docs/REMEDIATION_TODO.md`, confirmed via `git diff` to touch only those
two lines in each file.

No test accompanies this task; GAT-001's acceptance criterion is the CI
gate itself, recorded in `docs/GAP_SPEC.md` as a deliberate choice rather
than an omission. The full suite SHALL still be run, since an import
reorder can in principle change import-time side effects.

**Verified.** `uv run ruff check .`: all checks passed. `uv run ruff
format --check .`: 164 files already formatted (was 2 files would be
reformatted). `uv run mypy src`: no issues in 60 source files.
`pytest --collect-only`: 1003 tests, identical to before. Full suite
(including the Docker-heavy `tests/workspace/`/`tests/git/` suites):
1003 passed in 536s, 0 Docker leftovers before and after.

---

## Phase 1 — Security

### G2 — `environment:` cannot supply interpreter or resolution control
**Satisfies:** GR-004 · **Verified by:** GAT-002
**Commit:** `PLACEHOLDER`

- [ ] `PATH`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `IFS`, `HOME`, `LD_*`
      rejected at validation
- [ ] The rejection names the offending key, matching the existing
      `shadowed` error's shape
- [ ] Allowlist-vs-denylist choice recorded with its residual risk
- [ ] Test both a rejected key and an ordinary key that still passes

**Defect (reproduced in live containers).**
`src/dev_workspace_mcp/project_config.py:128` rejects only
`SSH_AUTH_SOCK` and `MCP_*`:

```python
shadowed = sorted(k for k in v if k == "SSH_AUTH_SOCK" or k.startswith("MCP_"))
```

Everything else reaches the container, where `entrypoint.sh` runs as root,
PID 1, before the privilege drop, as `#!/bin/bash`, with **no `PATH=`
assignment anywhere** and unqualified calls to `id`, `git`, `getent`,
`useradd`, `mkdir`, `chown`, `chmod`, `setpriv`.

Observed, against a real daemon:

```
BASH_ENV=/workspace/pwn.sh   ->  PWNED-BASH_ENV uid=0
PATH=/workspace/bin:...      ->  PWNED-PATH uid=0
```

**The sharp consequence is the drop-bypass, not the code execution.**
`entrypoint.sh:52`:

```sh
if [ "$(id -u)" != "0" ]; then
    exec "$@"          # "not root — nothing to create"
fi
```

A fake `id` on the poisoned `PATH` printing anything but `0` takes this
branch, and the workspace command `exec`s **as uid 0 with no drop ever
performed**. Verified with `--security-opt no-new-privileges:true` and
`MCP_SUDO=false`: `uid=0 root`.

**Severity — state it exactly this way in the write-up, neither inflated
nor dismissed.** Under the default `container.sudo: true` the client
already has container root, so this grants nothing new. Under
`container.sudo: false` (supported, `config/models.py:132`; listed in §74)
it is a real escalation to container root that additionally nullifies
`no-new-privileges`, since no privilege transition occurs for that flag to
constrain. Contained within the container. **Not a host escape.**

This is scoped to the threat model `project_config.py:115-120` already
states in its own words ("effectively client-authorable"). It does not
depend on, and SHALL NOT be written up as depending on, any question about
whether repositories are trusted.

**Shape of the fix.** Prefer an allowlist — the current filter failed
*because* it was open by default, and a denylist repeats that shape. If
the configuration surface makes an allowlist impractical, a denylist is
acceptable, but the write-up SHALL say which was chosen, why, and what
remains open.

**Check while here:** whether daemon-level `docker.environment`
(`config/models.py`) reaches the same place. It is operator-authored, not
client-authored, so it is a different trust class and probably needs no
filter — but confirm rather than assume, and record the finding either
way.

### G3 — The entrypoint resolves its own commands
**Satisfies:** GR-005 · **Verified by:** GAT-003
**Commit:** `PLACEHOLDER`

- [ ] `entrypoint.sh` sets an explicit absolute `PATH` before any external
      command
- [ ] The privilege-drop decision cannot be influenced by inherited `PATH`
- [ ] Real-container test: hostile `PATH`, drop still occurs

**Defect.** Confirmed by grep: `docker/default/entrypoint.sh` contains no
`PATH=` assignment and no `export PATH` anywhere. Every command it runs
before the drop resolves through whatever it inherited.

**Shape of the fix.** One line near the top, before the first external
call:

```sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
```

This alone defeats the drop-bypass, which is why it is a separate task
from G2 rather than folded into it: G2 closes today's input path, G3
closes the mechanism regardless of what future surface supplies the
environment.

**Where the test goes.** `tests/workspace/test_default_image.py`, following
`test_at_vat004_*`'s own precedent for a real-container entrypoint test.
The test SHALL assert the workspace command reports a **non-zero uid** with
a hostile `PATH` present — asserting merely that the container starts
proves nothing, since the pre-fix behavior also starts (it just starts as
root).

**Revert-verification note.** With G2 already landed, a hostile `PATH` may
be rejected before reaching the container, which would make this test pass
for the wrong reason. Construct the fixture so it exercises the
*entrypoint*, not the validator — e.g. by passing the environment through
`run_kwargs` directly, the way `test_at_vat003_*` builds its hostile input
at the `container_config` layer rather than through project config.

---

## Phase 2 — Correctness

### G4 — A lossy capture cannot reach a strict parser
**Satisfies:** GR-006 · **Verified by:** GAT-004
**Commit:** `PLACEHOLDER`

- [ ] `run_git_in_container` guards `stdout_lossy` as it guards
      `stdout_truncated`
- [ ] The error is distinguishable from the truncation error
- [ ] Test a genuinely non-UTF-8 path through `git_status`/`git_log`
- [ ] Schema-drift check — this adds an error condition to three tools

**Defect.** AR-001 added `stdout_lossy` and guarded both `git_commit`
index sites. Repo-wide, `stdout_lossy` is consumed in exactly two places,
both in `tools/git_commit.py` (307, 356). `git/service.py:175`:

```python
if captured.stdout_truncated:
    raise CommandFailedError(...)
return GitCommandResult(returncode=captured.exit_code, stdout=captured.stdout, ...)
```

So `git_status`, `git_diff`, `git_log`, and `read_committed_files` all
receive U+FFFD-substituted paths silently.

**Severity — lower than AR-001, and SHALL be written up that way.** These
are read tools. None writes an index or builds an argv from the decoded
path, which is what made AR-001's sites destructive. The consequence is a
reported path that does not round-trip, with no signal it is degraded.
Misreporting, not data loss.

**Test fixture warning.** The fixture SHALL create a path that is
genuinely non-UTF-8 *on disk* (e.g. `os.fsdecode` of a raw byte sequence,
the approach AAT-001/AAT-003 already use), not a path containing a literal
U+FFFD character — `decode_utf8_lossy` distinguishes the two deliberately
(strict decode first), so a literal U+FFFD would not set the flag and the
test would pass against the unfixed code.

### G5 — Redaction output is always JSON-serializable
**Satisfies:** GR-007 · **Verified by:** GAT-005
**Commit:** `PLACEHOLDER`

- [ ] A `set` in `details`/`metadata` survives `json.dumps`
- [ ] A non-secret `bytes`/`bytearray` survives `json.dumps`
- [ ] Test through `to_tool_error` and the audit writer, not just `_redact`

**Defect (reproduced).**

```
to_tool_error({'tags': {'a','b'}})      -> TypeError: Object of type set is not JSON serializable
to_tool_error({'payload': b'\x00\x01'}) -> TypeError: Object of type bytes is not JSON serializable
to_tool_error({'t': (1,2)})             -> OK
```

`redact_secrets` feeds `json.dumps` at `errors.py:389` and `audit.py:83`.
A `TypeError` from the former escapes `translate_workspace_errors` (which
catches only `WorkspaceError`) as an unhandled crash; from the latter it
kills the audit write.

**Latent, not live — say so.** No caller constructs a `set` or raw `bytes`
in `details`/`metadata` today. This is a guard against a future caller,
and the write-up SHALL NOT imply a reachable crash exists now.

**Worth noting in the fix.** The in-code justification for the
`frozenset`/`memoryview`/namedtuple exclusions is "no caller constructs
one today" — the same reasoning that produced this. Here it produced a
*worse* outcome than the alternative: coercing `set` to `list` would have
been JSON-safe and lossless for redaction's purpose, whereas preserving
set-ness guarantees the crash. That asymmetry is the useful thing to
record; it is not an argument for reversing V5's tuple decision, which is
correct (tuples serialize as arrays).

`frozenset`, `memoryview`, and namedtuple degradation stay deliberate
exclusions and are **out of scope** — except for the comment correction in
G7.

---

## Phase 3 — Test, reference, and process integrity

### G6 — Close the two test gaps
**Satisfies:** GR-009 · **Verified by:** GAT-006, GAT-007
**Commit:** `PLACEHOLDER`

- [ ] Real-container test for the `.ssh` **directory** symlink refusal
      (`entrypoint.sh:209`)
- [ ] `isinstance` assertion on the `set` redaction branch

**Gap 1.** VR-004 required two refusals; `entrypoint.sh` implements both
(209 for the directory, 214 for the `config` file), but VAT-004
(`tests/workspace/test_default_image.py:232`) exercises only the file one.
The directory refusal has no test. Model the new one on `test_at_vat004_*`
directly.

**Gap 2.** `tests/test_errors.py:194` asserts value equality only, and
`{"[REDACTED]"} == frozenset({"[REDACTED]"})` is `True` — a
`set`→`frozenset` coercion would pass. The tuple test at `:198-204`
already does the right thing (`assert isinstance(..., tuple)`); match it.

Batching these two in one commit is deliberate: both are "a control
asserted by an earlier requirement has no test that can fail", they are
each a few lines, and splitting them adds ceremony without review value —
the same reasoning V7 and V8 used for their own batches.

### G7 — Correct references the splits invalidated
**Satisfies:** GR-008
**Commit:** `PLACEHOLDER`

- [ ] `src/dev_workspace_mcp/git/commit.py:107` — `tools/git.py` →
      `tools/git_commit.py`
- [ ] `docs/REVIEW_TODO.md:104,115,196`
- [ ] `docs/REMEDIATION_TODO.md:2008,2010,2012`
- [ ] `docs/AUDIT_TODO.md:412`
- [ ] `errors.py` exclusion comment — note that namedtuple field names are
      never used as key hints

The exact rows are tabulated in `docs/GAP_SPEC.md` GR-008. One is in
shipped source; the rest are documentation. Verified that the *other*
`tools/git.py` references in `src/` are still accurate (`audit_denials`,
`git_pull_tool`, `git_push_tool` genuinely remain in the hub), so the
source-side scope is exactly one line.

**Do not** run a mechanical path-rewriting pass over all six documents.
Most path references in `docs/HARDENING_TODO.md` and `docs/TODO.md` are
historical accounts of what a task touched at the time and are correct as
history; rewriting them to today's layout would destroy that. Only the
seven tabulated rows — all created by the five split commits, all in
passages describing *current* state — are in scope.

Per convention: a reference that was accurate when written gets a
correction note; a reference to a file that no longer exists is updated
outright, since there is nothing accurate left to preserve.

**Do not** widen `tests/test_acceptance_test_coverage.py` into a path
checker to catch this class. It is a coverage audit — "is this number
cited anywhere" — and that is the right scope. A link checker is a
different tool with different failure modes; if one is wanted, it is its
own task, not a mutation of this one.

### G8 — Close the tooling-drift hole, and correct the claims it produced
**Satisfies:** GR-002, GR-003
**Commit:** `PLACEHOLDER`

- [ ] A mechanism runs CI's three `uv run` commands before a push
- [ ] It is enforcing, not advisory-only
- [ ] `docs/DEVELOPMENT.md` points at it
- [ ] Correction recorded for the `mypy src` claim in `05fe306`,
      `fb194f9`, `fca1199`

**Defect.** `docs/DEVELOPMENT.md:13-15` already documents the right
commands. They were documented and not followed, and nothing caught it:
no `.pre-commit-config.yaml`, no non-sample hook in `.git/hooks/`. The
result was G1.

**The claim to correct.** Those three commit messages assert:

> `mypy src`: 59 errors both before and after … all pre-existing
> missing-stub/environment issues.

`uv run mypy src` — what CI runs — reports:

```
Success: no issues found in 60 source files
```

The 59 came from a bare `mypy` outside the project environment, where
`docker`/`yaml`/`mcp.server` have no stubs. The before/after comparison
was sound; presenting it as the project's type state was not, and it
understated a codebase that is actually clean.

Pushed commit messages SHALL NOT be rewritten. The correction goes in this
task's write-up, per the convention `REMEDIATION_TODO.md` and
`HARDENING_TODO.md` already use for superseded claims.

**Note on what this is.** This is the RR-033 failure mode — "re-run every
claim before writing it" — occurring while working on the document family
that established RR-033. Record it plainly. The useful lesson is not
"be more careful"; it is that a documented-but-unenforced convention is
indistinguishable from no convention, which is why the checkbox above
says *enforcing*.

### G9 — A test that asserts H31 currently verifies nothing
**Satisfies:** GR-010, GR-011 · **Verified by:** GAT-008
**Commit:** `PLACEHOLDER`

- [ ] `test_manager.py`'s H31 assertion targets a logger that covers the
      emitting modules
- [ ] `tests/test_cli.py` restores root logging state
- [ ] `tests/workspace/test_manager.py` passes **run on its own**
- [ ] Removing the `_logger.info` calls makes it fail **run on its own**

**Defect (reproduced).** `tests/workspace/test_manager.py:61`:

```python
with caplog.at_level("INFO", logger="dev_workspace_mcp.workspace.manager"):
```

`05fe306` moved the `"...created"`/`"...deleted"` emissions to
`manager_lifecycle.py:283,552`. `manager.py` now defines no logger at all;
`manager_core.py:38` and `manager_lifecycle.py:53` define their own.
`…manager_lifecycle` is a **sibling** of `…manager`, not a child, so
`at_level` never enables it and the records are dropped.

Measured:

| scope | result |
| --- | --- |
| `tests/workspace/test_manager.py` alone | **1 failed** |
| `tests/test_cli.py` + `tests/workspace/test_manager.py` | 40 passed |
| full suite (`tests/`) | 1003 passed |

**Why CI is green.** `configure_logging` (`logging_config.py:34`) does
`basicConfig(level=…, force=True)` on the **root** logger.
`tests/test_cli.py` drives that path (`cli.py:86`) inside the pytest
process and never restores it, so every later test inherits the level. In
collection order `tests/test_cli.py` sorts before `tests/workspace/`, so
CI always gets the passing order. `pytest-randomly` is not installed
(checked against `pyproject.toml` and `uv.lock`), so this is a permanent
property of the suite, not something that would eventually surface.

**Production impact is cosmetic — say so, do not inflate it.**
`logging_config.py` configures the root logger with no per-module
handlers, so the records still propagate and are still emitted; only
`%(name)s` changes from `…workspace.manager` to
`…workspace.manager_lifecycle`. H31's requirement still holds in
production. What broke is the test's ability to detect a regression.

**Shape of the fix.** Retarget to the common parent, which covers both
mixin modules:

```python
with caplog.at_level("INFO", logger="dev_workspace_mcp.workspace"):
```

Then close the leak so the fix is verifiable: restore the root logger's
level and handlers after the CLI tests — a fixture in `tests/test_cli.py`,
or an autouse snapshot/restore fixture in `tests/conftest.py`.

**Order matters within this task.** Close the leak **first or together**
with the retarget. Until it is closed, reverting the retarget still passes
in any run containing `tests/test_cli.py`, so the revert-verification step
cannot distinguish a working fix from a broken one — which is exactly how
this defect survived two full-suite runs.

**Do not** change `configure_logging` itself. `force=True` is correct for
a daemon entrypoint called once per process and its docstring explains
why; the defect is a test calling a process-global configuration function
without cleaning up.

**Check while here:** whether any other test asserts on log records (or
their absence) and is order-dependent for the same reason. `caplog` appears
in several suites; a quick audit costs little and this task is the natural
place for it. Record what was found either way.

---

## Definition of done

`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run mypy src` all pass at `HEAD`; a divergent local toolchain cannot
reach a push undetected; project-authored `environment:` cannot reach the
entrypoint's interpreter or command resolution, and the entrypoint does
not depend on inherited `PATH` even if it could; a lossy capture cannot
reach a strict parser through any call path; redaction output is always
JSON-serializable; every reference corrected here names a referent that
exists; `tests/workspace/test_manager.py` passes standalone and fails
standalone if the behavior it asserts is removed; no test leaves global
logging state altered for the tests that follow it; GAT-001..GAT-008 all
pass; the full suite passes with no Docker-dependent test skipped and no
foreign Docker state left behind; and no claim in this file was written
without being re-run against the code first.

**Carried forward from `docs/REVIEW_TODO.md`, and still binding:** if
working this list produces a finding about **this** document, it goes in a
correction paragraph here — not a sixth review layer. `docs/GAP_SPEC.md`'s
preamble records why this file exists at all despite that rule.
