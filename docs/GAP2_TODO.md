# Gap 2 TODO

**Spec:** `docs/GAP2_SPEC.md` (G2R-001..G2R-022, G2AT-001..G2AT-016)
**Source:** code review of `docs/GAP_TODO.md` G1–G9 plus a general review
of the four largest source modules, 2026-08-30, against `9bc44fd`
**Status:** not started

Twenty-two tasks. One is a live privilege-drop bypass reproduced against
the shipped image; two are regressions the `GAP_TODO.md` pass itself
introduced; two more are security controls that stop short of what their
own requirement claims. The rest are correctness, tooling, and integrity
defects.

**G1–G9 were verified and are not re-opened here.** All nine are
genuinely implemented, committed one per commit in the recommended
order, and the suite is green (1019 passed, verified on a clean run).
Where a task below corrects one of them (X1 closes what G2 left open, X6
finishes what G4 started, X7/X8 fix what G8 broke and missed), it says so
explicitly rather than implying the earlier work was absent.

## How to work this list

Unchanged from `docs/GAP_TODO.md`, with two additions at steps 3 and 9
that this list exists partly because of:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.**
3. Run the new test alone, then the relevant suite, then the full suite —
   **with no concurrent Docker activity.** X22 exists because a
   full-suite run alongside other containers produced 11 spurious
   failures during the review that produced this list; the identical
   suite run cleanly immediately afterwards passed 1019. Until X22
   lands, check `docker ps` before starting a suite run and treat any
   `tests/workspace/` failure during concurrent activity as unproven,
   not as a defect.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> >
   <file>`, confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck — **using `uv run`, never a bare binary**:
   ```
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   ```
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION`
   per §33.2. **X13, X14, and X17 are the candidates** — each changes a
   tool's error or output surface. Run the drift check on every task
   regardless rather than reasoning from this paragraph.
7. One task per commit. No `Co-Authored-By` trailers — and note that
   **the hook that enforces this is currently disabled in this clone**
   until X7 lands (G2R-007); check the message by eye until then.
8. **Re-run every claim before writing it in the task's write-up**
   (RR-033).
9. **A claim about a control SHALL be produced by exercising that
   control, not by reading it.** G2's denylist was verified by reading
   its own field list; the bypass in X1 was found by trying an input the
   list did not name. Where a task closes a class of input, the write-up
   SHALL record what was tried and rejected, not only what was blocked.

**Ordering.** Task numbers are stable identifiers, not an execution
order. Recommended order: **X7, then X1/X2/X3, then X6**, then the rest.

X7 first: it is trivial, and until it lands the `Co-Authored-By` hook
this list's own step 7 depends on is inert — every commit for every task
below is exposed to it. X1/X2/X3 next, in descending consequence: X1 is
the only live, reproduced security bypass in this document. X6 next
because it is a user-visible regression currently shipping. X4/X5 are
independent security work; X9–X18 are independent of one another and of
everything above, any order. X19–X22 are integrity and tooling work that
is most useful once the code beneath it has stopped moving.

**X1, X2, and X3 are complementary, not duplicates.** X1 and X2 close two
specific inputs; X3 closes the mechanism that makes any missed input
dangerous. GR-005's own "neither control is sufficient alone" reasoning
is the precedent, and G2R-001 is the concrete demonstration that it was
right: G2 closed the inputs it named and G3 closed `PATH`, and a bypass
survived both. Land them as separate commits but do not stop after one.

---

## Phase 0 — Security: controls that do not hold

### X1 — `environment:` cannot supply an exported shell function
**Satisfies:** G2R-001 · **Verified by:** G2AT-001, G2AT-003

- [ ] A key that is not a valid POSIX environment-variable name is
      rejected at validation
- [ ] The rejection names the offending key, matching the existing
      `blocked`/`shadowed` error shape
- [ ] Real-container test: an exported `BASH_FUNC_id` function present in
      the environment, privilege drop still occurs
- [ ] The residual-risk paragraph at `project_config.py:162-170` is
      corrected — it names G3 as the backstop for exactly this, and G3
      does not cover it

**Defect (reproduced against the shipped image).**
`src/dev_workspace_mcp/project_config.py:46` denies six exact names plus
an `LD_` prefix. An exported bash function is none of them, and a shell
function **takes precedence over `PATH` lookup**, so `entrypoint.sh:43-44`'s
hardcoded `PATH` does not help either:

```
CONTROL (no BASH_FUNC):                           REAL_EUID=1234
ATTACK  (-e 'BASH_FUNC_id%%=() { echo 1234; }'):  REAL_EUID=0
```

`entrypoint.sh:68`'s `if [ "$(id -u)" != "0" ]` resolves `id` to the
imported function, takes the "not root" branch, and `exec "$@"` runs the
workspace command as uid 0 with no drop performed.

