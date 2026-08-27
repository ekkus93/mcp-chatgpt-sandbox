# Development Workspace MCP Server — Hardening TODO

**Source of truth:** `docs/HARDENING_SPEC.md` (HR/HAT references point into it),
which in turn defers to `docs/SPEC.md` for everything it does not address.
**Status:** not started
**Origin:** security and correctness review of 2026-08-27 against `214c095`
**Convention:** tasks are `H<n>`. Each names the hardening requirements it
implements and the acceptance tests that prove it. One task per commit.

A task is done when its subtasks are checked, its acceptance tests pass, the
full suite passes, and `schemas/tools.json` has been re-exported if the task
changed a tool contract (§33.2 bump rules still apply).

---

## Decisions

Made 2026-08-27, before work started.

- [x] **HD1 — Host-side Git reads are retained and hardened in place.**
      Rather than moving `git_status`/`git_diff`/`git_log` into the container.
      Preserves §20.1's speed exception and §73's degraded start, at the cost of
      an ongoing control surface that must be maintained (`HARDENING_SPEC.md`
      §4.1). The alternative — eliminating host-side execution entirely — is
      recorded as **H36**, deferred, not rejected.
- [x] **HD2 — `docs/SPEC.md` is amended, not merely superseded.** Three of the
      six critical defects exist because the specification itself is wrong.
      `HARDENING_SPEC.md` §9 lists sixteen amendments; **H34** applies them.
- [x] **HD3 — Unsafe defaults are changed outright.** Pre-1.0 private server, so
      churn is cheap and a safe out-of-box state is worth more than
      compatibility. Affects `exec_output`'s response cap, project-config
      `environment:` handling, and `delete_file`'s root behavior.

---

## Ordering

**H1 comes first and is not optional.** The current test teardown removes every
container carrying the daemon's production label and every `mcp-cache-` volume
on the host. Until it is scoped, running the suite on this machine destroys
live workspaces and every dependency cache — including while validating any
other task here.

After H1, phases 1–3 (the six critical defects) should land before anything
else. Phases 4–7 are independent of each other and can be reordered freely.
Phases 8–9 are cleanup and can trail.

---

## Phase 0 — Make it safe to run the tests

### H1 — Scope test teardown to test-created resources ✅ (partial — see below)
**Satisfies:** HR-026 · **Verified by:** HAT-020
**Commit:** `dfb5ac0`

- [x] Track every container the suite creates and remove exactly those at
      teardown — **not** the label/prefix approach originally proposed (see
      below)
- [x] Track cache volumes the same way, from the mount that referenced them,
      and remove exactly those — not the global `mcp-cache-` prefix
- [x] Correct the fixture docstring, which claimed "nothing else on the host's
      Docker daemon is touched"
- [x] Add a sentinel container and sentinel volume, both carrying production
      identifiers, and assert a full suite run leaves them intact
- [ ] Make concurrent suite runs non-destructive to each other — **only the
      destructive half is fixed; the collision half is not** (see below)

**Why first:** `tests/workspace/conftest.py:61–70`. This is the one finding
that makes the act of verifying the others dangerous.

**Implementation departs from the literal task text, and `HARDENING_SPEC.md`'s
HR-026 was amended to match** (see its own "Implementation note"). Rather than
labeling containers or prefixing volume names with a session token, teardown
now tracks the *exact identity* of what was created: `conftest.py` monkeypatches
`docker.models.containers.ContainerCollection.run` for the whole test session,
recording every returned container's ID and — read straight off that same
call's `mounts` kwarg — every referenced volume name. `docker_client`'s teardown
drains those two lists and removes exactly what's in them. This is strictly
more precise than a label or prefix (nothing is inferred from a naming
convention; every removal targets an identity this process itself watched get
created) and needs zero changes to production code — no session token has to
be threaded through `container_labels()`/`cache_volume_name()`.

Patching the class method rather than an instance was deliberate: production
code (`workspace/manager.py`'s `_run_container`) and the handful of tests that
create a second container directly both call `docker.from_env()` for their own
fresh client, so an instance-level patch would miss most of what needs
tracking.

**A real bug the first version of this fix had**, caught by running the full
suite rather than trusting the new fixture in isolation: `containers.run()`
called without `detach=True` — the one-shot pattern `test_default_image.py`'s
`_run_oneshot` and two tests in `test_mounts_integration.py` use, always
paired with `remove=True` — returns the captured output as `bytes`, not a
`Container`. The first cut of `_tracking_run` assumed a `Container` back
unconditionally and crashed every one-shot call with
`AttributeError: 'bytes' object has no attribute 'id'`. Fixed by checking
`isinstance(result, docker.models.containers.Container)` before tracking;
bytes-returning one-shot calls need no tracking anyway, since Docker has
already force-removed that container by the time `run()` returns.

**Verified the fix actually verifies something**, not just that it passes:
temporarily restored the old label/prefix teardown logic and reran
`test_conftest_teardown.py`'s end-to-end test (the one that spawns a real,
separate `pytest` process against a throwaway test file placed inside
`tests/workspace/` so its real, wired-in fixture teardown executes — not a
direct call into the extracted cleanup function). It failed with a real
`docker.errors.NotFound` against the sentinel container, confirming the test
catches the regression it exists to catch. Restored the fix; reran; passes.

