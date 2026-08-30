# Gap TODO

**Spec:** `docs/GAP_SPEC.md` (GR-001..GR-011, GAT-001..GAT-008)
**Source:** code review of `docs/REVIEW_TODO.md` V1–V8 plus the five
file-splitting commits that followed `docs/AUDIT_TODO.md`, 2026-08-29,
against `fca1199`
**Status:** complete (G1-G9 done)

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

### G2 — `environment:` cannot supply interpreter or resolution control ✅
**Satisfies:** GR-004 · **Verified by:** GAT-002
**Commit:** `fdc81a1`

- [x] `PATH`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `IFS`, `HOME`, `LD_*`
      rejected at validation
- [x] The rejection names the offending key, matching the existing
      `shadowed` error's shape
- [x] Allowlist-vs-denylist choice recorded with its residual risk
- [x] Test both a rejected key and an ordinary key that still passes

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

**Implemented as described (chose a denylist).** A second
`@field_validator("environment")` on `ProjectWorkspaceConfig`
(`project_config.py`), alongside the existing HR-006 one rather than
merged into it — the two guard genuinely different things (daemon
identity vs. entrypoint bootstrap), and a bypass of one SHALL NOT
silently become a bypass of both, the same reasoning the existing
validator's own docstring already gives for keeping `container_config.py`'s
merge-order control separate from it. A new module-level
`_ENTRYPOINT_CONTROL_ENV_KEYS = frozenset({"PATH", "BASH_ENV", "ENV",
"SHELLOPTS", "IFS", "HOME"})`, checked alongside a `k.startswith("LD_")`
prefix match (the loader family is open-ended — `LD_PRELOAD`,
`LD_LIBRARY_PATH`, `LD_AUDIT`, ... — a fixed set would need updating for
every new one). The rejection message names the offending key(s), in the
same `sorted(...)` shape the existing check uses.

**Allowlist rejected, explicitly, not by default.** `environment:` exists
specifically so a project can set arbitrary, project-specific variables
(build flags, log levels, service URLs) — an allowlist of "safe" names
would defeat the field's own purpose, not just be inconvenient. **Residual
risk, recorded rather than left implicit:** a denylist only ever covers
what it names; a future interpreter-affecting variable not yet in this set
(or a shell feature not yet considered) would reach the entrypoint
unfiltered. G3 (below) is the independent, mechanism-level backstop for
exactly this — an entrypoint that sets its own `PATH` doesn't depend on
this denylist being exhaustive for the sharpest consequence (the
drop-bypass) to stay closed.

**Check while here — done, no gap found.** `docker.environment` does not
exist: grepped `config/models.py`'s `DockerConfig` and every other
daemon-level `*Config` class — none has an `environment` field at all.
The daemon's own `config.yml` has no environment-injection surface
whatsoever; `ProjectWorkspaceConfig.environment` (just fixed) is the only
one in the codebase.

**New tests, watched fail first.** `tests/test_project_config.py`:
`test_at_gr004_environment_cannot_set_an_interpreter_or_resolution_variable`,
parametrized over `PATH`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `IFS`, `HOME`,
`LD_PRELOAD`, `LD_LIBRARY_PATH` (8 cases — the full denylist, plus two
`LD_*` instances to exercise the prefix match, not just the exact-name
set). Pre-fix: all 8 raised `Failed: DID NOT RAISE
ContainerCreateFailedError`. `test_at_hat005_an_ordinary_environment_
variable_is_still_accepted` (pre-existing, `RUST_LOG`) is the "ordinary
key still passes" half the checklist asks for — confirmed it still
passes unmodified.

**Revert-verified.** Backed up `project_config.py`, restored `HEAD`'s
version, re-ran the 8 new cases: all failed identically to the pre-fix
run. Restored the fix; `tests/test_project_config.py`: 33 passed (25
pre-existing + 8 new).

**Suites.** `tests/test_project_config.py
tests/workspace/test_project_config_integration.py`: 47 passed.
`pytest --collect-only`: 1013 tests (1005 + 8 new). Lint/format clean;
`uv run mypy src`: no issues in 60 source files. No schema/contract
change — this is `.mcp-workspace.yml` validation, never a tool's own
JSON output; no `CONTRACT_VERSION` bump. Full unscoped `tests/`: 1013
passed, 0 failed, 532s, 0 Docker leftovers before and after.

### G3 — The entrypoint resolves its own commands ✅
**Satisfies:** GR-005 · **Verified by:** GAT-003
**Commit:** `c1668fe`

- [x] `entrypoint.sh` sets an explicit absolute `PATH` before any external
      command