Chain verified end to end: validation accepts the key;
`tools/workspace.py:405` copies it into `extra_env`;
`container_config.py:147-150` strips only `_DAEMON_CONTROLLED_ENV_KEYS`
(`:57-68`), which does not include it.

**Severity — state it exactly this way, neither inflated nor dismissed.**
Under the default `container.sudo: true` the client already has container
root, so this grants nothing new. Under `container.sudo: false` it is a
real escalation that additionally nullifies `no-new-privileges`, since no
privilege transition occurs for that flag to constrain. Contained within
the container. **Not a host escape.** This is the same framing GR-004
used for the same class of finding.

**Shape of the fix — close the shape, not the spelling.** `BASH_FUNC_` is
bash's *current* prefix; pre-Shellshock bash used a bare name with a
`() {` value, so a single hardcoded prefix comparison is a denylist of
one spelling. Reject any key that is not `^[A-Za-z_][A-Za-z0-9_]*$`. That
is an allowlist of *shape* rather than of names, so it does not defeat
`environment:`'s purpose the way an allowlist of permitted names would
(the reason G2 gave for choosing a denylist, which still stands).

**Do not** merge this into the existing `_ENTRYPOINT_CONTROL_ENV_KEYS`
check. That set names variables whose *meaning* is dangerous; this
rejects keys that are not variables at all. Keep them separate for the
same reason G2 kept its check separate from HR-006's.

**Where the test goes.** Validation half in `tests/test_project_config.py`
alongside `test_at_gr004_*`. Container half in
`tests/workspace/test_default_image.py`, modeled on
`test_at_gr005_a_hostile_path_does_not_skip_the_privilege_drop`: build
the hostile input via `build_workspace_run_kwargs`'s `extra_env` so it
exercises the *entrypoint* rather than this task's own validator, and
assert with `bash -c 'echo $EUID'` — never `id -u`, which is the exact
thing the attack shadows.

### X2 — `environment:` cannot redirect Git's own configuration
**Satisfies:** G2R-002 · **Verified by:** G2AT-002

- [ ] `GIT_CONFIG`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_GLOBAL`,
      `GIT_CONFIG_COUNT` rejected at validation
- [ ] The broader `GIT_*` assessment recorded in code, not re-derived
- [ ] Test a rejected key and an ordinary `GIT_*` key that still passes

**Defect (reproduced with git 2.43.0).** `entrypoint.sh:64` runs, as root
before the drop:

```sh
git config --system url."git@github.com:".insteadOf "https://github.com/"
```

`GIT_CONFIG_SYSTEM` is accepted by the validator and redirects that write
to an attacker-chosen path:

```
$ GIT_CONFIG_SYSTEM=/tmp/fakesys.cfg git config --system url."git@github.com:".insteadOf "https://github.com/"
$ cat /tmp/fakesys.cfg
[url "git@github.com:"]
	insteadOf = https://github.com/
```

Content is fixed, so **not** direct code execution — arbitrary root-owned
file creation, and gitconfig syntax appended into an existing root-owned
file can corrupt it. Say it that way; do not inflate it to code
execution.

**Record the negative result too, in code.** The review assessed the rest
of the `GIT_*` family and found no privilege gain: the root-phase git
invocation is only `config --system` — no repository, no diff, no
transport — so `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_EXTERNAL_DIFF`,
`GIT_DIR`, and `GIT_ALTERNATE_OBJECT_DIRECTORIES` have nothing to act on,
and post-drop git runs as the identity the client already controls. That
assessment is only sound *while the entrypoint's root phase stays this
narrow*, so it belongs in a comment next to the check, where a future
entrypoint change that adds a root-phase `git` call against a real
repository will be read against it.

### X3 — The entrypoint does not inherit interpreter state it cannot neutralize
**Satisfies:** G2R-003 · **Verified by:** G2AT-003

- [ ] The root phase does not execute under a shell that has already
      imported unvalidated environment state
- [ ] The write-up states which inherited-state classes the chosen
      mechanism does and does not cover
- [ ] GR-005's "closes the mechanism regardless of what future surface
      supplies the environment" is corrected — it is true of `PATH` only

**Defect.** GR-005 set `PATH` *inside* the script. `BASH_ENV` is sourced
by bash **before line 1 executes**, and an exported function is imported
at the same moment, so no assignment within the script can defend against
either. X1/X2 close today's only reachable route (the daemon populates
`extra_env` solely from `project_config.environment`), but GR-005 exists
precisely so a future surface cannot reopen what a denylist misses — and
as written it provides that for `PATH` alone.

**Shape of the fix.** Re-exec through a sanitized environment as the
script's first action — an `ENTRYPOINT` that
`exec env -i PATH=… /entrypoint.sh "$@"`, or a `#!/bin/sh` shebang where
the runtime shell does not import functions from the environment.

**Verify the shell, do not assume it.** Ubuntu's `/bin/sh` is `dash`,
which does not import environment functions — but the image is
configurable and could change. If the `sh` route is chosen, the test
SHALL assert the property (a function in the environment is *not*
imported), not the shebang.