**`tests/workspace/test_conftest_teardown.py` (HAT-020) is two tests, not
one**, because a single test using pytest fixture dependencies for both the
sentinel and the client-under-test would have proven nothing: fixture
teardown order is LIFO, so a `sentinel_container` fixture depending on
`docker_client` tears down *before* `docker_client`'s own teardown runs,
meaning the sentinel would already be gone by the time the code under test
executes — passing regardless of whether that code is correct. First
`test_teardown_removes_only_what_it_tracked` calls the extracted
`remove_tracked_docker_resources` function directly, mid-test, to verify the
removal logic itself is selective. Second,
`test_a_full_suite_run_leaves_a_foreign_sentinel_intact` spawns a real nested
`pytest` subprocess so the *actual* fixture wiring — not a stand-in — is what
runs, which is the only way to honestly claim HAT-020 ("a full suite run
SHALL leave both intact") is covered.

**Not fixed — left honest, not silently dropped:** the last bullet named two
distinct problems bundled together. Teardown no longer *destroys* another
concurrent run's containers/volumes (each process only ever removes what it
itself tracked creating) — that half is fixed as a direct consequence of this
change. But the *collision* half is untouched: every test hard-codes
repository/workspace id `"example"`, so `container_name()`'s deterministic
naming means two pytest processes racing to `containers.run(name=
"mcp-dev-ws-example-<hash>", ...)` still hit a real `409 Conflict` — this fix
makes concurrent runs non-destructive to each other, not collision-free.
Making workspace ids unique per test run would touch dozens of test files
that assert on the deterministic name and is out of scope for a teardown fix;
left as a known gap for whoever next needs concurrent CI runs on shared
infrastructure.

---

## Phase 1 — Host escape

### H2 — Revision parameters cannot inject Git options ✅
**Satisfies:** HR-001 · **Verified by:** HAT-001
**Commit:** `5a7630c`

- [x] Reject `git_diff`'s `base`/`head` and `git_log`'s `ref` when the value
      begins with `-`, with `INVALID_ARGUMENT`
- [x] Additionally place revision arguments after `--end-of-options`, or
      validate through `git rev-parse --verify --quiet --end-of-options`
- [x] Audit every other argv built from client input for the same class
- [x] Test through the MCP tool layer, not just the internal function — the
      published contract is what an attacker holds

**Defect:** `git/diff.py:74`, `git/log.py:55`. Reproduced end-to-end: a real
`git_log` call with `ref="--output=<path>"` wrote a host file and returned
`{'result': []}`.

**Implementation.** Added `git/service.py:validate_revision_argument` (rejects
a leading `-` with `INVALID_ARGUMENT`) and `END_OF_OPTIONS = "--end-of-options"`,
a real Git marker verified against git 2.43.0 to make everything after it
parse strictly as a positional argument — even a value shaped exactly like
`--output=<path>` — with no side effect on options placed *before* it in the
same argv (`git_log`'s `--max-count`/`--pretty=format:` still parse correctly
ahead of the marker). `git_diff` places it immediately before `base`/`head`;
`git_log` places it immediately before `ref`. Both layers are deliberate, not
redundant: the leading-`-` rejection is the human-readable error a client
sees; `--end-of-options` is the one that would still hold if a future Git
release added option syntax that doesn't start with `-`.