- [x] The privilege-drop decision cannot be influenced by inherited `PATH`
- [x] Real-container test: hostile `PATH`, drop still occurs

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

**Implemented as described.** `docker/default/entrypoint.sh`, right after
`set -euo pipefail`, before any of the variable defaults or the first
external command (`id`, in the very next block):

```sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
```

**New test, built exactly per the revert-verification note.**
`tests/workspace/test_default_image.py`:
`test_at_gr005_a_hostile_path_does_not_skip_the_privilege_drop`, modeled
directly on `test_at_hat005_hostile_extra_env_cannot_make_pid_1_root`
(the sibling test proving HR-006's *entrypoint-side* backstop) — hostile
input built via `build_workspace_run_kwargs`'s `extra_env` (the layer
that still enforces nothing about `PATH`, unaffected by whether G2's
project-config validator is bypassed), not through project config. A
fake `id` (always printing `1000`) is bind-mounted into the container
and put first on a poisoned `PATH` supplied through `extra_env`.

One subtlety found while writing this, not anticipated by the task text:
asserting through the workspace command's *own* `id -u` call would be
worthless post-fix, since that command's `id` is *also* resolved via the
same poisoned `PATH` — a pass there wouldn't distinguish "the drop
happened and a genuinely non-root `id -u` ran" from "the fake `id`
printed `1000` regardless of anything." Switched to `bash -c 'echo
$EUID'` — a bash builtin, never resolved through `PATH` at all — so the
assertion can only reflect the *real* running uid.

**Revert-verified.** `entrypoint.sh` reverted to `HEAD`'s version
(`git show HEAD:... > entrypoint.sh`), new test re-run: failed with
`assert '0' == '1234'` — the drop-bypass, reproduced on demand. Restored
the fix; passes again.

**Suites.** `tests/workspace/test_default_image.py`: 12 passed.
`shellcheck docker/default/entrypoint.sh`: the same one pre-existing
`SC2034` warning (`GROUP_NAME`, line 82, untouched by this change) V4
already documented — no new warnings. `pytest --collect-only`: 1014
tests (1013 + 1 new). Lint/format clean; `uv run mypy src`: no issues in
60 source files. No schema/contract change — an entrypoint-script
change, never a tool's own JSON output; no `CONTRACT_VERSION` bump. Full
unscoped `tests/`: 1014 passed, 0 failed, 539s, 0 Docker leftovers
before and after.

---

## Phase 2 — Correctness

### G4 — A lossy capture cannot reach a strict parser ✅
**Satisfies:** GR-006 · **Verified by:** GAT-004
**Commit:** `ea4d4ad`

- [x] `run_git_in_container` guards `stdout_lossy` as it guards
      `stdout_truncated`
- [x] The error is distinguishable from the truncation error
- [x] Test a genuinely non-UTF-8 path through `git_status`/`git_log`
- [x] Schema-drift check — none: `schemas/tools.json` enumerates no
      error conditions (see step 6 above); confirmed empty
      `git diff --stat schemas/tools.json` after this change

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

**Implemented — a stricter version was tried first, and the full suite
correctly rejected it.** First pass: `run_git_in_container`
(`git/service.py`) gained a second guard, unconditionally, immediately
after the existing `stdout_truncated` one. Targeted suites (this file's
own new test, `tests/workspace/test_git_status_tool.py
tests/workspace/test_git_diff_log_tools.py tests/git/`: 199 passed) gave
no hint anything was wrong. The **full** suite did:
`test_at_aat001_a_non_utf8_path_present_only_pre_add_is_not_restored_as_a_
phantom` and `test_at_aat003_a_new_non_utf8_path_the_calls_own_add_
stages_is_left_honestly_dirty` (`tests/workspace/
test_git_commit_tool_failure_modes.py`) both failed — `git_commit`'s own
`assert '"index_left_dirty": true' in str(excinfo.value)` no longer held,
because the *unconditional* guard now also fired inside `git_commit`'s
own HR-023 precondition check (an ordinary `read_git_status` call, to
inspect `status.conflicted`/`status.branch`/`status.head_sha` — nothing
path-related), for *any* repository containing an unrelated non-UTF-8
file anywhere. `git status`'s own porcelain output lists the whole
repository, not just what a given caller cares about, so a caller that
never intended to report paths to the client — and both AAT-001/AAT-003's
own fixtures create exactly such a file, for an unrelated reason — was
newly, and wrongly, blocked outright.