**Revert-verification note.** With X1 landed, a hostile function may be
rejected before reaching the container, which would make this test pass
for the wrong reason. Build the fixture at the `extra_env`/`run_kwargs`
layer, exactly as `test_at_gr005_*` does, so it exercises the entrypoint
and not X1's validator.

### X4 — Redaction never returns a secret it was asked to redact
**Satisfies:** G2R-004 · **Verified by:** G2AT-004

- [ ] A secret-flagged `frozenset` is redacted
- [ ] A secret-flagged `memoryview` is redacted
- [ ] The comment at `errors.py:324-333` records the redaction-bypass
      consequence, not only the serialization one
- [ ] Decide and record: recurse into remaining container types, or fail
      closed on unrecognized ones

**Defect (reproduced).** `_redact` has no branch for `frozenset` or
`memoryview`, so both reach the terminal `return value` at
`errors.py:378` **regardless of `key_flags_secret`**:

```
set   : {'secret': ['[REDACTED]']}
frozen: {'secret': frozenset({'hunter2'})}     <-- leaked
list  : {'secret': ['[REDACTED]']}
tuple : {'secret': ('[REDACTED]',)}
```

**Latent, not live — say so.** No caller constructs either type in
`details`/`metadata` today, exactly as GR-007's own defect was latent.
But a leak is strictly worse than the crash GR-007 fixed, and the
carve-out that produced it was reasoned entirely about serializability.
That asymmetry is the useful thing to record: the exclusion was justified
against one consequence and shipped with a second, worse one.

**In scope, if the fail-closed option is chosen.** `redact_secrets` also
raises `AttributeError` on a non-`str` dict key (`errors.py:295`,
`key.lower()`) — a contract violation nothing enforces at either
`json.dumps` call site. Fail-closed closes it; recursion alone does not.

**Do not** attempt GR-007's literal "every value SHALL be
JSON-serializable" here. That sentence contradicts its own carve-out two
paragraphs later; reconciling it is a correction to `docs/GAP_SPEC.md`
(see X21), not a code change in this task.

### X5 — A post-open re-check enforces every rule its pre-open resolve enforced
**Satisfies:** G2R-005 · **Verified by:** G2AT-005

- [ ] `verify_fd_within_root` applies `resolve()`'s full rule set
- [ ] `write_file`'s inline `resolve_open_fd`/`is_within_root` sequence
      does the same
- [ ] Test the in-tree `.git` redirect under the default
      `git_dir_mode: read-write`

**Defect.** `resolve()` enforces three rules — external-git-dir,
containment, and `_reject_in_tree_git_metadata`.
`filesystem/security.py:290-306` re-checks **containment only**;
`filesystem/service.py:439-445` has the same shape.

Under the shipped default `git_dir_mode: "read-write"`, `.git` is *inside*
the repository root, so a container process that wins the documented race
between `resolve()` and the open — the module's own docstring records
reproducing it "on the very first race iteration" — by replacing the
target with a symlink to `.git/config` yields an fd whose real path is
inside root. Containment passes; §12.1's "`.git` is never readable via
the filesystem tools" is bypassed. The `write_file` variant redirects a
write into `.git/hooks/pre-commit`, a code-execution primitive.

Under `git_dir_mode: "none"`/`"read-only"` with an external git dir,
containment alone catches it — the gap is specific to the in-tree
default, which is the default.

**Test note.** Drive the race deterministically through the same
injection point the existing race tests use rather than looping and
hoping; a loop-based test is slow and flaky and this codebase already
established the deterministic pattern.

---

## Phase 1 — Regressions introduced by the `GAP_TODO.md` pass

### X6 — `git_pull` and `git_push` work on repositories containing non-UTF-8 paths
**Satisfies:** G2R-006 · **Verified by:** G2AT-006

- [ ] `tools/git_pull.py:102` passes `strict_utf8=False`
- [ ] `tools/git_pull.py:249` passes `strict_utf8=False`
- [ ] `tools/git_push.py:147` passes `strict_utf8=False`
- [ ] Each carries the same in-code justification `git_commit`'s two
      sites carry
- [ ] Real-container test through both tools

**Defect.** G4 found that an unconditional `stdout_lossy` guard wrongly
blocked callers inspecting only `branch`/`head_sha`, and fixed it by
threading `strict_utf8` through `read_git_status` — for `git_commit`'s
two calls, and no others:

| call site | `strict_utf8` | fields actually consumed |
| --- | --- | --- |
| `tools/git_commit.py:141` | `False`, explicit | `conflicted`/`branch`/`head_sha` |
| `tools/git_commit.py:394` | `False`, explicit | `head_sha` |
| `tools/git_pull.py:102` | **inherits `True`** | `branch`, `head_sha` only |
| `tools/git_pull.py:249` | **inherits `True`** | `head_sha` only |
| `tools/git_push.py:147` | **inherits `True`** | `branch`, `head_sha` only |

