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

### H7 — The §19.1 key invariant covers every key path ✅
**Satisfies:** HR-007 · **Verified by:** HAT-006
**Commit:** `e8e39ba`

- [x] Extend `_check_ssh_key_not_under_host_ssh` to `ssh.shared_key`
- [x] Restructure so a new key-bearing config field that is not checked fails a
      test rather than passing silently
- [x] Cover all three paths in tests

**Defect:** `config/loader.py:74–94`. Reproduced: `shared_key:
"../fakehome/.ssh/id_ed25519"` accepted, resolving to the host user's personal
key. FR-025 says this "SHALL NOT be relaxed by policy or configuration."

**Implementation.** `_check_ssh_key_not_under_host_ssh` now checks
`config.ssh.shared_key` (resolved against `keys_dir`, same as the per-repository
check) alongside `ssh.keys_dir` and every `repositories[].ssh_key`.

**The "restructure" bullet was deliberately NOT a cross-module refactor.**
`workspace/ssh_agent.py`'s `resolve_ssh_key_path` already encodes "which key
file will actually be loaded" — importing it into `config/loader.py` was
considered and rejected: it would make a config-validation module depend on a
runtime/process-management one (`ssh_agent.py` pulls in `subprocess`/`signal`
for spawning real agent processes), inverting the codebase's normal
config → workspace dependency direction for a benefit `field_validator`-free
restructuring didn't need. Instead, `tests/config/test_loader.py` gained
`test_every_ssh_config_key_bearing_field_is_checked`: a meta-test asserting
`set(SSHConfig.model_fields)` and `set(RepositoryConfig.model_fields)` each
equal exactly (the fields the check inspects) ∪ (an explicit, named allowlist
of fields decided to be non-key-bearing). Adding any new field to either
model without updating one of those two sets fails this test immediately —
which is HR-007's actual requirement, achieved without new coupling.

**That meta-test's actual guarantee, verified precisely, not assumed:** it
protects against a *future* field being added silently — confirmed by adding
a synthetic extra field to the checked set and watching the assertion fail —
but it does **not**, and structurally cannot, retroactively prove
`shared_key`'s pre-existing gap was closed, since it only compares the
*model's* field set, not the *check function's* actual behavior. Reverted
`config/loader.py` alone and confirmed the meta-test still passed (correctly
— `SSHConfig`'s fields didn't change), while the dedicated
`test_at_hat006_shared_ssh_key_under_host_ssh_rejected` regression test failed
with "DID NOT RAISE `ConfigError`". The two tests are doing genuinely
different jobs, and both are needed.

**New tests:** `tests/config/test_loader.py` — the literal exploit config
(`scope: shared`, `shared_key` walking into `~/.ssh`) rejected with the
`ssh.shared_key` error naming that specific path; a "legitimate shared key
outside `~/.ssh` still accepted" guard; and the structural meta-test above.

### H8 — The entrypoint re-validates cache paths ✅
**Satisfies:** HR-008 · **Verified by:** HAT-007
**Commit:** `b012229`

- [x] Reject absolute, `..`-containing, and empty `MCP_CACHE_PATHS` entries in
      `entrypoint.sh` before `mkdir`/`chown`
- [x] Reject `:` in a cache path host-side (it is the separator)
- [x] Keep host-side validation too — both sides of the boundary

**Defect:** reproduced — `'../../etc:../../usr/bin'` left `/etc` and `/usr/bin`
owned by the container user.