**Corrected: `run_git_in_container` gained a `strict_utf8: bool = True`
parameter**, guarding the raise (`if strict_utf8 and captured.stdout_lossy:
raise CommandFailedError(...)`, message/`details` as originally
described — distinguishable from the truncation error by content, not a
second `ErrorCode`, matching this codebase's existing convention). Every
call site was audited (`grep -rn "run_git_in_container(" src/`, 9 total)
and set explicitly, not left to infer:

| call site | reports paths to client? | `strict_utf8` |
| --- | --- | --- |
| `git/status.py` `read_git_status` (→ `git_status` tool) | yes | `True` (default) |
| `git/status.py` `read_git_status` (→ `git_commit`'s 2 precondition calls) | no — only `conflicted`/`branch`/`head_sha` | `False`, explicit |
| `git/diff.py` `read_git_diff` (→ `git_diff` tool) | yes, sole caller | `True` (default) |
| `git/log.py` `read_git_log` (→ `git_log` tool) | yes, sole caller | `True` (default) |
| `git/commit.py` `read_committed_files` (→ `files_committed`) | yes — GR-006's own explicit scope | `True` (default) |
| `git/commit.py` `is_head_published` | no — only `.ok`, no path ever parsed | `False`, explicit |
| `git/remote.py` `resolve_remote_url`/`resolve_remote_push_url` (×2) | no — a remote URL, not a path | `False`, explicit |
| `git/push.py` `build_force_with_lease_arg` | no — a rev-parse'd SHA | `False`, explicit |

`read_git_status` itself needed the parameter threaded through it (not
just `run_git_in_container`), since it is the one function genuinely
shared between a strict caller (the `git_status` tool) and two
non-strict ones (`git_commit`'s own preconditions) — a single default
could not serve both.

**New test, watched fail first.**
`tests/workspace/test_git_status_tool.py`:
`test_at_gr006_a_non_utf8_path_raises_rather_than_reports_a_mangled_one`
— `os.fsdecode(b"bad\xffname.txt")`, exactly the fixture warning's own
example, an untracked file in the repository root, then `git_status`.
Picked `git_status` over `git_log` (the checklist's "/" — either) since
it is the more direct case: the bad path reaches stdout via `git status`'s
own porcelain output with no commit required first.

**Revert-verified, twice.** First, the corrected fix against the exact
regression that found it: `tests/workspace/
test_git_commit_tool_failure_modes.py -k "aat001 or aat003"` — 2 passed
(both were failing under the first-pass, unconditional guard). Second,
the full defect: backed up all six changed `src/` files, restored
`HEAD`'s versions, re-ran the new test: failed with `Failed: DID NOT
RAISE ToolError` — `git_status` completed successfully, silently
returning the mangled path (the exact pre-fix defect). Restored the fix;
passes again.

**Suites.** `tests/workspace/test_git_status_tool.py
tests/workspace/test_git_diff_log_tools.py tests/git/
tests/workspace/test_git_commit_tool_failure_modes.py
tests/workspace/test_git_commit_tool.py tests/workspace/test_git_push_tool.py
tests/workspace/test_git_pull_tool.py`: 258 passed. `pytest
--collect-only`: 1015 tests (1014 + 1 new — unchanged by the correction,
since it only reshaped an existing guard's scope). Lint/format clean;
`uv run mypy src`: no issues in 60 source files. No schema/contract
change — confirmed empty `git diff --stat schemas/tools.json`; a new
runtime error condition is not represented in that file at all (it
carries only `input_schema`/`output_schema`, no error enumeration), so
no `CONTRACT_VERSION` bump, matching G1's own earlier finding about this
file's actual contents. Full unscoped `tests/`: 1015 passed, 0 failed,
531s, 0 Docker leftovers before and after.

**Correction (X19/X21, `docs/GAP2_TODO.md`), superseding two claims
above.** "Every one of the 9 call sites … was audited and set
explicitly" was wrong on both counts:

- There are **eleven** `run_git_in_container` call sites, not nine —
  `git_state.py:76`, `tools/git_pull.py:264`, and `tools/git_push.py:168`
  were never opened by this task (confirmed: all three already existed
  at `ea4d4ad^`, and `ea4d4ad`'s own diff never touches their files).
- Of the eight sites this task's own table *did* list, four were left
  at the implicit `strict_utf8=True` default rather than set
  explicitly: `git/diff.py`, `git/log.py`, `git/commit.py`'s
  `read_committed_files`, and — the one with a real behavioral
  consequence — `tools/git_pull.py`'s two `read_git_status` calls and
  `tools/git_push.py`'s one, which inherited the strict default this
  task's own fix was written to move *away* from for exactly this
  shape of call (consuming only `branch`/`head_sha`, never a path).
  That gap shipped as a real regression, closed separately by X6
  (`docs/GAP2_TODO.md`) rather than here, since it is a behavior change
  and this paragraph is a documentation-only correction.

This paragraph corrects the *count and the "set explicitly" claim*
only. `git_pull`'s two sites and `git_push`'s one now carry an explicit
`strict_utf8=False`, closed by X6 (`docs/GAP2_TODO.md`) as a behavior
fix, not a documentation change. The remaining gaps this correction
surfaced — `git/diff.py`, `git/log.py`, and `git/commit.py`'s
`read_committed_files` left at the implicit default rather than set
explicitly, and `git_state.py:76`'s undocumented degradation
(`workspace_info`'s `git.clean` silently becoming `null` for a
repository containing a non-UTF-8 path) never decided at all — are
closed separately by X21 (`docs/GAP2_TODO.md`), which is where all
eleven sites are made explicit; not yet done as of this correction.

### G5 — Redaction output is always JSON-serializable ✅
**Satisfies:** GR-007 · **Verified by:** GAT-005
**Commit:** `bb919a5`

- [x] A `set` in `details`/`metadata` survives `json.dumps`
- [x] A non-secret `bytes`/`bytearray` survives `json.dumps`
- [x] Test through `to_tool_error` and the audit writer, not just `_redact`

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

**Implemented as described.** `_redact` (`errors.py`): `set` now coerces
to `list` (`[_redact(v, ...) for v in value]`) instead of a `set`
comprehension — the exact asymmetry-fix the spec text called out, chosen
over `tuple`'s type-preservation specifically because `tuple` is
JSON-safe (an array) and `set` is not. Non-secret `bytes`/`bytearray`
now decode with `errors="replace"` (`bytes(value).decode("utf-8",
errors="replace")`) instead of returning the raw object — the same
lossy-but-safe tradeoff `sanitize_for_client` (`git/service.py`) already
uses at a client-facing boundary — computed once and reused for both the
`_looks_like_secret` check and the return value, rather than decoding
twice.

**G7's own comment-correction folded in here, not deferred**, since it
sits in the exact lines this task was already rewriting: the `tuple`/
`namedtuple` comment block now explicitly notes that a namedtuple's
field names are never consulted as key hints either (not just that the
type degrades to a plain `tuple`). G7's own checklist item for this is
satisfied by this commit — recorded there as "see G5" rather than
duplicated.

**Existing tests corrected, not silently rewritten — each explains
why.** Three pre-existing tests encoded the old (buggy) behavior as
their expected result, not merely "accurate when written, now
describes obsolete history" — the assertion itself was wrong per this
task's own requirement, so each was corrected in place with a docstring
explaining the change, matching how AR-002/V5 corrected prior tests
that encoded a defect:
`test_at_vat005_a_secret_flagged_set_recurses` (expected a `set` back;
now expects a `list`), `test_at_vat005_a_non_secret_bytes_value_is_left_
untouched` → renamed `..._is_decoded_not_left_as_bytes` (expected raw
`bytes` back; now expects the decoded `str`), and
`test_at_aat002_a_non_secret_bytearray_value_is_left_untouched` →
renamed `..._is_decoded_not_left_as_bytearray` (same, for `bytearray`) —
found only by running the full touched-file suite, not anticipated by
this task's own checklist.

**New tests, watched fail first.** `tests/test_errors.py`:
`test_at_gr007_a_non_utf8_non_secret_bytes_value_decodes_lossily_not_
left_as_bytes` (the genuinely-non-UTF-8 case `errors="replace"` exists
for) and `test_at_gr007_to_tool_error_does_not_crash_on_a_set_or_bytes_
value` (through `to_tool_error`, per the checklist). `tests/test_audit.py`:
`test_at_gr007_write_audit_event_does_not_crash_on_a_set_or_bytes_value`
(through the audit writer, per the checklist) — its own docstring notes
the pre-fix failure mode specifically: a `TypeError` from `json.dumps`
isn't an `OSError`, so it wasn't caught by `write_audit_event`'s own
`except OSError` and the event was lost entirely, not just logged to
stderr.

**Revert-verified.** Backed up `errors.py`, restored `HEAD`'s version,
re-ran all 6 new/corrected tests together: all 6 failed with `TypeError:
Object of type set/bytes is not JSON serializable` or an `AssertionError`
against the old expected value — confirmed, not assumed. Restored the
fix; `tests/test_errors.py tests/test_audit.py`: 75 passed.

**Suites.** `tests/ --ignore=tests/workspace --ignore=tests/git` (the
full non-Docker suite, `errors.py` being widely used): 451 passed.
`pytest --collect-only`: 1018 tests (1015 + 3 new). Lint/format clean;
`uv run mypy src`: no issues in 60 source files. No schema/contract
change — internal redaction, never a tool's own JSON output; no
`CONTRACT_VERSION` bump. Full unscoped `tests/`: 1018 passed, 0 failed,
522s, 0 Docker leftovers before and after.

---

## Phase 3 — Test, reference, and process integrity

### G6 — Close the two test gaps ✅
**Satisfies:** GR-009 · **Verified by:** GAT-006, GAT-007
**Commit:** `89a0236`

- [x] Real-container test for the `.ssh` **directory** symlink refusal
      (`entrypoint.sh:237` — X20 (`docs/GAP2_TODO.md`): was `:209`, re-verified against `HEAD`)
- [x] `isinstance` assertion on the `set` redaction branch

**Gap 1.** VR-004 required two refusals; `entrypoint.sh` implements both
(237 for the directory, 242 for the `config` file — X20 (`docs/GAP2_TODO.md`): were 209/214, re-verified against `HEAD`), but VAT-004
(`tests/workspace/test_default_image.py:326`) exercises only the file one.
The directory refusal has no test. Model the new one on `test_at_vat004_*`
directly.

**Gap 2.** `tests/test_errors.py:195` <!-- X20: was `:194` --> asserts value equality only, and
`{"[REDACTED]"} == frozenset({"[REDACTED]"})` is `True` — a
`set`→`frozenset` coercion would pass. The tuple test at `:198-204`
already does the right thing (`assert isinstance(..., tuple)`); match it.

Batching these two in one commit is deliberate: both are "a control
asserted by an earlier requirement has no test that can fail", they are
each a few lines, and splitting them adds ceremony without review value —
the same reasoning V7 and V8 used for their own batches.

**Implemented as described, both.**

**Gap 2** (`tests/test_errors.py`): `test_at_vat005_a_secret_flagged_
set_recurses` now also asserts `isinstance(redacted["secret"], list)`
— G5 already made the fix return `list`, so the equality check alone was
already type-safe in practice (`["x"] != {"x"}` / `!= frozenset({"x"})`
in Python, unlike the old `set == frozenset` masking VR-009 named), but
the task's own literal checklist item asks for the explicit assertion,
matching `test_at_vat005_tuple_recursion_preserves_the_container_type`'s
own shape immediately above it.

**Gap 1** (`tests/workspace/test_default_image.py`):
`test_at_gr009_a_symlinked_ssh_directory_planted_in_a_cache_volume_is_not_
followed`, modeled on `test_at_vat004_*` directly, with one structural
difference the checklist's own "model it on VAT-004" instruction didn't
anticipate: `.ssh` *itself* cannot be replaced with a symlink through a
cache volume mounted **at** `.ssh` (`cache_paths=[".ssh"]`, VAT-004's own
setup) — a Docker volume mount always creates a real directory at its
own mount point, so the mount point can never itself be a symlink.
`cache_paths=["."]` — `.` normalizes to `$HOME` itself, per
`entrypoint.sh`'s own docstring at that line — mounts a persistent,
container-writable volume at `$HOME` *containing* `.ssh` as an ordinary
subdirectory, which an unprivileged run genuinely can replace
(`rm -rf ~/.ssh && ln -s /etc ~/.ssh`).

**Revert-verified — and found a sharper finding than the checklist item
alone implied.** Removed *only* the directory-level refusal
(`entrypoint.sh:237`, the `config`-file refusal at :242 left intact — X20 (`docs/GAP2_TODO.md`): were :209/:214, re-verified against `HEAD`),
re-ran the new test: `Failed: DID NOT RAISE ContainerError` — the
container started cleanly. Traced why: with `.ssh` a symlink to `/etc`
and no refusal, `mkdir -p "$HOME_DIR/.ssh"` follows the symlink and
succeeds (the target already exists as a directory); the `config`-file
check then tests `/etc/config` — which doesn't exist as a symlink, it
doesn't exist at all yet — so *that* check also passes cleanly, and the
entrypoint proceeds to **write straight into `/etc/config` through the
followed symlink**, silently. The file-level refusal alone provides no
protection at all once the directory itself is redirected — confirming,
concretely rather than by inference, VR-004's own precedent that neither
control is sufficient alone. Restored the fix; passes again.

**Suites.** `tests/test_errors.py tests/workspace/test_default_image.py`:
80 passed. `pytest --collect-only`: 1019 tests (1018 + 1 new).
Lint/format clean; `uv run mypy src`: no issues in 60 source files.
Full unscoped `tests/`: 1019 passed, 0 failed, 529s, 0 Docker leftovers
before and after.

### G7 — Correct references the splits invalidated ✅
**Satisfies:** GR-008
**Commit:** `e09ad82`

- [x] `src/dev_workspace_mcp/git/commit.py:107` — `tools/git.py` →
      `tools/git_commit.py`
- [x] `docs/REVIEW_TODO.md:104,115,196`
- [x] `docs/REMEDIATION_TODO.md:2008,2010,2012`
- [x] `docs/AUDIT_TODO.md:412`
- [x] `errors.py` exclusion comment — already done in G5 (touched the
      exact same lines while implementing that fix); recorded here, not
      duplicated

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

**Implemented as described, all five items — plus one adjacent finding
GR-008's own audit had missed, corrected in the same table it sits in.**

- `git/commit.py:107` (shipped source): fixed in place, not annotated —
  a docstring explaining current architecture to today's reader, not a
  historical record. `tools/git.py`'s `capture_index` → `tools/
  git_commit.py`'s.
- `docs/REVIEW_TODO.md` (narrative "what V1 did" — accurate when
  written): one consolidated **Correction (G7, ...)** paragraph appended
  after V1's own "Suites." line, covering both `capture_index`'s module
  and the VAT-001 test's file (`f9ead84`/`7ce029a`); a second, separate
  correction appended after V2's "Suites." line for the `test_service.py`
  mentions (`fb194f9`/`7a3f38a`) — two paragraphs, not one, since they
  correct two different tasks' write-ups.
- `docs/REMEDIATION_TODO.md` (a `Property | Test | Location` **lookup
  table**, not narrative — closer to "describes current state" than "what
  a task did"): the `Location` column updated outright for all affected
  rows, plus a correction note explaining the choice. **Found two more
  stale rows here than GR-008 tabulated** — `test_rr005_*`/`test_rr004_*`
  had also moved (`test_git_commit_tool.py` →
  `test_git_commit_tool_failure_modes.py`), in the exact same table as
  the three tabulated rows. Fixed all five together rather than leaving
  two behind in a table I was already correcting; recorded as an
  explicit finding beyond this task's own tabulated scope, not silently
  folded in as if GR-008 had already counted it.
- `docs/AUDIT_TODO.md` (narrative "what A4's own verification steps
  did," including a `git show HEAD:… >`/restore sequence against the
  real file at the time): corrected with a note, not rewritten — the
  file genuinely existed and was genuinely edited/restored as described;
  only its later, unrelated split makes the name look wrong to a reader
  today.
- `errors.py`: see G5's own commit — folded in there since it touched
  the identical lines.

**Verified rather than assumed** that no *other* `tools/git.py`
reference in `src/` needed the same fix: `grep -n "tools/git\.py"
src/dev_workspace_mcp/git/commit.py` after the edit shows exactly one
remaining hit (the module docstring's "the execution service `tools/
git.py` shares with `exec`") — accurate as written, since it describes
shared execution infrastructure at the hub/subsystem level, not a
specific function's current location.

No test accompanies this task — every change is a comment or a
documentation correction, no behavior changed. Verified anyway, per
this list's own step 3: `tests/git/test_commit.py
tests/workspace/test_git_commit_tool.py
tests/workspace/test_git_commit_tool_failure_modes.py`: 52 passed.
`pytest --collect-only`: 1019 tests, unchanged from G6 (as expected — no
test added or removed). Lint/format clean; `uv run mypy src`: no issues
in 60 source files. Full unscoped `tests/`: 1019 passed, 0 failed, 531s,
0 Docker leftovers before and after.

### G8 — Close the tooling-drift hole, and correct the claims it produced ✅
**Satisfies:** GR-002, GR-003
**Commit:** `11c4084`

- [x] A mechanism runs CI's three `uv run` commands before a push
- [x] It is enforcing, not advisory-only
- [x] `docs/DEVELOPMENT.md` points at it
- [x] Correction recorded for the `mypy src` claim in `05fe306`,
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

**Implemented — a `pre-commit` git hook, not `pre-push`.** The checklist
above says "before a push"; `docs/DEVELOPMENT.md` itself already said
"All four SHALL pass before a **commit**" (unchanged by this task), and
`docs/GAP_SPEC.md` GR-002's own suggested mechanism is explicitly "a
`pre-commit` hook." Went with the earlier, already-documented enforcement
point rather than the checklist's looser wording — catching a violation
at `git commit` is strictly earlier than at `git push` (this session
alone pushes right after every commit), and it matches what
`DEVELOPMENT.md` already told contributors to expect.

`.githooks/pre-commit` (new): runs `uv run ruff format --check .`, `uv
run ruff check .`, `uv run mypy src` — exactly G8's own "three `uv run`
commands," deliberately excluding `pytest`: the full suite is
Docker-heavy and multi-minute, too slow for every commit, and neither
the checklist nor GR-002 asked for it here. Exits nonzero, with a clear
message naming which check failed and how to deliberately bypass it
(`git commit --no-verify`, git's own standard escape hatch — no hook can
close that, and none should try to).

**Why `.githooks/`, not `.git/hooks/` directly.** `.git/hooks/` is
per-clone, never version-controlled — writing there would help only this
one checkout. `.githooks/pre-commit` is a tracked, ordinary file;
`git config core.hooksPath .githooks` (documented in `docs/
DEVELOPMENT.md`'s own Setup section, right after `uv sync --group dev`)
points git at it. One-time per clone, not automatic — there is no way to
make git run something automatically on every fresh clone without the
clone owner's own consent, by design.

**`docs/DEVELOPMENT.md` updated**, not just told to point at the hook:
explains what activates it, why it's a one-time step, and explicitly
tells a reader to always invoke these three commands through `uv run`,
citing G1 as the concrete cost of not doing that.

**Revert-verified against a real `git commit` attempt, not a simulation
— and caught my own mistake mid-verification.** First attempt appended a
plain-text line to `docs/DEVELOPMENT.md` as the "violation": the hook
correctly ran and the commit correctly succeeded — which, on reflection,
proved nothing, since a markdown text line trips neither `ruff` nor
`mypy` at all. Caught this, discarded that commit (`git reset --soft
HEAD~1`, then restored the file to `HEAD`), and re-verified with a
genuine violation instead: an unused, out-of-order `import sys` appended
to `errors.py`. `git commit` (not a dry run — the actual command a
contributor would type) was correctly blocked:

```
pre-commit: uv run ruff format --check .
1 file would be reformatted, 164 files already formatted
pre-commit: uv run ruff check .
E402 Module level import not at top of file
F401 `sys` imported but unused
pre-commit: one or more checks failed — commit blocked (docs/GAP_SPEC.md GR-002).
```

Nothing was committed (`git log` unchanged). Restored `errors.py` to
`HEAD` exactly (`git restore --source=HEAD --staged --worktree`,
confirmed zero diff); a clean commit of the real change set then
succeeds through the hook.

**Correction recorded for the `mypy src` claim** — GR-003
(`docs/GAP_SPEC.md`) and this task's own "The claim to correct" section
above *are* that correction, per the convention `REMEDIATION_TODO.md`/
`HARDENING_TODO.md` use for superseded claims elsewhere; nothing further
needed here beyond what already exists in this document.

No pytest test accompanies this task — the hook is a shell script, not
Python, and its correctness was verified the way G1 (the other pure
tooling task) was: direct reproduction via a real `git commit`, not a
simulation. `shellcheck .githooks/pre-commit`: clean, no warnings.
`pytest --collect-only`: 1019 tests, unchanged (as expected — no Python
behavior changed; the transient `import sys` violation used for
verification was never committed and is confirmed fully reverted).
Lint/format clean; `uv run mypy src`: no issues in 60 source files. Full
unscoped `tests/`: 1019 passed, 0 failed, 530s, 0 Docker leftovers before
and after — run anyway per this session's own standing discipline,
despite the near-zero likelihood a shell-script/docs-only change affects
it; confirmed it doesn't.

### G9 — A test that asserts H31 currently verifies nothing ✅
**Satisfies:** GR-010, GR-011 · **Verified by:** GAT-008
**Commit:** `7485af2`

- [x] `test_manager.py`'s H31 assertion targets a logger that covers the
      emitting modules
- [x] `tests/conftest.py` restores root logging state (autouse, for
      every test — not scoped to `tests/test_cli.py` alone; see below)
- [x] `tests/workspace/test_manager.py` passes **run on its own**
- [x] Removing the `_logger.info` calls makes it fail **run on its own**

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

**Order matters within this task, but not for the reason originally
written here — corrected after implementing both fixes and checking
each in isolation.** The original text claimed reverting the retarget
would still pass in any run containing `tests/test_cli.py`, so revert-
verification couldn't distinguish a working fix from a broken one unless
the leak was closed first. Tested directly: `caplog.at_level(level,
logger=name)` sets `getLogger(name)`'s own explicit level (not just the
capturing handler's), so retargeting to `dev_workspace_mcp.workspace` —
a genuine ancestor of both `manager_core.py`/`manager_lifecycle.py` —
makes `test_manager.py` pass **standalone**, with the leak fixture
disabled entirely (verified: reverting *only* the fixture's restoration,
leaving the retarget in place, the standalone test still passes).
Reverting *only* the retarget, with the leak fixture fully active, fails
standalone as expected. **The two fixes are independent of each other
for this specific test.**

Where "order matters" is real: revert-verifying the retarget by running
it in a context that happens to include `tests/test_cli.py` (rather than
standalone) is unreliable regardless of fix order, because the leak
independently produces a false pass for the *original, wrong* logger name
too (reproduced: reverted retarget + `tests/test_cli.py` prefixed → 10
passed, no failure). GAT-008's own "run on its own" clause is what
actually protects revert-verification here — not fix ordering. The leak
fix is still required by GR-011 on its own terms: it is a real, general
defect (a test calling a process-global configuration function without
cleaning up) independent of whether it happens to matter for *this*
test's revert-verification.

**Do not** change `configure_logging` itself. `force=True` is correct for
a daemon entrypoint called once per process and its docstring explains
why; the defect is a test calling a process-global configuration function
without cleaning up.

**Check while here — done.** Audited every `caplog` user in the suite
(`tests/execution/test_service.py`, `tests/jobs/test_reconcile.py`,
`tests/workspace/test_async_runner.py`, `tests/workspace/
test_git_push_tool.py`, `tests/workspace/test_ssh_agent.py`, plus
`test_manager.py` itself). All five others already name their own
correct emitting logger exactly (not a sibling), so none share
`test_manager.py`'s specific defect. All six benefit from the leak fix
regardless, since it is implemented as a global autouse fixture rather
than scoped to `tests/test_cli.py` alone — chosen over the narrower
option specifically because of this audit: it closes the defect class
for every current and future `caplog`-based test, not just the one
instance found.

**Implemented as described**, plus one addition beyond the checklist: a
dedicated `pytester`-based regression test
(`tests/test_logging_isolation.py`, modeled on the real, separate-
subprocess pattern `tests/test_skip_policy.py` already established for
proving cross-test-boundary properties) that exercises the real
`_restore_root_logging_state` fixture from `tests/conftest.py` — not a
copy of its logic — against a synthetic leaky test, proving level *and*
handler restoration directly, plus a negative control proving the same
scenario leaks without the fixture. Also discovered while writing it:
pytest's own logging plugin already has handlers attached to root before
any test runs (`_LiveLoggingNullHandler`, a file handler, two
`LogCaptureHandler`s) — `configure_logging`'s `force=True` therefore also
wipes pytest's *own* capturing infrastructure, not merely cosmetic
level/format state, making the fixture's handler-list restoration (not
just the level) load-bearing.

**Revert-verified, four ways:**
1. Isolation fixture neutralized (restoration replaced with a bare
   `yield`) → `test_logging_isolation.py`'s positive test fails with
   `assert 50 != 50` (root stuck at the synthetic `CRITICAL` level) —
   restored, passes again.
2. Retarget reverted (wrong sibling name), leak fixture active →
   `test_manager.py -k h031` fails standalone with the original
   `assert False` — restored, passes again.
3. Retarget reverted, **and** run with `tests/test_cli.py` prefixed →
   still passes (10 passed) — the false-positive this task exists to
   close, reproduced on demand.
4. Both fixes active, but the two `_logger.info` calls (`"...created"`/
   `"...deleted"`) removed from `manager_lifecycle.py` entirely
   (simulating a genuine H31 regression) → `test_manager.py -k h031`
   fails standalone — restored, passes again. This is GAT-008's second
   half: the fixed test can actually catch the regression H31 exists to
   prevent, which the original (broken) version could not.

**Suites.** `tests/test_cli.py tests/workspace/test_manager.py`
(the order that used to mask the defect): 40 passed.
`tests/execution/test_service.py tests/jobs/test_reconcile.py
tests/workspace/test_async_runner.py tests/workspace/test_ssh_agent.py`
(the other `caplog` users unaffected by the new autouse fixture): 34
passed. `tests/test_logging_isolation.py`: 2 passed.
`pytest --collect-only`: 1005 tests (1003 + the 2 new isolation tests).
Full unscoped `tests/`: 1005 passed, 0 failed, 537s, 0 Docker leftovers
before and after. Lint/format clean; `uv run mypy src`: no issues in 60
source files.

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