`git status --porcelain` lists the whole repository, so **`git_pull` and
`git_push` currently fail outright on any repository containing a single
unrelated non-UTF-8 filename** — the exact failure mode G4 diagnosed,
corrected for one tool and left in two others. The consumed-fields column
was verified by reading each call's downstream use; none touches a path.

**Why the suite did not catch it.** Non-UTF-8 fixtures exist only in
`test_git_status_tool.py` and `test_git_commit_tool_failure_modes.py` —
never in `test_git_pull_tool.py`/`test_git_push_tool.py`. The test half
of this task is the part that matters; the fix itself is three
arguments.

**This is a correction to G4, not a new requirement.** Record it in G4's
write-up per the convention this family uses for superseded claims —
alongside X19's correction of the same task's call-site count, which is a
separate error in the same audit.

### X7 — Activating this repository's hooks does not disable others
**Satisfies:** G2R-007 · **Verified by:** G2AT-007

- [ ] A `Co-Authored-By:` trailer is refused again in this clone
- [ ] The other four shadowed global hooks are reachable, or their loss
      is documented as deliberate
- [ ] `docs/DEVELOPMENT.md` explains the shadowing

**Defect (verified in this clone).** `core.hooksPath` is a single
directory, not a search chain:

```
file:~/.gitconfig    core.hookspath=/home/phil/.config/git/hooks
file:.git/config     core.hookspath=.githooks
```

`~/.config/git/hooks/` holds `commit-msg`, `post-checkout`,
`post-commit`, `post-merge`, `pre-push`; `.githooks/` holds `pre-commit`
alone. **Five hooks are inert**, including the `commit-msg` hook that
refuses a `Co-Authored-By:` trailer — the hook this list's own step 7,
and `docs/GAP_TODO.md`'s before it, depend on. Confirmed by running it
directly (exits 1 with its refusal) and confirming git cannot reach it
(`.githooks/commit-msg` does not exist).

G8 satisfied GR-002 and disabled an unrelated, load-bearing control as an
undocumented side effect. That is the finding; G8's own mechanism is
correct and is not being reverted.

**Shape of the fix.** A dispatcher is preferred over copying: copies go
stale, and the global directory is the developer's, not this
repository's. `.githooks/<hook>` that execs the same hook under the
*previously configured* `core.hooksPath` when one exists covers all five
without this repository owning their content.

**Do not** satisfy this with documentation alone. GR-002's own finding is
that advisory documentation is what already failed; a hook this list
depends on being silently inert is the same failure with a different
subject.

**Verification note.** Verify with a real `git commit` attempt carrying a
trailer, not by reading the dispatcher — G8's own write-up records
catching a vacuous verification (a markdown line that tripped neither
tool) and redoing it against a genuine violation. Same discipline here.

### X8 — The pre-commit gate verifies what is being committed
**Satisfies:** G2R-008 · **Verified by:** G2AT-008

- [ ] The three checks run against the index, not the working tree
- [ ] A clean commit still succeeds with unrelated dirty files present
- [ ] The stash/restore cannot lose work on failure or interrupt

**Defect.** `.githooks/pre-commit` runs `ruff format --check .`,
`ruff check .`, `mypy src` — all against the working tree. A commit
records the index. Two divergences:

- *False negative, the dangerous one.* Stage a file that fails a check,
  fix it unstaged. The hook passes; the broken version commits and
  reaches CI red — the G1 failure mode by another route, through the
  hook built to prevent it.
- *False positive.* An unrelated dirty or untracked `.py` blocks every
  commit until cleaned. `ruff check .` walks untracked files too.

**Shape of the fix.** `git stash --keep-index --include-untracked` around
the checks with a restoring `trap`. **The trap is the load-bearing
part**: a hook that stashes and dies without restoring loses the
developer's uncommitted work, which is far worse than the bug being
fixed. Test the interrupt path explicitly, not just the pass and fail
paths.

---

## Phase 2 — Correctness defects in shipped code

### X9 — Filesystem listing survives concurrent mutation
**Satisfies:** G2R-009 · **Verified by:** G2AT-009

- [ ] `_describe`'s `stat`/`is_symlink`/`exists`/`resolve` calls tolerate
      a vanished entry
- [ ] A vanished entry is skipped or reported, never propagated untyped
- [ ] The local `stat` at `:175` no longer shadows the `stat` module

**Defect.** `filesystem/service.py:175` — `stat = path.stat()`,
unguarded. `_is_git_metadata` directly above (`:150-156`) *was* hardened
against exactly this class; `_describe` was not. `FileNotFoundError` is
not a `WorkspaceError`, and `translate_workspace_errors`
(`errors.py:432`) catches only `WorkspaceError`, so it escapes as an
unstructured crash.