**Audited every other client-reachable argv for the same class**, per the
task's own bullet, and found the rest already safe rather than assuming it:
- `remote` (`git_pull`/`git_push`'s free-form parameter, default `"origin"`)
  reaches `git/remote.py`'s `resolve_remote_url`, which runs `git remote
  get-url <name>` **before** `build_fetch_argv`/`build_push_argv` are ever
  called. Verified empirically: `git remote get-url --output=/tmp/x` exits
  `129` with `error: unknown option`, no file created — Git's own arg parser
  for that subcommand already fails closed on an option-shaped name, and
  `resolve_remote_url` treats any non-`ok` result as
  `GitRemoteNotAllowedError`, so an option-shaped `remote` can never reach the
  fetch/push argv at all.
- `branch` (`git_push`) was already validated via `git check-ref-format
  --branch` (S27), independently confirmed by the review to reject a leading
  `-`.
- `refspec` is entirely daemon-constructed from validated parameters
  (`build_refspec`), never a client-supplied string.
- `paths` (all of `git_diff`/`git_log`/`git_commit`) already sit behind a
  literal `--`, Git's own canonical "everything after this is a pathspec, not
  an option" separator — correct and unaffected by this task.

**New tests, both the pure-function level and the real MCP tool layer**:
`tests/git/test_diff.py`/`test_log.py` add `test_at_h001_*` tests reproducing
the exact regression (an option-shaped `base`/`head`/`ref` is rejected before
Git runs, and no file is created) plus a "legitimate value still works" test
for each, run independently of the file's other diff/log tests so a
regression here is unambiguous. `tests/workspace/test_git_diff_log_tools.py`
adds `test_at_hat001_git_diff_base_cannot_write_a_host_file` and
`test_at_hat001_git_log_ref_cannot_write_a_host_file`, calling the real
`git_diff`/`git_log` MCP tools with `base`/`ref` set to the literal exploit
string from the review's own reproduction — the published tool contract is
what an attacker holds, not the internal function.

### H3 — Host-side reads execute no repository-supplied code ✅
**Satisfies:** HR-002 · **Verified by:** HAT-002
**Commit:** `6141dfc`

- [x] Add `--no-ext-diff --no-textconv` to diff-producing reads
- [x] Enumerate the repository's own config and pass `-c <key>=` for every
      code-executing key (`HARDENING_SPEC.md` §4.1), as a named module-level
      control with a comment stating it must track Git
- [x] Run host-side reads with `GIT_CONFIG_GLOBAL=/dev/null` and
      `GIT_CONFIG_SYSTEM=/dev/null`
- [x] Strip `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`,
      `GIT_SEQUENCE_EDITOR`, `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_ASKPASS`,
      `GIT_PROXY_COMMAND`, `GIT_TEMPLATE_DIR` from the inherited environment
- [x] Cover `git_status`, `git_diff`, `git_log` **and** `workspace_info` —
      `read_basic_git_state` reaches the same code path
- [x] Assert each read still returns correct results; a read that fails is not a
      pass

**Defect:** `git/service.py:57–75`. Verified against git 2.43.0 during the
review, and these results constrained the implementation:

- `-c diff.external=` is **not** a fix — it blocks execution but aborts the read
  with `fatal: external diff died`. Use `--no-ext-diff`.
- `--no-ext-diff` does **not** cover `textconv`; `--no-textconv` is also needed.
- `core.attributesFile=/dev/null` does **not** help — the in-tree
  `.gitattributes` is what binds the driver.
- `git status` *can* execute `filter.*.clean` (see below — this turned out to be
  conditional, not unconditional, but the fix cannot depend on when).
- Driver names are attacker-chosen, so static overrides cannot work — enumerate
  what is configured, which is complete because an undefined driver cannot run.

**Implementation, four layers in `git/service.py`:**
1. **Static** `-c core.hooksPath=<empty-dir> -c core.fsmonitor=`, unchanged from
   before this task — kept as an explicit override even though layer 2 below
   would also catch them, so the property doesn't depend on the discovery probe
   succeeding.
2. **Dynamic driver neutralization**, new: `_discover_code_executing_overrides`
   runs `git config --list -z --local` and `--worktree` (two separate
   invocations — verified empirically that Git rejects `--worktree --local`
   combined in one call with `error: only one config file at a time`), matches
   every key against `_CODE_EXECUTING_CONFIG_KEY_RE` (the same pattern list
   `HARDENING_SPEC.md` §4.1 specifies, as one named, commented module constant),
   and returns `-c <key>=` for each match. This is what makes the mechanism
   generic rather than a name-matching denylist: it neutralizes whatever the
   repository actually defined, under whatever name, because a driver git never
   invokes unless it is defined somewhere.
3. **`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM=/dev/null`**, new: a host-side read
   depends only on the repository's own configuration, never the host's —
   proven with a key set *only* at global scope (so layer 2 would never even
   see it), which a baseline call honors and `run_git_host_side` does not.
4. **Environment scrubbing**, new: `GIT_EXTERNAL_DIFF`/`GIT_PAGER`/`GIT_EDITOR`/
   `GIT_SEQUENCE_EDITOR`/`GIT_SSH`/`GIT_SSH_COMMAND`/`GIT_ASKPASS`/
   `GIT_PROXY_COMMAND`/`GIT_TEMPLATE_DIR` removed from the inherited
   environment before every host-side invocation, the config-list probe calls
   included.

`--no-ext-diff --no-textconv` (item 1 of §4.1, diff-specific) live in
`git/diff.py`'s own argv construction, not `run_git_host_side` — verified that
`git status`/`git rev-parse` reject those flags outright (`error: unknown
option`), so they cannot be unconditional in the shared plumbing; verified they
are accepted in every mode `read_git_diff` actually uses (`--stat`, `--cached`,
commit-to-commit) before adding them unconditionally there.

**A real finding while writing the tests, not just the implementation**:
`git status`'s invocation of `filter.*.clean` turned out to be *conditional* on
Git's own internal "racily clean" stat-based heuristic, not unconditional as
the review first characterized it. In a freshly isolated repository, a plain
`git status --porcelain` (even the exact production shape,
`--porcelain=v2 -z --branch --untracked-files=all`) did **not** invoke the
filter for a simple stat-detectable modification — only `git diff`, which
genuinely needs the rendered content to build a patch, invoked it reliably
across every scenario tried. The full-container `test_host_side_execution_safety.py`
test still reliably reproduces marker creation across a `git_status`/
`git_diff`/`git_log`/`workspace_info` sequence when reverted (confirmed twice,
see below) — plausibly because `workspace_open`'s container-side activity
perturbs the working tree's stat state in a way a bare host repository never
does — but the *mechanism* is Git's own optimization, outside this daemon's
control, so the fix (and the unit-level tests in `tests/git/test_service.py`)
correctly does not depend on which specific host-side tool triggers it in a
given case: `run_git_host_side` neutralizes the driver unconditionally,
regardless of whether that particular call would have triggered it.

**Verified the fix actually verifies something**: reverted `git/service.py`
and `git/diff.py` to their pre-H3 state (keeping H2's fix) and reran
`tests/workspace/test_host_side_execution_safety.py` — all four tests failed,
each with the marker file present, confirming the exploit reproduces exactly
as characterized. Restored the fix; reran; all four pass, with the diff/status
content still correct (a read that merely fails is not a pass, per the task's
own bullet).

**New tests:**
- `tests/git/test_service.py`: five new tests at the `run_git_host_side` level
  — a locally-configured `filter.clean` driver (forced via `git diff`, the
  scenario verified to reproduce deterministically, not `git status`, which
  does not); an arbitrary, unlisted driver name, proving the mechanism is
  discovery rather than name-matching; `diff.external`; and global-config
  exclusion (a key set only in a fake `GIT_CONFIG_GLOBAL` file, honored by a
  raw baseline call and correctly ignored by `run_git_host_side`).
- `tests/workspace/test_host_side_execution_safety.py`, new file: four tests
  through the real MCP tool layer — `diff.external`, `.gitattributes`-bound
  `textconv`, `.gitattributes`-bound `filter.clean`, and an arbitrary unlisted
  driver name — each calling `git_status`, `git_diff`, `git_log`, and
  `workspace_info` in sequence (the exact tool set HAT-002 names) and asserting
  no marker is created across any of them, plus that `git_diff`/`git_status`
  still report the real change correctly.

---

## Phase 2 — Data destruction

### H4 — The repository root is not a deletable target ✅
**Satisfies:** HR-003 · **Verified by:** HAT-003
**Commit:** `5c0c64c`

- [x] Refuse a `delete_file` path resolving to the repository root, regardless
      of `recursive`
- [x] Refuse recursive descent into any directory containing a `.git` entry
      (nested repo, submodule, vendored checkout)
- [x] Cover `""`, `"."`, `"./"`, an absolute path to the root, and a traversal
      that lands on it

**Defect:** `filesystem/service.py:201–215`. Reproduced: working tree and `.git`
both removed, tool returned `{'deleted': True}`. Nothing locally recoverable.

**Implementation.** Two independent checks added to `FilesystemService.delete_file`,
ahead of the existing existence/recursive checks: (1) `path == self._security.root`
is refused with `INVALID_ARGUMENT`, regardless of `recursive` — this is the check
`_reject_in_tree_git_metadata` structurally cannot make, since the root's own
relative path has zero components, so `".git"` is never among them; (2) before
`shutil.rmtree`, `any(path.rglob(".git"))` refuses to sweep a nested repository's
Git metadata into a recursive delete of its *containing* directory — the existing
guard already blocks *targeting* a nested `.git` directly, but not this. Both
checks fire regardless of whether the `.git` in question is a directory (an
ordinary repo) or a file (a gitfile — a linked worktree or submodule), since
`rglob(".git")` matches by name, not by type.

**"An absolute path to the root" from the task text turned out not to be a real
case, caught before it shipped as a wrong test.** §12 already reinterprets a
leading `/` as repository-relative rather than truly absolute
(`_resolve_candidate`'s `lstrip("/")`), so `delete_file(path=str(root))` does
**not** resolve back to the root — it resolves to `root / str(root).lstrip("/")`,
a deeply nested (and generally nonexistent) path. Verified this directly before
trusting it: `sec.resolve(str(root))` returned
`.../work/example/tmp/.../work/example`, not the root. Replaced that variant
with one that does genuinely reproduce "a traversal that lands on the root" —
`src/nested/../..` from two levels down — verified separately to resolve to
`sec.root` exactly, before writing the test around it.

**Verified the fix actually verifies something**: reverted `filesystem/service.py`
to its pre-H4 state and reran the new `HAT-003`-tagged tests — all 9 in
`tests/filesystem/test_service.py` failed (the root-deletion variants with "DID
NOT RAISE", the nested-`.git` cases the same way), confirming they catch the
exact regression. Restored the fix; reran; all pass, and the pre-existing 82
filesystem tests are unaffected (an ordinary directory with no nested `.git`
still deletes normally — checked explicitly, not merely assumed from the new
checks' logic).

**New tests:**
- `tests/filesystem/test_service.py`: a parametrized root-deletion test over
  `""`, `.`, `./`, `src/..`, `src/../.`; a separate deep-traversal variant;
  a non-recursive-root case (proving the root check fires before the
  recursive/non-recursive branch, not as a side effect of it); a nested-`.git`
  directory case and a nested-gitfile case; and a explicit "ordinary directory
  still deletes fine" regression guard.
- `tests/tools/test_filesystem.py`: `test_at_hat003_delete_file_cannot_erase_the_repository_root`,
  calling the real `delete_file` MCP tool with the literal exploit call from the
  review (`path="", recursive=True`) and asserting the repository, `.git`, and
  an unrelated file all survive.

### H5 — A truncated read never becomes a write ✅
**Satisfies:** HR-004, HR-005 · **Verified by:** HAT-004
**Commit:** `ea3dd17`

- [x] `patch_file` fails with `INVALID_ARGUMENT` when its read was truncated
- [x] `patch_file` passes the sha256 it read through as the write's
      `expected_sha256`, closing its own read/write window
- [x] Enforce `filesystem.max_patch_bytes` on the incoming diff
- [x] Audit for other read-bounded/write-whole pairs

**Defect:** `tools/filesystem.py:187–219`. Reproduced: 1950 of 2000 bytes
destroyed, reported as `{'applied': True}` with a fresh valid sha256.

**Implementation.** Three checks added to `patch_file`, in order: (1) the diff
payload's byte length is checked against `filesystem.max_patch_bytes` before
the file is even read, so an oversized diff fails cheaply; (2) `current.truncated`
is checked immediately after the read and refused with `INVALID_ARGUMENT`
before any patching is attempted; (3) `write_file` is now called with
`expected_sha256=current.sha256` unconditionally — not only when the *client*
supplied one — closing the function's own read/write race independent of the
caller.

That third check is safe specifically because `ReadResult.sha256` is documented,
and verified by reading `read_file`'s implementation, to always be the hash of
the *full, untruncated* file content (`full_sha = sha256_of(data)` is computed
before any range/truncation is applied) — so it remains a correct
`expected_sha256` for the write even in the ordinary, non-racing case, and the
truncation refusal above means it's never reached with content that doesn't
actually correspond to that hash.

**Audited for other read-bounded/write-whole pairs**, per the task's own
bullet, rather than assuming `patch_file` was the only one: grepped every
`read_file(...)` call site in `src/`. Two exist — `patch_file` (fixed here) and
the `read_file` tool itself, which only returns what it read (with `truncated`
correctly reported to the client) and never writes anything back. No other
instance of this pattern exists in the codebase.

**Verified the fix actually verifies something**: reverted `tools/filesystem.py`
to its pre-H5 state and reran the two new `HAT-004` tests — both failed with
"DID NOT RAISE `ToolError`," confirming they reproduce the exact regression.
Restored the fix; reran; both pass, and the pre-existing 12 filesystem-tool
tests plus 82 filesystem-service tests are unaffected — including a new
regression guard proving an ordinary patch within both limits still applies
correctly.

**New tests**, all in `tests/tools/test_filesystem.py`, at the real MCP tool
layer: the literal exploit scenario from the review (2000-byte file, 50-byte
`max_read_bytes`, patch rejected, file byte-identical afterward);
`max_patch_bytes` enforcement with an oversized diff; and a regression guard
that an ordinary patch below both limits still applies and returns the correct
content.

---

## Phase 3 — Configuration boundary

### H6 — Project configuration cannot set daemon-controlled variables ✅
**Satisfies:** HR-006 · **Verified by:** HAT-005
**Commit:** `c5c4a1b`

- [x] Reject `SSH_AUTH_SOCK` and any `MCP_*` key in `.mcp-workspace.yml`'s
      `environment:`, naming the rejected key in the error
- [x] Independently, merge daemon-controlled variables **last** in
      `build_workspace_run_kwargs` so ordering alone cannot override them
- [x] Keep both controls — neither is sufficient alone
- [x] Assert PID 1 does not run as root on a daemon without userns remapping

**Defect:** `container_config.py:100–112`, `tools/workspace.py:351`. The §5.3
refusal at line 89 inspects only `container_config.user`. Reproduced: root PID 1
and a setuid-root host binary via the bind mount.

**Implementation, two independent controls.** (1) A new `field_validator` on
`ProjectWorkspaceConfig.environment` (`project_config.py`) rejects any key that
is `SSH_AUTH_SOCK` or starts with `MCP_`, naming every offending key in the
error, at project-config *load* time — before `workspace_open` ever reaches
Docker. (2) `build_workspace_run_kwargs` now merges `extra_env` **first** in
both the `root` and non-`root` branches, with the daemon's own `MCP_UID`/
`MCP_GID`/`MCP_SUDO`/`MCP_WORKSPACE_PATH`/`MCP_SSH_MODE`/`SSH_AUTH_SOCK`
merged last — so ordering alone still holds even if control (1) is somehow
bypassed. Neither is redundant: (1) is what makes the failure legible (a clear
error naming the key, before any container exists); (2) is what holds if (1)
doesn't.

**A pre-existing test collided with the new reserved namespace, and got fixed
rather than the control weakened.** `test_environment_from_project_config_reaches_the_container`
used `MCP_TEST_VAR` as its example variable name — which the new validator
correctly rejects, since `MCP_*` is now unconditionally reserved for the
daemon, not just the specific names it happens to use today. Renamed the
test's variable to `PROJECT_TEST_VAR` and added a comment explaining why,
rather than narrowing the validator to only the daemon's current specific
variable names — narrowing it that way would silently reopen the door to any
`MCP_*` name the daemon adds later.

**Verified against a real container, not only the `run_kwargs` dict.** Added
`test_at_hat005_hostile_extra_env_cannot_make_pid_1_root` to
`test_default_image.py`, in the same one-shot-container style S7's own
identity tests use (a one-shot container's command *is* PID 1, unlike
`docker exec`, which uses the image's default user) — `extra_env={"MCP_UID":
"0", "MCP_GID": "0"}` passed directly to `build_workspace_run_kwargs`
(bypassing control (1), simulating it being defeated) still produces `id -u`
== the real checkout uid, not `0`. Reverted `container_config.py` alone and
reran: `AssertionError: assert '0' == '1234'` — PID 1 genuinely was root under
the old code, against a real container, not merely a constructed dict.
Restored the fix; reran; passes.

**Verified the fix actually verifies something** at every layer: reverted
`project_config.py` and `container_config.py` together and reran every new
test — all 9 `HAT-005`-parametrized/named tests across `test_project_config.py`
and `test_container_config.py` failed as expected (one legitimate
"still works normally" test correctly kept passing throughout); reverted the
end-to-end `test_project_config_integration.py` test alone and confirmed
"DID NOT RAISE `ToolError`" while a container was genuinely created (visible
until H1's own tracked teardown removed it — a small confirmation that H1 and
H6 compose correctly). Restored both files; reran everything; all pass.

**New tests:** `tests/test_project_config.py` (parametrized over all six
reserved variable names, plus a "still works normally" guard);
`tests/workspace/test_container_config.py` (three tests proving the ordering
control alone, including the `container.user: root` branch specifically, not
only the non-root one); `tests/workspace/test_project_config_integration.py`
(the literal exploit `.mcp-workspace.yml` through the real `workspace_open`
MCP tool, asserting no container is created at all); `tests/workspace/
test_default_image.py` (the real-container PID-1 proof above).

### H7 — The §19.1 key invariant covers every key path
**Satisfies:** HR-007 · **Verified by:** HAT-006

- [ ] Extend `_check_ssh_key_not_under_host_ssh` to `ssh.shared_key`
- [ ] Restructure so a new key-bearing config field that is not checked fails a
      test rather than passing silently
- [ ] Cover all three paths in tests

**Defect:** `config/loader.py:74–94`. Reproduced: `shared_key:
"../fakehome/.ssh/id_ed25519"` accepted, resolving to the host user's personal
key. FR-025 says this "SHALL NOT be relaxed by policy or configuration."

### H8 — The entrypoint re-validates cache paths
**Satisfies:** HR-008 · **Verified by:** HAT-007

- [ ] Reject absolute, `..`-containing, and empty `MCP_CACHE_PATHS` entries in
      `entrypoint.sh` before `mkdir`/`chown`
- [ ] Reject `:` in a cache path host-side (it is the separator)
- [ ] Keep host-side validation too — both sides of the boundary

**Defect:** reproduced — `'../../etc:../../usr/bin'` left `/etc` and `/usr/bin`
owned by the container user.

---

## Phase 4 — Path containment

### H9 — File access is race-free
**Satisfies:** HR-011 · **Verified by:** HAT-009

- [ ] Open files with `O_NOFOLLOW` relative to a directory fd pinned at
      validation time, or re-verify the opened inode with `fstat`
- [ ] Apply to read, write, patch, and delete paths
- [ ] Test with a sustained concurrent flipper, not a single attempt

**Defect:** `filesystem/security.py:69–80` validates by path;
`service.py:132,196` re-open by path. Reproduced: a host private key returned on
iteration 1; an escaping write within 24.

### H10 — An escaping symlink does not break enumeration
**Satisfies:** HR-012 · **Verified by:** HAT-010

- [ ] Catch `PathOutsideRepositoryError` alongside `PathGitMetadataError` in
      `list_files` and `search_files`
- [ ] Report escaping and dangling symlinks as entries, as `_describe`'s
      docstring already promises
- [ ] Test with a `.venv/bin/python`-style link, the common real case

---

## Phase 5 — Integrity of reported guarantees

### H11 — Error messages are redacted
**Satisfies:** HR-013 · **Verified by:** HAT-011

- [ ] Redact `payload["message"]`, not only `details`
- [ ] Fix redaction of list values (the key hint is currently dropped),
      non-string scalars, and `scheme://user:pass@host` userinfo
- [ ] Cover the YAML-echo path — a malformed `.mcp-workspace.yml` currently
      echoes the offending source line verbatim

**Defect:** `errors.py:248–259, 273–280`. Reproduced: a `ghp_` token in
cleartext in `message` while `details.url` was correctly `[REDACTED]`.

### H12 — The validated push destination is the one used
**Satisfies:** HR-014 · **Verified by:** HAT-012

- [ ] Resolve with `git remote get-url --push`, or push to the validated URL
      rather than the remote name
- [ ] Validate both URLs where fetch and push differ
- [ ] Assert the audit record names the destination actually used

**Defect:** `git/remote.py:48–65`, `git/push.py:96–100`. Reproduced: validation
approved `owner/repo` while the commit landed elsewhere and the audit record
named the wrong destination.

### H13 — Denials are audited at §20.7 fidelity
**Satisfies:** HR-015

- [ ] Include the policy key from `PolicyDeniedError.details["policy"]`
- [ ] Include `repository_id`, and `remote`/`branch`/`remote_repository` where
      applicable
- [ ] Add `refspec` and `remote_protection_status` to `git_push` success records
- [ ] Do not let an unwritable audit path replace a denial with a crash

### H14 — Authorization is re-checked against live configuration
**Satisfies:** HR-016 · **Verified by:** HAT-013

- [ ] Validate the resolved repository against the live `RepositoryRegistry` on
      every workspace-scoped call
- [ ] Test that removing a repository from config immediately revokes access
      through its stale `workspace_id`

---

## Phase 6 — Recoverability

### H15 — A workspace recovers from any Docker state
**Satisfies:** HR-017 · **Verified by:** HAT-014

- [ ] Create a container for an existing row whose container is missing
- [ ] Adopt-or-remove a container found by deterministic name with no matching
      row, on the create path and not only in `reconcile()`
- [ ] Cover: removed out of band; failed create; orphaned name collision

**Defect:** `workspace/manager.py:246–261, 509–516`. Currently a transient
failure wedges the workspace permanently, even after Docker recovers.

### H16 — Deleting a workspace that ran jobs succeeds
**Satisfies:** HR-018 · **Verified by:** HAT-015

- [ ] Cascade `job.workspace_id`, or delete dependent rows in order inside one
      transaction
- [ ] Ensure a failure cannot leave the container removed and the row in
      `deleting`

### H17 — Migrations are atomic
**Satisfies:** HR-019 · **Verified by:** HAT-016

- [ ] Wrap migrations in explicit `BEGIN`/`COMMIT` (`with connection:` does not
      wrap DDL)
- [ ] Add a migration test against a populated database at an older
      `user_version`, not only an empty one
- [ ] Configure `busy_timeout` and consider WAL; two processes open the same
      file today with neither

### H18 — A failed termination is not reported as success
**Satisfies:** HR-020, HR-021 · **Verified by:** HAT-017

- [ ] Propagate `PermissionError` from `kill` as a structured error
- [ ] Do not mark a job `timed_out`/`canceled` when termination failed
- [ ] Bound the drain task so a stream that never EOFs cannot hold an executor
      thread forever
- [ ] Kill the process group, not only the direct child
- [ ] Mark a job terminal when its exec start fails, so it stops pinning the
      workspace against idle stop
- [ ] Evict `AsyncJobRunner._live` entries on completion (currently unbounded)

### H19 — Structured errors survive Docker and decoding failures
**Satisfies:** HR-022 · **Verified by:** HAT-019

- [ ] Translate `docker.errors.DockerException` in the exec and workspace paths
- [ ] Decode host-side Git output with an explicit error strategy so a non-UTF-8
      filename cannot crash `git_status`/`git_diff`/`workspace_open`
- [ ] Tolerate a commit subject containing `\x1e` in `git_log`'s parser
- [ ] Correct the `log.py` docstring claiming such bytes are invalid in a subject

---

## Phase 7 — Git and workspace correctness

### H20 — `git_commit` refuses unresolved conflicts
**Satisfies:** HR-023 · **Verified by:** HAT-018

- [ ] Inspect `status.conflicted` alongside `detect_interrupted_operation`
- [ ] Test via a real `git stash pop` conflict, which leaves no marker file

### H21 — `git_commit` stages exactly what it was told
**Satisfies:** HR-024 · **Verified by:** HAT-018

- [ ] Pass client paths as literal pathspecs (`:(literal)` or
      `--pathspec-file-nul`)
- [ ] Report `files_committed` from what Git committed, not the request echo
- [ ] Normalize paths before the strict-mode set-difference check, and account
      for staged renames
- [ ] Test `paths=[":/"]` and `[":(glob)**"]`

### H22 — A failed commit leaves the index unchanged
**Satisfies:** HR-025

- [ ] Roll back `git add` when `git commit` fails (§20.6)
- [ ] Map "nothing to commit" to a specific code rather than `COMMAND_FAILED`

### H23 — Cache volumes cannot collide across repositories
**Satisfies:** HR-009 · **Verified by:** HAT-008

- [ ] Hash-derive the volume name from `(repository_id, cache_name)`
- [ ] Provide a migration or documented path for existing volumes
- [ ] Reclaim volumes on `delete` (currently orphaned, and inherited by a later
      repository reusing the id)

### H24 — Assorted Git correctness
**Satisfies:** HR-022 (partial)

- [ ] `git_diff`: return `next_offset` and split on character boundaries — byte
      offsets currently corrupt any non-ASCII diff and reassembly is impossible
- [ ] `git_diff`: validate `offset`/`max_bytes` (negative values currently
      slice from the end)
- [ ] `detect_default_branch`: use `removeprefix("refs/remotes/origin/")`, not
      `rsplit("/", 1)`, which mangles `release/v1`
- [ ] Accept abbreviated `expected_head_sha`, or document that it must be full
- [ ] Handle `amend_published`'s `require_approval` consistently with
      `default_branch_push`
- [ ] `git_commit`: call `touch_last_used` so an active commit stream resets the
      idle clock

---

## Phase 8 — Test and CI integrity

### H25 — The coverage audit does not satisfy itself
**Satisfies:** HR-027 · **Verified by:** HAT-021

- [ ] Exclude `__file__` from `_all_test_source()`
- [ ] Add a meta-test: removing every real citation for an AT makes the audit
      fail for that AT

**Defect:** `tests/test_acceptance_test_coverage.py:36,59`. Verified — with all
real AT-010 citations removed, the audit still passed, because its own error
message contains the string. Line 23 does the same for AT-011.

### H26 — Coverage claims rest on assertions
**Satisfies:** HR-028

- [ ] Implement AT-037's `ssh_auth` audit event, or record AT-037 as explicitly
      uncovered — `grep -rn ssh_auth src/` currently returns nothing
- [ ] Make AT-021's scenario reachable by adding §24's `exec` audit event, or
      amend the AT
- [ ] Re-audit every AT whose only citation is a docstring
- [ ] Correct S33's category audit, which cites `filesystem/test_security.py`
      for "secret leakage" on the strength of a file *named* `secret.txt`

### H27 — A degraded CI run fails
**Satisfies:** HR-029 · **Verified by:** HAT-022

- [ ] Fail CI when Docker-dependent tests skip (`--strict-markers` plus an
      explicit skip guard, or a minimum-collected assertion)
- [ ] Replace the stale `alpine:3.20` pull step, which is no longer the test
      image
- [ ] Make the contract check able to fail on an unbumped `contract_version` —
      it currently compares the constant against itself

### H28 — Adversarial coverage for every input boundary
**Satisfies:** HR-030

- [ ] For each control in Phases 1–5, add a hostile-input test alongside the
      well-formed one
- [ ] Fix the tests the review found asserting nothing meaningful: AT-006's
      "disconnect" (`del started` on an in-process server), `test_write_is_
      flushed_immediately_for_concurrent_readers` (single-threaded), and
      `test_reconcile`'s hand-set `pid` (a shape production never produces)
- [ ] Add restart-survival coverage for jobs, which currently has none

---

## Phase 9 — Defaults, dead configuration, observability, documentation

### H29 — Bound output by default
**Satisfies:** HD3, `HARDENING_SPEC.md` §9 (§15.5 amendment)

- [ ] Default the `exec_output` response cap to ~64 KiB, with the full capture
      reachable only through explicit ranged retrieval
- [ ] Validate `byte_start`/`byte_end`/`line_start`/`line_end` (negatives
      currently slice from the end)
- [ ] Reconcile byte offsets against `stream_total_bytes`, which currently index
      different things and silently mislead a paging client
- [ ] Stream rather than slurping in `read_log_bytes`
- [ ] Bound `read_file` by stat before reading, not after

**Defect:** the current default is `max_output_bytes + tail_keep_bytes` =
10.25 MiB, defeating §15.5's stated purpose. AT-007 passes only because that
test reconfigures the cap to 4096.

### H30 — Enforce or delete declared configuration
**Satisfies:** HR-010

- [ ] Enforce or remove the `security:` block (`allow_docker_socket`,
      `allow_privileged_containers`, `allow_additional_host_mounts`)
- [ ] Enforce or remove `git_policy.validate_remote`
- [ ] Clamp client `timeout_seconds` to `execution.sync_timeout_seconds`
- [ ] Normalize `cwd` before the containment test (`/workspace/../etc` currently
      passes a string-prefix check)
- [ ] Add cross-field validation: `api_auth: token` with no token path,
      `scope: shared` with no `shared_key`, `user: root` with `sudo: true`
- [ ] Extend the §53 secrets-outside-repository check to `audit.path` and
      `state_db_path`
- [ ] Implement §73 step 7 — repository roots are never checked for existence
- [ ] Catch non-`ConfigError` exceptions at load (`workspace: 5` currently
      raises a raw `TypeError`)

### H31 — Operational logging
**Satisfies:** HR-031

- [ ] Introduce `logging` with a configurable level
- [ ] Log swallowed exceptions, container and ssh-agent lifecycle,
      reconciliation outcomes, and policy denials
- [ ] Route secrets through HR-013's redaction
- [ ] Replace `except Exception: exit_code = None` with a logged, typed handler

### H32 — Close the remaining spec gaps
**Satisfies:** `HARDENING_SPEC.md` §9

- [ ] Report OOM through a real surface — detection exists, reporting does not
- [ ] Query GitHub rulesets, not only branch protection (§18.5, FR-032), and
      distinguish "confirmed absent" from "not visible to this token"
- [ ] Ship pinned GitHub host keys, or document operator provisioning — the box
      is ticked, the file is created empty, and a fresh install cannot push
- [ ] Make `GIT_AUTH_FAILED` reachable (it is the likeliest real push failure
      under deploy-key auth and currently surfaces as `COMMAND_FAILED`); same
      for `GIT_REFSPEC_NOT_ALLOWED`, `NETWORK_FAILED`, `INTERNAL`
- [ ] Warn when `.devcontainer/devcontainer.json` is present and ignored (§30
      requires reporting, not support)
- [ ] Validate `.mcp-workspace.yml`'s `version:` — `999` is currently accepted
      and recorded
- [ ] Re-run setup steps after `reset`, or document that reset discards project
      configuration; a failed step currently leaves a permanently
      half-provisioned workspace that the next open silently accepts
- [ ] Give `reset` and `rebuild` distinct behavior or merge them in the spec
- [ ] Restore project `environment:` and cache mounts on `reset`/`rebuild`

### H33 — Documentation matches the shipped system
**Satisfies:** HR-032

- [ ] `docs/TOOLS.md`: list all 21 tools (six structured Git tools are missing),
      drop "the six future structured Git tools", fill in `contract_version`
      0.7.2
- [ ] `docs/CONFIGURATION.md`, `SECURITY.md`, `THREAT_MODEL.md`: write them or
      state plainly that `docs/SPEC.md` is the only reference
- [ ] `docs/DEVELOPMENT.md`: `--config` no longer merely checks existence
- [ ] Add a docs check to CI so the tool table cannot drift again

### H34 — Apply the `docs/SPEC.md` amendments
**Satisfies:** HD2, `HARDENING_SPEC.md` §9

- [ ] Apply all sixteen amendments in `HARDENING_SPEC.md` §9
- [ ] Bump `docs/SPEC.md`'s version and date
- [ ] Note in `HARDENING_SPEC.md` §2 that precedence has returned to
      `docs/SPEC.md` for each amended point

### H35 — Reconcile the checkboxes
**Satisfies:** HR-033

Untick or qualify these in `docs/TODO.md`, each with a pointer to the H-task
that closes it. The review's finding was not that tasks were unbuilt — most were
built substantially — but that a box is ticked and the prose beneath then walks
the claim back.

| Task | Correction |
| --- | --- |
| S2 | §19.1 invariant unguarded for `shared_key` (H7); §73 step 7 unimplemented (H30); invalid YAML escapes as `TypeError` (H30) |
| S3 | Migrations not atomic (H17); no concurrency configuration (H17) |
| S6 | §69 reconciliation reaches `absent` but never recovers (H15) |
| S9 / S10 | `max_patch_bytes` unenforced and `patch_file` truncation destroys data (H5); `delete_file("")` wipes the repository (H4); §12.1 rule 3 fails open when only `repositories[]` is configured |
| S12 | `cwd` check bypassable, timeout unclamped, no Docker error handling (H19, H30) |
| S15 | "no race between running and having a pid" is false ~95% of the time; `update_output_counts` is dead code |
| S21 | Argument injection (H2); bounded-diff offsets break on non-ASCII (H24) |
| S22 | Commits through unresolved conflicts (H20); pathspec magic defeats strict mode (H21); failed commit leaves paths staged (H22) |
| S24 | "Pinned GitHub host keys" ships zero keys (H32) |
| S28 | Rulesets never queried (H32) |
| S29 | Redaction covers `details` only (H11); denial fidelity below §20.7 (H13) |
| S31 | OOM reporting absent; pid exhaustion has no daemon-side detection (H32) |
| S33 | AT-037 has no test, AT-021's scenario is unreachable, coverage audit self-satisfies (H25, H26) |

S32's deferral was verified as accurate against §22.1, §22.2, §20.6 and §34.3 —
no correction needed.

---

## Deferred

### H36 — Move Git reads into the container
**Supersedes:** HD1

Not scheduled. Eliminates the host-side execution class structurally rather than
by maintaining §4.1's control surface. Costs: contradicts §20.1's stated
exception, removes Docker-free `workspace_info`/`git_status` and part of §73's
degraded start, and adds container-exec latency to every read. Revisit if §4.1
proves burdensome to maintain, or if a knob is discovered that it cannot cover.

---

## Definition of done

All six critical defects are closed and covered by HAT-001..HAT-008; the full
suite passes with no Docker-dependent test skipped; a suite run leaves foreign
Docker state untouched; `docs/SPEC.md` carries the §9 amendments; and no ticked
checkbox in either TODO overstates what the code does.