**H6 already closed the specific path this defect was originally reproduced
through**, discovered while writing this task's own test: `.mcp-workspace.yml`
setting `environment: {MCP_CACHE_PATHS: '../../etc'}` is now rejected at
project-config load time, since `MCP_CACHE_PATHS` is itself `MCP_`-prefixed
and H6's validator rejects any such key unconditionally — verified directly
before writing this task's tests, rather than assumed. This task's fix is
therefore genuine defense in depth, not the only barrier: it holds regardless
of *how* an unsafe `MCP_CACHE_PATHS` value might reach the container (a future
code path, a bug in `caches.py`'s own resolution), not only the one route
already closed.

**Implementation.** `entrypoint.sh` validates each `:`-split
`MCP_CACHE_PATHS` entry before `mkdir`/`chown` — rejects empty, rejects a
leading `/` (absolute), and rejects any `/`-separated component exactly equal
to `..` (checked component-wise, not by substring, so a path merely
*containing* the string `..` isn't wrongly rejected) — exiting non-zero with a
clear message rather than silently skipping the bad entry, since this runs as
root before the identity drop and a silent skip would hide a misconfiguration
that also lost a cache mount. `workspace/caches.py`'s `_home_relative_path`
now also rejects a literal `:` in a cache path, since the daemon joins every
resolved path into one colon-separated string before it ever reaches the
entrypoint — a legitimate path containing `:` would otherwise silently
fracture into two separate `MCP_CACHE_PATHS` entries.

**Verified against a real container, both ways.** Rebuilt the test image
before probing by hand — confirmed a stale, pre-existing tagged image (from
before this edit) let `MCP_CACHE_PATHS='../../etc'` straight through, which is
why the test uses the `default_image_tag` fixture's own session-scoped
rebuild rather than assuming an already-tagged image reflects the current
source. With the fix, `docker.errors.ContainerError` is raised with the
entrypoint's own message. Since H6 already blocks the project-config route,
the real-Docker test passes the hostile value as `extra_env` directly to
`build_workspace_run_kwargs` — the same "simulate an earlier layer being
bypassed" technique H6's own `test_at_hat005_hostile_extra_env_cannot_make_pid_1_root`
uses — to exercise the entrypoint's defense specifically, independent of how
the value might arrive.

**Verified the fix actually verifies something**: reverted `entrypoint.sh`
and `caches.py` together and reran both new tests — the real-container test
failed with "DID NOT RAISE `ContainerError`," the pure-Python `:`-rejection
test failed with "DID NOT RAISE `ContainerCreateFailedError`." Restored both;
reran; both pass, and no other cache/mount/project-config test regressed.

**New tests:** `tests/workspace/test_default_image.py` (the real-container
traversal-through-`extra_env` proof above) and `tests/workspace/test_caches.py`
(the `:`-rejection unit test).

---

## Phase 4 — Path containment

### H9 — File access is race-free ✅
**Satisfies:** HR-011 · **Verified by:** HAT-009
**Commit:** `4bfff54`

- [x] Open files with `O_NOFOLLOW` relative to a directory fd pinned at
      validation time, or re-verify the opened inode with `fstat`
- [x] Apply to read, write, patch, and delete paths
- [x] Test with a sustained concurrent flipper, not a single attempt

**Defect:** `filesystem/security.py:69–80` validates by path;
`service.py:132,196` re-open by path. Reproduced: a host private key returned on
iteration 1; an escaping write within 24.

**Implementation: the "re-verify with fstat" alternative, generalized to a real
path comparison.** `FilesystemSecurity` gained three primitives — `open_validated`
(open + verify in one call), `open_parent_validated` (opens the *parent* as a
`dir_fd`, for `delete_file`'s `dir_fd`-relative operations), and the pair
`resolve_open_fd`/`is_within_root` a caller with a more involved open sequence
(`write_file`) can use directly. All three read `/proc/self/fd/<fd>` — the
kernel's own record of an open fd's canonical path — rather than a bare `fstat`
inode number: an `open()` is atomic, so whatever this reports is genuinely what
got opened, immune to any race after the fact, and it gives back an actual
*path* to compare against the repository root rather than an opaque inode to
match against a second, separately-racy `stat()`. Linux-only (`/proc`), matching
this daemon's existing Linux-only assumptions elsewhere (`entrypoint.sh`'s
`setpriv`/`useradd`).

`read_file` now opens via `open_validated` and uses `fstat` on the *opened fd*
(not a fresh `stat()` on the path) for its "is this a regular file" check —
that check was itself a second, independent race in the original code.
`delete_file` opens the *parent* directory race-free and passes `dir_fd=` to
`os.unlink`/`shutil.rmtree` — `unlinkat`/`shutil.rmtree(dir_fd=...)` resolve the
target relative to that pinned fd with no further path-string re-resolution at
all, closing the race for the delete syscalls themselves, not only the
validation before them (confirmed `shutil.rmtree` on this Python already walks
recursively via `dir_fd`-relative operations internally once it has its own
top-level fd — `shutil._use_fd_functions` — so passing `dir_fd` here extends
that same safety to the initial hop too). `patch_file` needed no separate
change: it already goes through `read_file` then `write_file`.

**A real bug in my own first draft, caught by adversarial testing before it
shipped, not assumed away.** The first version of `write_file`'s fix correctly
*rejected* an escaped write (no success ever returned to the caller) but cleaned
up the just-created file by calling `path.unlink()` — the same racy path
*string* the fix exists to stop trusting. Under sustained racing, the race that
redirected the `open()` to the escaped location had usually already moved on by
cleanup time, so the unlink silently missed the real file and a stray,
attacker-content file was left behind outside the repository — confirmed with
a dedicated adversarial script: escaped on iteration 7, with the rejection
correctly reported to the caller every time. Fixed by capturing the actual
kernel-reported path (`resolve_open_fd`) *before* attempting cleanup and
unlinking that, not the original string — re-tested with the same script for
3000 iterations with zero stray files afterward. Deliberately did **not** put
the actual escaped host path in the exception's `details`, since §31 errors are
serialized straight back to the MCP client — surfacing it there would leak host
filesystem structure through what's supposed to be a client-facing "your path
was invalid" error.

**Two residual gaps, deliberately scoped out and documented, not silently
dropped:** (1) `write_file`'s `path.parent.mkdir(parents=True, exist_ok=True)`
still resolves by path string, so a pre-existing malicious symlink at an
intermediate component could make `mkdir` create empty directories through it
before the fd-based checks below ever run — those checks still guarantee no
*data* is ever written outside the root even so (HR-011's actual property), but
the *mkdir* side effect itself isn't closed; doing so would need the full
per-component `O_NOFOLLOW` walk HR-011 names as its alternative technique. (2)
`delete_file`'s nested-`.git` scan (`path.rglob(".git")`, H4) still walks the
resolved path string rather than the pinned `dir_fd` — a narrower,
already-accepted race matching H4's own scope (a TOCTOU in the scan-then-delete
pattern itself, not the repository-escape race this task targets), immediately
preceding the `dir_fd`-anchored `rmtree` that this task's fix does cover.

**Verified with sustained concurrent races, not single attempts, per the task's
own bullet** — a background thread continuously flips a directory between a
real directory and a symlink pointing outside the repository (standing in for
the container racing the daemon via `exec`) while the main thread hammers
`read_file`/`write_file`/`delete_file` for 2000 iterations each. Verified each
race genuinely reproduces the original defect by reverting the fix and
confirming: the read race leaked host content on iteration 134 (not the first
attempt — genuinely intermittent, which is why a single-attempt test would have
been insufficient); the write race escaped on iteration 7; the delete race
destroyed the host file on iteration 52. Restored the fix; reran all three;
zero leaks/escapes/deletions across 2000 iterations each, and the escaped
directory left completely empty afterward in both cases (confirmed by listing
it, not just checking the specific filename).

**New tests:** `tests/filesystem/test_service.py` — a shared `_symlink_flipper`
fixture (the background-thread race harness above) and three tests
(`test_at_hat009_read_file_never_returns_content_from_outside_the_repository`,
`..._write_file_never_lands_outside_the_repository`,
`..._delete_file_never_deletes_outside_the_repository`), each independently
confirmed to fail against the pre-fix code.

### H10 — An escaping symlink does not break enumeration ✅
**Satisfies:** HR-012 · **Verified by:** HAT-010
**Commit:** `5a7b62b`

- [x] Catch `PathOutsideRepositoryError` alongside `PathGitMetadataError` in
      `list_files` and `search_files`
- [x] Report escaping and dangling symlinks as entries, as `_describe`'s
      docstring already promises
- [x] Test with a `.venv/bin/python`-style link, the common real case

**Implementation.** `_is_git_metadata` (`list_files`'s per-candidate filter)
now catches `PathOutsideRepositoryError` too, treating it as "not excluded" —
`_describe` is what actually reports the entry, which is HR-012's real
requirement, not this function's job. `search_files` gained the same catch at
its own, separate `security.resolve()` call site, treating it as `continue`
(skip) instead — a search has no reason to read *through* an escaping symlink
into host content, so it's dropped the same way a `.gitignore`-excluded file
already is, not reported as a match.

**`_describe`'s own docstring turned out to overclaim, caught while fixing
this.** It already said an escaping symlink was "reported as an entry, but
with no stat-able size/mtime" via the `is_symlink and not path.exists()`
branch — but `path.exists()` *follows* symlinks, so that branch only ever
fired for a **dangling** target, never one that escapes to somewhere that
genuinely exists (confirmed directly: `Path.exists()` on a symlink to a real
file outside the repo returns `True`). An escaping symlink to a real target
therefore fell through to `path.stat()`, which followed it and reported the
**host target's own real size and mtime** — a real, if minor, information
disclosure the docstring's own claim had been masking. Fixed by checking
`self._security.is_within_root(path.resolve())` (the same primitive H9 added)
alongside the existence check, so both the dangling case and the
escapes-to-something-real case take the no-stat-info branch; an ordinary
in-repo symlink is unaffected (still reports its real target's stat, unchanged
behavior, no existing test depended on the disclosure case either way).

**Verified against the exact reproduction, both fixed and reverted.** Built a
repo with a normal file, a symlink escaping to a real host file, and a
dangling symlink; confirmed `list_files`/`search_files` both succeed with the
fix (correctly reporting the escaping symlink with `size=None`/`modified_at=None`,
and the search correctly matching the in-repo file while never reading through
the symlink) and both crash with `PathOutsideRepositoryError` on the reverted
code — including, unexpectedly, the **dangling** case too: `_is_git_metadata`
calls `resolve()` before `_describe` is ever reached, and `resolve()`'s
containment check fires regardless of whether the target exists, so the
pre-fix code was already broken for a dangling symlink pointing outside the
repository, not only the escapes-to-something-real case the docstring
described. All three reverted variants failed with the same
`PathOutsideRepositoryError`; restored, reran, all pass.

**New tests**, all in `tests/filesystem/test_service.py`: an escaping symlink
to a real target (asserting the listing succeeds, the entry is reported
correctly, no host metadata leaks, and the rest of the listing is
unaffected); a dangling symlink (the same, without a real target); and a
`search_files` test proving it matches real in-repo content while skipping
the escaping symlink rather than crashing or reading through it.

---

## Phase 5 — Integrity of reported guarantees

### H11 — Error messages are redacted ✅
**Satisfies:** HR-013 · **Verified by:** HAT-011
**Commit:** `5169c6e`

- [x] Redact `payload["message"]`, not only `details`
- [x] Fix redaction of list values (the key hint is currently dropped),
      non-string scalars, and `scheme://user:pass@host` userinfo
- [x] Cover the YAML-echo path — a malformed `.mcp-workspace.yml` currently
      echoes the offending source line verbatim

**Defect:** `errors.py:248–259, 273–280`. Reproduced: a `ghp_` token in
cleartext in `message` while `details.url` was correctly `[REDACTED]`.

**Implementation.** `to_tool_error` now redacts `message` the same way it
already redacted `details` — applied once, universally, at the interface
layer, so every one of the many raise sites that interpolate raw Git/SSH
stderr or a full remote URL into `message` is covered without touching them
individually. `_redact` itself gained three fixes: the list branch now
carries `key=key` through its recursive call (a list under a sensitive key —
`{"password": ["hunter2"]}` — used to lose that hint entirely); the key-hint
check now runs regardless of the value's type, not only inside the
`isinstance(value, str)` branch (a non-string scalar under a sensitive key —
`{"secret": 12345}` — used to pass through unredacted); and every string
value, not only ones already flagged secret, now passes through a new
`_URL_USERINFO_RE` substitution that masks just the credential portion of a
`scheme://user:pass@host` URL, preserving the rest (host, path) as genuinely
useful diagnostic context rather than nuking the whole string.

**A GitHub-token-shaped credential in a URL was already caught** by the
existing `gh[pousr]_...` pattern's substring search before this task — the
userinfo regex specifically closes the gap for a plain, non-token-shaped
password (`https://user:hunter2@host`), which matched none of the existing
value patterns and has no key-name hint to catch it either.

**The YAML-echo path needed no separate code change** — `project_config.py`'s
`ContainerCreateFailedError` already flows through the same
`to_tool_error`, so making `message` redaction universal there closed this
path for free. Verified directly rather than assumed, and this surfaced a
second, genuine finding along the way: **PyYAML's own error formatting
truncates a long offending line across two separate `"..."`-bounded
excerpts**, splitting a sufficiently long secret so no single, contiguous
match ever appears in the raw message at all — independent of this task's
redaction. My first test used a 36-character token and passed even against
the *unfixed* code, for the wrong reason (PyYAML's own truncation, not this
task's redaction, happened to hide it). Caught by checking the *reverted*
code's raw message directly rather than trusting a passing test; fixed by
choosing a token short enough (empirically confirmed: whole up to ~28
characters past `ghp_` here, split across two excerpts at 32) to stay within
one un-truncated snippet, so the test genuinely exercises this task's fix
rather than an unrelated formatting accident.

**Verified the fix actually verifies something**, including that near-miss:
reverted `errors.py` alone and reran every new test — all failed as expected
(the corrected YAML test with the real token substring visible in the
un-redacted message; the four `errors.py`-level tests with plain
`AssertionError`s showing the unredacted values). Restored the fix; reran;
all pass, and the pre-existing 54 error/audit tests are unaffected.

**New tests:** `tests/test_errors.py` — a list-under-sensitive-key case, a
non-string-scalar case, a URL-userinfo case, and a `to_tool_error`
message-redaction case using the literal exploit shape from the review
(`token=ghp_...` interpolated into `message`, alongside an unrelated userinfo
URL in `details`, both redacted). `tests/test_project_config.py` — the
YAML-echo path through the real `load_project_config`/`to_tool_error` pair,
with the token-length caveat above documented in the test itself.

### H12 — The validated push destination is the one used ✅
**Satisfies:** HR-014 · **Verified by:** HAT-012
**Commit:** `e2040d2`

- [x] Resolve with `git remote get-url --push`, or push to the validated URL
      rather than the remote name
- [x] Validate both URLs where fetch and push differ
- [x] Assert the audit record names the destination actually used

**Defect:** `git/remote.py:48–65`, `git/push.py:96–100`. Reproduced: validation
approved `owner/repo` while the commit landed elsewhere and the audit record
named the wrong destination.

**Implementation.** `git/remote.py` gained `resolve_remote_push_url` (`git
remote get-url --push <name>`, host-side, mirroring `resolve_remote_url`),
and `validate_remote_identity`'s old body was extracted into a new shared
`_validate_url_identity` helper taking an already-resolved URL string, so
the six §20.2 checks (transport, owner/repo form, allowlist, caller-supplied
`expected_remote_repository`) run identically regardless of which URL —
fetch or push — is being checked. `validate_remote_identity` (used by
`git_pull`, fetch-only, unchanged) calls the helper once against the fetch
URL. The new `validate_remote_identity_for_push` (used by `git_push`) calls
it against the fetch URL first, then — only if the push URL differs from
the fetch URL — calls it again against the push URL, returning *that*
result. `tools/git.py`'s `git_push_tool` now calls
`validate_remote_identity_for_push` instead of `validate_remote_identity`,
and its single `remote_repository` local (fed into both the audit event's
`metadata["remote_repository"]` and `GitPushOutput.remote_repository`) now
comes directly from that call — satisfying the third bullet by
construction, since there is no longer any separate fetch-only-derived
value in scope to accidentally name instead.

**Empirically verified real Git behavior before trusting the claim, twice.**
First, that `git remote get-url` and `git remote get-url --push` genuinely
return different strings once `remote.<name>.pushurl` (or
`url.<base>.pushInsteadOf`) is configured — confirmed against git 2.43.0 in
a real repository. Second, before writing any test, reproduced the *exact*
original exploit manually against two real repositories: (a) a
`pushurl` pointed at a different host entirely
(`https://evil.example.com/evil/other.git`) — old `validate_remote_identity`
wrongly approved `owner/repo` (the fetch URL's identity); new
`validate_remote_identity_for_push` correctly rejected with the push URL's
own scheme/identity failure. (b) a `pushurl` pointed at a same-host,
different-repo destination (`https://github.com/owner/different-repo.git`)
— new function correctly rejected with `resolved: 'owner/different-repo'`
not matching the configured identity.

**A self-caught bug before it ever ran:** the first draft of
`validate_remote_identity_for_push`'s `push_url == fetch_url` fast path
re-derived `normalize_to_owner_repo(fetch_url)` a second time and asserted
the result was not `None` — the exact `assert`-in-production-code
anti-pattern this session's own earlier review flagged (stripped under
`-O`; `AssertionError` isn't a `WorkspaceError` the error-translation layer
handles). Fixed before running anything: `_validate_url_identity`'s return
value is now captured once (`fetch_normalized`) and returned directly on
the fast path, eliminating both the redundant re-derivation and the assert.

**Verified the new tests actually catch the regression.** Reverted
`git/remote.py` and `tools/git.py` to their pre-H12 versions and reran the
new tests: all 6 new unit tests in `tests/git/test_remote.py` failed with
`ImportError` (the function doesn't exist yet on the reverted code — the
strongest possible "this test depends on the fix" signal), and the new
end-to-end test in `tests/workspace/test_git_push_tool.py` failed
differently but just as tellingly: instead of raising `GIT_REMOTE_NOT_ALLOWED`
before Git ever ran, the *old* code let the push attempt proceed all the way
to a real SSH connection to the (unauthorized) `pushurl` host, failing only
on host-key verification — direct proof the pre-fix code does not stop the
push before it reaches the network. Restored the fix; all 7 new tests pass
again; no regressions in the 53 pre-existing tests across
`tests/git/test_remote.py`, `tests/workspace/test_git_push_tool.py`,
`tests/workspace/test_git_pull_tool.py` (now 162 total across that broader
run).

**New tests:** `tests/git/test_remote.py` — `resolve_remote_push_url`
reading a configured `pushurl` and falling back to the fetch URL when unset;
`validate_remote_identity_for_push` rejecting a cross-host redirect and a
same-host different-repo redirect, accepting a redirect that resolves to an
allowlisted identity (asserting the *returned* identity is the push
destination's, not the fetch URL's), accepting the ordinary matching-pushurl
and no-pushurl-configured cases, and checking `expected_remote_repository`
against the actual push destination rather than the fetch URL.
`tests/workspace/test_git_push_tool.py` — one real-Docker end-to-end test
reproducing the `pushurl`-redirect exploit through the actual `git_push` MCP
tool (`test_h12_pushurl_redirect_to_an_unauthorized_repo_is_rejected`),
mirroring the existing `test_at_009_unauthorized_remote_is_rejected` pattern.
A genuine success-path end-to-end test (pushing through an allowlisted
`pushurl` redirect all the way to a real accepted push) isn't reachable in
this suite without a provisioned SSH key (D3, blocked) — the same
constraint the file's own module docstring already documents for the
ordinary authenticated-push case; the unit-level tests cover that path
directly instead.

### H13 — Denials are audited at §20.7 fidelity ✅
**Satisfies:** HR-015
**Commit:** `8d55a19`

- [x] Include the policy key from `PolicyDeniedError.details["policy"]`
- [x] Include `repository_id`, and `remote`/`branch`/`remote_repository` where
      applicable
- [x] Add `refspec` and `remote_protection_status` to `git_push` success records
- [x] Do not let an unwritable audit path replace a denial with a crash

**Defect:** `tools/git.py`'s `audit_denials` recorded only `{policy_decision,
error_code}` per denial, discarding `PolicyDeniedError.details["policy"]`
entirely and never including `repository_id` — a denial record could not say
*which* policy fired or *which* repository it was about, unlike every
success record alongside it. `write_audit_event` also let a plain `OSError`
(an unwritable `audit.path`) propagate uncaught, which — raised from inside
`audit_denials`'s own exception handler — would replace a correctly-computed
denial with an unrelated crash.

**Implementation.** `audit_denials` now takes a `manager` parameter (the
three `register()` call sites pass `manager=manager`) and, on catching a
`WorkspaceError`, resolves `repository_id` via a best-effort
`manager.get(workspace_id).repository_id` lookup (wrapped in
`contextlib.suppress(WorkspaceError)` — a lookup failure, e.g. an
already-nonexistent `workspace_id`, still lets the *original* denial get
audited, just without a `repository_id`) when the raise site didn't already
supply one in `details`. `policy`, `remote`, `branch`, and
`remote_repository` are pulled from `exc.details` first, falling back to
the tool's own resolved kwargs (`remote`/`branch` are always present as
call kwargs per the MCP framework's own invocation convention, already
documented in this decorator's docstring) — `_DENIAL_DETAIL_FIELDS` names
exactly these four. Several raise sites in `git_commit_tool`/`git_push_tool`
gained a `"branch": target_branch` (or `status.branch`) entry in their
`details` where it wasn't already present, and the post-remote-validation
failure raises in both `git_pull_tool` and `git_push_tool`
(`CommandFailedError`/`GitNonFastForwardError`, four sites total) gained
`"remote_repository": remote_repository` — correct "where applicable"
behavior falls out naturally: a denial raised *before* remote validation
genuinely has no `remote_repository` to report, and none of the tests below
expect one there.

`git_push_tool`'s success `write_audit_event` call gained `"refspec":
refspec` and `"remote_protection_status": remote_protection.status`; the
latter required hoisting the previously return-statement-only
`to_report_output(_load_stored_remote_protection(...))` call one step
earlier into a shared `remote_protection` local, reused for both the audit
metadata and `GitPushOutput.remote_protection` — the same "single value
feeds both the audit record and the response" construction H12 used for
`remote_repository`, so the two SHALL NOT be able to name different values.

`write_audit_event` (`audit.py`) now wraps its `mkdir`/`open`/`write` in
`try/except OSError`, printing a one-line diagnostic to stderr (the
conventional channel for an MCP server over stdio — stdout carries the
JSON-RPC stream) and returning normally instead of propagating. This
protects every caller, not only `audit_denials`: an already-completed
`git_commit`/`git_pull`/`git_push` should not turn into a client-visible
crash just because the audit log itself couldn't be written.

**Verified the new tests actually catch the regression.** Reverted
`tools/git.py` and `audit.py` to their pre-H13 versions and reran the new
tests: all 4 new end-to-end tests in `tests/workspace/test_audit_logging.py`
failed (missing `policy`/`repository_id`/`branch` keys, `AssertionError:
assert None == 'example'` for the repository_id lookups that don't exist
yet), and the new unit test in `tests/test_audit.py` failed with an
unhandled `FileExistsError` propagating straight out of
`write_audit_event` — exactly the crash this task exists to prevent.
Restored the fix; all pass again; no regressions across the broader
191-test run spanning `tests/test_audit.py`,
`tests/workspace/test_audit_logging.py`, `tests/workspace/test_git_commit_tool.py`,
`tests/workspace/test_git_push_tool.py`, `tests/workspace/test_git_pull_tool.py`,
`tests/git/`.

**A genuine, documented gap:** `git_push`'s success path (and therefore its
success audit record's `refspec`/`remote_protection_status`) has no
end-to-end test reaching an actual accepted push — the same D3 (SSH key
provisioning, blocked) limitation `tests/workspace/test_git_push_tool.py`'s
own module docstring already documents for every other success-path test in
that file. An `insteadOf`-rewrite attempt to fake an offline success target
was tried and abandoned: `git remote get-url`/`get-url --push` themselves
apply `url.<base>.insteadOf` rewriting before returning, so the daemon's own
`validate_remote_identity_for_push` would see the rewritten (non-GitHub)
URL and correctly reject it — confirmed empirically rather than assumed.
Correctness here instead rests on the same "single value feeds both records"
construction verified directly by reading the code and confirmed by
`mypy`/`ruff`: `refspec` and `remote_protection` are each computed once and
passed to both the audit call and the return value, so they cannot diverge.

**New tests:** `tests/test_audit.py` — `write_audit_event` survives a
real `OSError` (a file sitting where a parent directory needs to be
created) without raising, and reports it to stderr rather than staying
silent. `tests/workspace/test_audit_logging.py` — a policy denial's audit
record carries the firing policy key; carries `repository_id` (top-level)
and `branch`; a non-policy denial (`GIT_REMOTE_NOT_ALLOWED`, raised before
remote validation) carries `repository_id` and `remote` but correctly
omits `remote_repository`.

### H14 — Authorization is re-checked against live configuration ✅
**Satisfies:** HR-016 · **Verified by:** HAT-013
**Commit:** `37f28ab`

- [x] Validate the resolved repository against the live `RepositoryRegistry` on
      every workspace-scoped call
- [x] Test that removing a repository from config immediately revokes access
      through its stale `workspace_id`

**Defect:** `workspace/manager.py:99–123` resolves `workspace_id → canonical_path`
from the database with no registry consultation, so a repository removed from
configuration remains fully accessible through its stale rows. Reproduced
against a registry authorizing nothing.

**Scope, established first.** The CLI's own operator path (`cli.py`'s `reset`/
`rebuild`/`delete`) already requires `registry.get(repository_id)` to succeed
*before* it ever constructs a `DockerWorkspaceManager` — each invocation loads
config fresh, so it has no "stale in-memory registry" problem to begin with,
and those commands take a `repository_id` directly, never a `workspace_id`.
The defect is specific to the long-running MCP server: `RepositoryRegistry` is
built once at startup, and every workspace-scoped *tool* call resolves a
client-supplied `workspace_id` against the state DB with no re-check against
that registry. The fix is scoped to the tool layer only — `DockerWorkspaceManager`
itself and the CLI are untouched.

**Implementation.** Two shared chokepoints already existed for exactly this
kind of cross-cutting concern, so the fix lives entirely in
`workspace/manager.py`, with no changes to any tool's actual logic:

- `resolve_repository_root(db, workspace_id, registry)` — a new required
  `registry` parameter. Still returns the *persisted* `canonical_path` (what
  the workspace's container was actually bind-mounted against at open time),
  not the registry's own copy — this is authorization (is `repository_id`
  still allowed at all), not path reconciliation; a live edit to a
  repository's `path` without removing it is a different, unaddressed
  concern, and conflating the two risked returning a canonical_path that
  doesn't match what's actually mounted in the container. Used directly by
  the three host-side Git reads (`git_status`/`git_diff`/`git_log`) and by
  `git_commit`/`git_pull`/`git_push`'s own precondition reads, and indirectly
  by all six filesystem tools through `tools/filesystem.py`'s existing
  `_resolve_repository_root` wrapper — one signature change covers every
  caller.
- `get_authorized(manager, workspace_id, registry)` — new, for the
  `DockerWorkspaceManager.get(workspace_id)` call sites that need a
  `WorkspaceRecord` (state, container id, mode), not just a path: `exec`/
  `exec_start` (`tools/execution.py`), `workspace_info` (`tools/workspace.py`),
  and `git_commit`/`git_pull`/`git_push`'s own `record = manager.get(...)`
  calls (`tools/git.py`). Deliberately a free function wrapping `manager.get`
  rather than a change to `DockerWorkspaceManager.get` itself — the CLI also
  calls `.get()` (via `reset`/`rebuild`/`delete`), and per the scope note
  above those commands are already correctly gated on the registry one layer
  up; making `.get()` itself registry-aware would have been redundant there
  and would have coupled `DockerWorkspaceManager` to a `RepositoryRegistry`
  it doesn't otherwise need.

`registry: RepositoryRegistry` is now a required parameter of
`tools/filesystem.py`'s and `tools/execution.py`'s `register()` (git.py's and
workspace.py's already had it); `server.py`'s `_register_tools` passes it to
both.

**A test discipline finding, not a code bug:** the first draft's revert
check showed `workspace_info` and `git_push` "passing" even against the
reverted, pre-fix code — not because the fix was unnecessary there, but
because both already touch `registry.get()` for an *unrelated* reason
(`workspace_info`'s `_info()` needs the live `canonical_path` for git state;
`git_push`/`git_pull` already read `repository_record.github` for remote
validation). Those incidental calls happen to enforce the same invariant by
accident. Caught by reading the revert failure output closely rather than
trusting "6/6 passed against reverted code" at face value — replaced the
`git_push` test with a `git_commit` one (genuinely isolates
`get_authorized`'s effect: `git_commit` never touches the registry anywhere
else), and documented the `workspace_info` case's incidental-coverage nuance
directly in that test rather than deleting it (HAT-013 cares about the
*behavior*, and it's honest, correct behavior regardless of mechanism).

**Verified the new tests actually catch the regression**, this time for the
right reason: reverted all six changed source files to their pre-H14
versions and reran `tests/workspace/test_authorization_recheck.py` — the four
tests that genuinely depend on this task's fix (`exec`, one representative
filesystem tool, `git_status`, `git_commit`) failed, each with the *original*
resolution succeeding where it should have been rejected; the two tests that
were never expected to distinguish reverted from fixed code (`workspace_info`,
and the negative-space "an unrelated removal doesn't affect a still-configured
repository" check) still passed, as documented. Restored the fix; all six
pass again. Broader relevant suite (`tests/workspace/`, `tests/filesystem/`,
`tests/git/`) — 489 passed, no regressions.

**Two genuinely self-inflicted false starts along the way, neither a code
defect:** running the new test file directly while a background full-suite
run was still active produced real 409 container-name conflicts (this
project's deterministic `mcp-dev-ws-<repository_id>-<hash>` naming makes any
two concurrent processes sharing a `repository_id` collide) — resolved by
never running Docker-touching commands concurrently with each other for the
rest of this task, and by cleaning up an unrelated container left behind by
an ad-hoc manual reproduction script that (unlike the test suite) has no
fixture teardown of its own.

**New tests:** `tests/workspace/test_authorization_recheck.py` — six
end-to-end tests, each opening a workspace against one server, then building
a *second* server from the same `state_db_path` with the repository removed
from configuration (simulating an operator edit + restart), then reusing the
original `workspace_id` against the second server: `workspace_info`, `exec`,
a representative filesystem tool (`list_files`), `git_status`, and
`git_commit` all fail closed with `REPOSITORY_NOT_ALLOWED`; a control test
confirms removing a *different*, unrelated repository leaves a
still-configured one's workspace fully usable. Also fixed two pre-existing
tests (`tests/workspace/test_execution_tools.py`,
`tests/workspace/test_async_execution_tools.py`) that constructed
`execution_tools.register(...)` directly and needed the new required
`registry` argument.

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