Trigger: `list_files("", recursive=True)` while a container process
deletes an entry between `start.rglob("*")` enumeration and `_describe`
— reachable with `exec_start "rm -rf build/"` against the same
bind-mounted checkout. `:160-170` shares the exposure.

The `stat` shadowing is harmless today and one added line inside
`_describe` from being a `TypeError`; fix it here since the lines are
already being touched.

### X10 — `write_file` neither destroys content on failure nor reports an unverified hash
**Satisfies:** G2R-010 · **Verified by:** G2AT-010

- [ ] A write that fails partway leaves previous content intact
- [ ] The reported `sha256`/`size` describe what is on disk
- [ ] A short `pwrite` is looped or raises
- [ ] `dir_fd` containment is preserved by whatever replaces the current
      sequence

**Defect.** `filesystem/service.py:469-470`:

```python
os.ftruncate(fd, 0)
os.pwrite(fd, data, 0)
```

- **Data loss.** Target exists, disk full: `ftruncate` succeeds, `pwrite`
  raises `ENOSPC`, and the cleanup at `:471` unlinks only when
  `not existed_before` — so the pre-existing file is left **empty** and
  the `OSError` escapes untyped, leaving the client unable to determine
  the resulting state.
- **Unverified result.** `os.pwrite`'s return is discarded; `WriteResult`
  (`:506`) reports `sha256_of(data)`/`len(data)` from the intended
  buffer, never from disk. A short write returns a hash no `read_file`
  reproduces, poisoning the `expected_sha256` protocol §22.2 depends on.

**Shape of the fix.** Write to a temporary file in the same directory and
`os.replace` it over the target. That makes the replacement atomic,
leaves the original untouched on failure, and — because both live in the
same directory — preserves the `dir_fd` containment the current code
correctly uses.

**Preserve what is already right.** The cleanup at `:471-502` unlinks
`actual.name` relative to a `dir_fd` on `actual.parent`, not `path`, with
a recorded note that unlinking `path` was tried and empirically missed
files a race had moved. Whatever replaces this sequence keeps that
property; do not regress it while fixing the truncation.

### X11 — Opportunistic background work cannot fail the request that triggered it
**Satisfies:** G2R-011 · **Verified by:** G2AT-011

- [ ] `sweep_idle_workspaces` is best-effort per workspace
- [ ] A failure to stop one workspace does not abort the sweep
- [ ] `prune_job_logs` gets the same treatment
- [ ] The triggering request still succeeds and is still audited

**Defect.** `sweep_idle_workspaces` calls `self.stop(record.id)` with no
exception handling; `stop()` raises `ContainerRuntimeFailedError` on any
`DockerException`. It runs from `tools/workspace.py:501` — after
`ensure_workspace` succeeded, before the `workspace_opened` audit event
(`:507`) and the return.

So: a client opens workspace A successfully, an *unrelated* idle
workspace B fails to stop, and `workspace_open("A")` returns
`CONTAINER_RUNTIME_FAILED` though A is running and healthy — A's audit
record is never written, and the remaining idle workspaces are never
swept.

**The codebase already argues this case, elsewhere.** `delete()`'s RR-012
comment (`manager_lifecycle.py:530-547`) spends fifteen lines
establishing that post-completion cleanup "SHALL never be a reason to
report this call itself as failed." The sweep is exactly that shape and
was not held to it. Cite that comment in the fix rather than
re-deriving the reasoning.

**Recorded, not fixed here:** both `sweep_idle_workspaces` and
`prune_job_logs` are documented substitutes for a scheduler that does not
exist, so retention never runs on an idle server and idle-stop never
fires unless someone opens a workspace. Building the scheduler is a
design change, not this task; this task only stops the substitute from
failing its host request.

### X12 — Setup steps run whenever a container is newly provisioned
**Satisfies:** G2R-012 · **Verified by:** G2AT-012

- [ ] Setup re-runs after a failed run
- [ ] Setup runs when a container is recreated behind an existing row
- [ ] Completion is recorded on the workspace, not inferred

**Defect.** `tools/workspace.py:451-456` computes `was_created` from
**database row existence** before `ensure_workspace`; `:484` runs setup
only `if was_created`. Row and container have independent lifetimes:

- *Failure path.* First `workspace_open` for a repo with `setup:`. Row
  inserted, container created, a step fails transiently → retry sees the
  row, `was_created` is `False`, **setup is skipped permanently**. The
  workspace reports healthy and lacks its toolchain.
- *Recreate path.* Row exists, container removed out of band
  (`docker system prune`). `ensure_workspace`'s HR-017 branch
  (`manager_lifecycle.py:114-116`) builds a genuinely new, empty
  container — and setup does not run.

`run_setup_steps`' own docstring records fixing this exact bug for
`cli.py`'s `reset`/`rebuild`. The prune-recovery path was missed, which
is the useful detail: the same defect was closed once at one call site
and left at another.

**Shape of the fix.** Record setup completion on the workspace row and
drive the decision from whether a *container* was created, not a row.
Both triggers close together; a fix that only re-runs on failure leaves
the prune path open.

### X13 — `workspace_open` translates Docker image errors
**Satisfies:** G2R-013 · **Verified by:** G2AT-013

- [ ] `ImageNotFound` becomes a typed, translated error naming the image
- [ ] Schema-drift check — a new error condition may or may not reach
      `schemas/tools.json`; G4 established it does not, but re-run it

**Defect.** `tools/workspace.py:476` —
`docker_client.images.get(effective_image).id`, no `try`.
`docker.errors.ImageNotFound` is not a `WorkspaceError` and escapes
untyped, unlike every other Docker call in the tool layer, which HR-022
deliberately wrapped.

Trigger: a persistent workspace is RUNNING; the operator edits
`.mcp-workspace.yml` to an image absent locally. `reconcile()` returns
RUNNING without touching Docker images, so nothing pulls it and
`images.get` raises.

### X14 — A workspace's reported image and digest describe the same image
**Satisfies:** G2R-014 · **Verified by:** G2AT-014

- [ ] `image_digest` is the digest of the image the container actually
      runs
- [ ] A configured-image change without a rebuild is reported, not
      silently reconciled
- [ ] Schema-drift check — this one plausibly does change an output shape

**Defect.** `tools/workspace.py:476-482` writes the digest of
`effective_image` into the workspace row **unconditionally**, but
`ensure_workspace` never rebuilds a running container when the configured
image changes. With the new image present locally the container still
runs the old one, while `WorkspaceInfoOutput.image` reports
`record.image` (old) and `image_digest` reports the new image's digest.
§9.2 calls this pair the reproducibility record; it describes two
different images.

**Note the adjacent finding while here.** This write is one of two direct
SQL `UPDATE`s the tool layer performs against tables
`DockerWorkspaceManager` otherwise owns (the other is
`store_remote_protection` at `:470`). The invariant "who may write
`workspace` rows" is currently unstated and violated. Moving the write
behind the manager is in scope if it falls out of the fix; a broader
ownership refactor is not.

### X15 — Deleting a workspace reclaims its job logs
**Satisfies:** G2R-015 · **Verified by:** G2AT-015

- [ ] A deleted workspace's `<state_dir>/jobs/<job_id>/` directories are
      removed, or retention enumerates the directory rather than the
      table
- [ ] The choice is recorded
- [ ] `job_log_max_total_bytes` accounting reflects reality afterwards

**Defect.** `manager_lifecycle.py:551-552` deletes the workspace's `job`
rows. Nothing deletes the log files. `prune_job_logs`
(`jobs/retention.py:41-43`) enumerates candidates from `job_store` — rows
that no longer exist — so the files are permanently unreachable by
retention.

Trigger: `exec_start` a job producing 500 MB of output, let it finish,
delete the workspace. The 500 MB persists indefinitely *and* is invisible
to the size cap, so the cap under-counts and prunes still-tracked jobs
instead of the orphans.

### X16 — The setup-step timeout path handles kill failures as `run_exec` does
**Satisfies:** G2R-016 · **Verified by:** G2AT-016

- [ ] A kill failure during setup timeout returns a typed error
- [ ] The two timeout-and-kill blocks no longer diverge
- [ ] The two grace-period constants are reconciled

**Defect.** `tools/workspace.py:199-206` kills a timed-out setup step
with no exception handling. `ExecutionService.kill`
(`execution/service.py:330-370`) *deliberately* propagates
`PermissionError` (HR-020) and `DockerException` from `exec_inspect`
(RR-013) — its docstring is explicit. `tools/execution.py:419-428`
handles precisely this for `run_exec`; the setup copy does not.

Trigger: a setup step exceeds its timeout and the container was removed
out of band → `exec_inspect` raises → escapes untyped.

**Shape of the fix — extract, do not copy the handler.** The two blocks
also carry separate `KILL_GRACE_PERIOD_SECONDS` and
`_SETUP_KILL_GRACE_PERIOD_SECONDS` constants that `workspace.py:81-84`
admits must be kept in sync by hand, and which have already diverged.
Copying the `except` clause fixes today's asymmetry and preserves the
mechanism that produced it.

**Worth confirming, not assuming:** `docker.errors.APIError` *is* an
`OSError` (`APIError → HTTPError → RequestException → OSError`), so
`run_exec`'s narrower `except OSError` does cover the common Docker
cases. A bare non-API `DockerException` would still escape `run_exec`
too. Decide whether the extracted version widens the catch, and say
which.

### X17 — `exec_output` rejects an inverted byte range
**Satisfies:** G2R-017

- [ ] `byte_end < byte_start` is an invalid-argument error
- [ ] Schema-drift check

**Defect.** `tools/execution.py:627-634`. With `byte_start=100,
byte_end=50`: `requested = -50` → `capped = max(min(-50, limit), 0) = 0`
→ one byte read → `truncated = 1 > 0` is `True`, `text` empty. The client
receives empty content plus `truncated: true`, inviting an infinite
paging loop. The adjacent validation at `:568-576` checks negatives but
never the ordering.

### X18 — `list_files` bounds its work by the requested limit
**Satisfies:** G2R-018

- [ ] Enumeration stops at the limit
- [ ] Sorted output is preserved if it is a contract
- [ ] The bound is asserted by instrumentation, not wall-clock

**Defect.** `filesystem/service.py:114`:

```python
candidates = sorted(start.rglob("*") if recursive else start.iterdir())
```

The whole tree is walked and sorted before `if len(entries) >= limit:
break` at `:116` can apply, so the break is decorative for cost purposes.
`list_files("", recursive=True, max_entries=1)` on a repository with
`node_modules/` walks and sorts hundreds of thousands of paths to return
one entry. Client-controllable; nothing bounds it.

**Test note.** Assert the bound by counting work (a patched iterator, a
counter) — never by wall-clock, which makes the test a flake on a loaded
machine. Checked and *not* a defect: Python 3.12's `rglob` does not
recurse into symlinked directories, so `a/b -> ../a` terminates; do not
"fix" that.

---

## Phase 3 — Test, tooling, and reference integrity

### X19 — Complete and correct G4's call-site audit
**Satisfies:** G2R-021

- [ ] All eleven `run_git_in_container` sites carry an explicit
      `strict_utf8` with justification
- [ ] `git_state.py:76`'s silent degradation is decided deliberately
- [ ] The correction is recorded in `docs/GAP_TODO.md`'s G4 write-up

**Defect.** G4 tabulates nine call sites and states "Every one … was
audited and set explicitly". There are **eleven** — verified: the three
unlisted ones already existed at `ea4d4ad^`, and `ea4d4ad` never opened
their files — and four of the eight tabulated sites are left at the
implicit default, not set explicitly. The unaudited three:

| site | consequence of the strict default |
| --- | --- |
| `git_state.py:76` | `workspace_info`'s `git.clean` silently becomes `null` for any repository containing a non-UTF-8 path (absorbed at `:86-87`) |
| `tools/git_pull.py:264` | none — `merge-base --is-ancestor` stdout is always empty |
| `tools/git_push.py:168` | none — stdout is a SHA |

Two are harmless; recording them as explicit is the point, since the
claim was that they already were. `git_state.py:76` is a real behaviour
change nobody chose — decide it, comment it.

**Land this with or after X6.** Both are corrections to G4; keeping them
adjacent keeps the write-up coherent. Separate commits, since X6 changes
behaviour and this changes arguments and prose.

### X20 — Every acceptance-test series is audited
**Satisfies:** G2R-019

- [ ] `AAT` registered in `_SERIES`
- [ ] `GAT` registered in `_SERIES`
- [ ] Uncited numbers recorded in `known_uncited` with the reason
- [ ] The existing meta-test still proves the audit can fail

**Defect.** `tests/test_acceptance_test_coverage.py:71-99` registers
`AT`, `HAT`, `RAT`, `VAT`. `docs/AUDIT_SPEC.md`'s `AAT-001..004` and
`docs/GAP_SPEC.md`'s `GAT-001..008` are unregistered, though
`GAP_SPEC.md:545` uses the identical "Each SHALL be an automated test"
language the audit's own docstring cites as its justification for
covering the other three. Twelve acceptance tests drift with nothing to
catch a deletion or renumber.

Measured: `GAT` has **zero** citations under `tests/` — the GAP tests are
named after the requirement (`test_at_gr004_*`), not the acceptance test.
`AAT-001`/`002`/`003` are cited; `AAT-004` is not.

**Do not back-fill citations.** Requirement-id naming has precedent here
(12 `test_at_hr###`, 2 `test_at_rr###`) and is not itself a defect.
Record the uncited numbers in `known_uncited` with the reason, exactly as
`HAT`/`RAT`'s pre-existing gaps already are — the file's own docstring
explains why that is the honest option and why shrinking the allowlist
must be deliberate.

**Note the pleasing detail** while writing this up: `AAT-004` is itself
"Removing every citation of one `VAT` SHALL make the coverage audit
fail" — the audit's own series was the one it forgot to audit.

### X21 — Correct the stale and self-contradictory references
**Satisfies:** G2R-020 *(the `pyproject.toml` half)* · plus the
`docs/GAP_SPEC.md` corrections

- [ ] `pyproject.toml` declares `docker` third-party (or sets `src`)
- [ ] Import order regenerated with the pinned tool
- [ ] `docs/GAP_SPEC.md` carries the four corrections from
      `docs/GAP2_SPEC.md`'s "Corrections" section
- [ ] Stale line citations corrected

**The `pyproject.toml` half.** The repository has a top-level `docker/`
directory and sets neither `known-first-party` nor `src` for ruff, so
ruff classifies bare `import docker` as **first-party** (the local
directory) while `from docker.errors import …` stays third-party — one
distribution split across two import sections. That ambiguity is what let
two ruff versions disagree and produce G1; `manager_core.py:20-25` still
carries the resulting shape. G1 pinned the tool without removing the
ambiguity, so any tool with different source detection reopens it.

**The prose half** — four corrections, all to `docs/GAP_SPEC.md`, per
that document's own rule that findings about it go in correction
paragraphs there rather than a new layer:

1. **GAT-007 contradicts GR-007** in the same document: GAT-007 requires
   `_redact` on a set to "return a `set`, asserted by `isinstance`";
   GR-007 endorses coercing `set` to `list`, which G5 implemented,
   making a returned `set` the defect. The test correctly asserts
   `list`.
2. **GAT-006 is unsatisfiable as written** — a Docker volume mount always
   creates a real directory at its mount point, so the mount point can
   never be a symlink. G6's test mounts at `$HOME` with
   `cache_paths=["."]` and genuinely exercises the directory refusal.
3. **GR-007 contradicts itself** — "Every value … SHALL be
   JSON-serializable" versus a carve-out two paragraphs later. State
   which governs. The measured residue is not three named types but
   every type outside the handled set.
4. **Stale citations:** `GAP_SPEC.md:321` cites `errors.py:389`, now
   `:412`; GR-008's table cites `tools/git_commit.py:255` for
   `capture_index`, now `:260`; GR-009 cites `entrypoint.sh:209`/`:214`,
   now `:225`/`:230` (moved by G3's own PATH block) — and
   `docs/GAP_TODO.md:619,623,665` plus
   `tests/workspace/test_default_image.py:355` carry the same stale
   pair. Separately, one row G7 did not touch in
   `docs/REMEDIATION_TODO.md` keeps a wrong line: `test_rr016_…` is at
   `tests/git/test_diff.py:382`, not `:364`.

**Do not** run a mechanical path- or line-rewriting pass. Most citations
in `docs/HARDENING_TODO.md` and `docs/TODO.md` are historical accounts,
correct as history; only the rows above, all describing *current* state,
are in scope. This is G7's own rule and it still applies.

### X22 — Concurrent suite runs do not silently collide
**Satisfies:** G2R-022

- [ ] Test workspace names are namespaced per run, or the collision is
      documented in `docs/DEVELOPMENT.md`
- [ ] The failure mode is named, so it is not diagnosed as flakiness
- [ ] Not satisfied by a retry

**Defect.** Workspace container names are deterministic
(`mcp-dev-ws-<repo>-<hash>`). Two concurrent runs of `tests/workspace/`,
or one run alongside a live daemon serving a repository of the same id,
collide: the second run's fixture finds a container already at its name
and takes `manager_lifecycle.py:246-260`'s adoption early-return, so
lifecycle assertions fail. `tests/workspace/conftest.py`'s RR-024/HR-026
tracking prevents destroying *other* processes' resources but does
nothing about this.

**Observed twice independently while producing this list.** A full-suite
run alongside concurrent Docker activity produced 11 failures and 2
errors, all in `tests/workspace/`, all `workspace_open`-related; the
identical suite run cleanly immediately afterwards produced **1019
passed**. A second reviewer hit the same collision and lost five test
results to it before tracing the cause.

The failure mode is a silent false *failure* — expensive to diagnose,
and it reads as flakiness, which is how a real defect eventually gets
waved through. **A retry would make it worse**, not better: it would
convert a loud wrong answer into a slow one and hide the collision
entirely.

---

## Definition of done

`environment:` cannot supply an exported shell function or redirect Git's
configuration, and the entrypoint does not execute its root phase under a
shell that has already imported unvalidated interpreter state; redaction
never returns a value it was asked to redact; a post-open re-check
enforces every rule its pre-open resolve enforced; `git_pull` and
`git_push` work on repositories containing non-UTF-8 paths; activating
this repository's hooks does not disable hooks configured elsewhere, and
the hook verifies the index; a listing survives concurrent mutation; a
failed write neither destroys existing content nor reports a hash that
was never on disk; opportunistic background work cannot fail its
triggering request; setup steps run whenever a container is newly
provisioned; every acceptance-test series is audited; import
classification is unambiguous; G4's call-site audit is complete and its
overstatement corrected; `docs/GAP_SPEC.md` carries its four corrections;
G2AT-001..G2AT-016 all pass; the full suite passes with no
Docker-dependent test skipped and no foreign Docker state left behind,
**run with no concurrent Docker activity**; and no claim in this file was
written without being re-run against the code first.

**Carried forward from `docs/GAP_TODO.md`, and still binding:** if
working this list produces a finding about **this** document, it goes in
a correction paragraph here — not an eighth review layer.
`docs/GAP2_SPEC.md`'s preamble records why this file exists at all
despite that rule, and routes its own three prose-shaped findings into
`docs/GAP_SPEC.md` corrections rather than requirements here.
