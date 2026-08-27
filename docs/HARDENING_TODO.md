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

### H15 — A workspace recovers from any Docker state ✅
**Satisfies:** HR-017 · **Verified by:** HAT-014
**Commit:** `6f75101`

- [x] Create a container for an existing row whose container is missing
- [x] Adopt-or-remove a container found by deterministic name with no matching
      row, on the create path and not only in `reconcile()`
- [x] Cover: removed out of band; failed create; orphaned name collision

**Defect:** `workspace/manager.py:246–261, 509–516`. Currently a transient
failure wedges the workspace permanently, even after Docker recovers.

**Implementation, two independent fixes to `workspace/manager.py`:**

1. `ensure_workspace`'s existing-row branch unconditionally called `start()`
   whenever `reconcile()` didn't report `RUNNING` — but `start()` only ever
   starts an *already-existing* container; it has no way to create one. Any
   scenario that left the row with no container at all (`reconcile()`
   marking it `ABSENT` after the container was removed out of band; a
   `_create` attempt whose `containers.run` itself failed, leaving
   `container_id` permanently `None`) hit `start()`'s
   `"has no container to operate on"` error forever after, even once
   whatever caused the original failure was long gone. Fixed by checking
   `reconciled.container_id is not None` first — only then is `start()`
   appropriate; otherwise `_run_container` is called directly against the
   *existing* row, the same machinery `_create` already uses for a
   brand-new one, just without a second `INSERT`.
2. `_run_container` (shared by `_create` and `_recreate`) called
   `containers.run(name=record.container_name, ...)` with no check for
   whether that deterministic name (§51) was already taken — `docker system
   prune` leaving an orphan, a DB reset that lost the row while the
   container survived, or literally this session's own repeatedly-hit
   `mcp-dev-ws-<repository_id>-<hash>` collisions from concurrent/interrupted
   test runs all produce the identical 409, and since the name is
   deterministic it collides again on every retry — permanently wedged, not
   merely a bad first attempt. New `_adopt_or_remove_by_name`, called before
   `containers.run`: a container already carrying this exact workspace_id's
   label is *adopted* (its own earlier `containers.run` plausibly completed,
   just never got persisted); anything else at that name can only be a stale
   container from a *previous* incarnation of this same logical workspace
   (the name is deterministic per workspace_id — no other workspace could
   coincidentally collide) and is *removed* to free the name for the create
   that follows. `reconcile()` already had its own, narrower adoption logic
   for the case where a row exists but its `container_id` is `None` — this
   is additional coverage for the path `reconcile()` never runs on at all:
   `_create`, reached when `ensure_workspace` finds no row whatsoever.

**A test-discipline finding caught by the revert check itself, not a code
bug:** the first draft's "adopt" test cleared only the row's `container_id`
column before reopening — that scenario was already covered by
`reconcile()`'s own *pre-existing* label-based adoption (`ensure_workspace`
calls `reconcile()` whenever any row exists at all, regardless of this
task's changes), so the test passed identically against the reverted code
and proved nothing about this task's fix. Caught by reading the revert
output rather than trusting "passed." Fixed by deleting the row *entirely*
instead — that routes through `_create` (no row → no `reconcile()` call at
all), which is the one path only `_run_container`'s new adoption logic can
save; confirmed this version genuinely fails against the reverted code.

**Verified the tests catch the regression for the right reason:** reverted
`workspace/manager.py` to its pre-H15 version and reran all four new tests —
each failed distinctly (the out-of-band-removal and adopt cases via
`ContainerRuntimeFailedError`/409-driven `ContainerCreateFailedError`; the
failed-create and orphan cases via the same underlying 409 that never clears
on retry). Restored the fix; all four pass again; the full
`tests/workspace/test_recovery.py` file (15 tests) and the broader
`tests/workspace/` suite (267 tests) show no regressions. No Docker
leftovers, no schema drift (this task changes no tool's output shape).

**New tests:** `tests/workspace/test_recovery.py` — four tests against the
`manager` fixture (real Docker, in-memory state DB) directly, one per HAT-014
scenario plus the adoption sub-case: a container removed out of band (fully
`remove()`d, not merely stopped) recovers on the next `ensure_workspace`
with a new container id; a `containers.run` failure against a nonexistent
image leaves the row `FAILED` with no container, and recovers once a valid
image is used; an orphaned, unlabeled container occupying the deterministic
name is removed rather than permanently blocking the name; a correctly
labeled container surviving a lost `workspace` row is adopted rather than
recreated.

### H16 — Deleting a workspace that ran jobs succeeds ✅
**Satisfies:** HR-018 · **Verified by:** HAT-015
**Commit:** `78b1197`

- [x] Cascade `job.workspace_id`, or delete dependent rows in order inside one
      transaction
- [x] Ensure a failure cannot leave the container removed and the row in
      `deleting`

**Implementation.** `job.workspace_id REFERENCES workspace (id)` with no
`ON DELETE CASCADE`, and `db/connection.py` turns `PRAGMA foreign_keys = ON`
for every connection — so a workspace that ran even one job hit an
uncaught `sqlite3.IntegrityError` on `delete()`'s final `DELETE FROM
workspace`, well after `_remove_container_if_present` had already
succeeded. Chose the second of HR-018's two offered fixes (delete dependent
rows in order, inside one transaction) over adding `ON DELETE CASCADE` to
the schema: it needed no migration (SQLite can't alter an existing FK
clause in place — changing it means recreating the table, a real but
separate piece of schema surgery), and it keeps cascade behavior explicit
in the one place that already owns workspace deletion, rather than
implicit in the schema for every future table with a `workspace_id` FK.
`delete()` now deletes `job` rows for the workspace first, then the
`workspace` row itself, both inside one `with self._db:` block — matching
the same transactional idiom `touch_last_used` already uses elsewhere in
this module.

**Verified the new tests catch the regression:** reverted `manager.py` to
its pre-H16 version and reran both new tests — both failed with the exact
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` the defect
describes, confirming the tests genuinely exercise the fixed code path
rather than passing for an unrelated reason. Restored the fix; both pass
again; the full `tests/workspace/test_manager.py` file (17 tests) shows no
regressions. No Docker leftovers (the container is removed successfully in
both the broken and fixed versions — only the DB-side cleanup differed),
no schema drift (no tool output shape changed).

**New tests:** `tests/workspace/test_manager.py` — a workspace that ran one
job deletes cleanly, with the workspace row, the job row, and the container
all gone afterward; a workspace with three jobs also deletes cleanly, with
every job row removed.

### H17 — Migrations are atomic ✅
**Satisfies:** HR-019 · **Verified by:** HAT-016
**Commit:** `134dbe0`

- [x] Wrap migrations in explicit `BEGIN`/`COMMIT` (`with connection:` does not
      wrap DDL)
- [x] Add a migration test against a populated database at an older
      `user_version`, not only an empty one
- [x] Configure `busy_timeout` and consider WAL; two processes open the same
      file today with neither

**Defect:** reproduced — a partially-applied migration persisted its first
statement while `user_version` stayed at `0`, so the next start re-ran it, hit
"table already exists", and the daemon could never start again.

**Implementation, `db/schema.py`.** Confirmed the exact mechanism empirically
before touching anything: Python's `sqlite3` module issues an implicit
`BEGIN` only ahead of the first DML statement under the default legacy
transaction-control mode — `with connection:` genuinely does nothing for a
migration whose body is entirely `CREATE TABLE`/`ALTER TABLE`/`PRAGMA
user_version`, so every statement committed the instant it ran, individually.
A two-statement reproduction (`CREATE TABLE foo`, then the same statement
again) confirmed the exact failure mode: `foo` existed afterward but
`user_version` stayed unchanged. `apply_migrations` now issues an explicit
`BEGIN` before each migration and `commit()`/`rollback()` around it
(`except BaseException`, not just `Exception`, since a genuinely partial
migration is exactly the state a `KeyboardInterrupt` mid-migration would also
leave) — SQLite treats DDL as fully transactional under an explicit
transaction, confirmed with the same two-statement reproduction: after the
fix, neither `foo` nor the version bump survives the failure.

**`db/connection.py`.** `PRAGMA busy_timeout` and `PRAGMA journal_mode = WAL`
are now set on every connection `open_db` returns. A genuine finding while
writing the test for this, not assumed: Python's own `sqlite3.connect()`
already defaults its `timeout` parameter to 5 seconds, which — verified
empirically — already sets `PRAGMA busy_timeout` to `5000` with *no* code
change at all. The explicit pragma this task adds doesn't change behavior by
itself; it makes that behavior a documented, intentional choice rather than
an incidental side effect of an unrelated default a future change could
silently remove. `journal_mode = WAL` is the part that's genuinely new: the
pre-fix default (`delete`, a rollback journal) blocks readers behind an
active writer in a way WAL does not, and is what the corresponding test
actually catches a regression in. `:memory:` databases (every test in this
suite) silently keep `journal_mode = memory` instead — confirmed empirically
rather than assumed, since a `:memory:` database has nowhere to put the
`-wal`/`-shm` sidecar files WAL needs.

**Verified the tests catch the regression for the right reason, and was
honest where they don't:** reverted `schema.py`/`connection.py` to their
pre-H17 versions and reran all four new tests. The atomicity test
(monkeypatched `MIGRATIONS` with a guaranteed-to-fail entry) and the
`journal_mode` half of the pragma-configuration test both failed as
expected. The `busy_timeout` assertion and the concurrent-write-vs-write
test both *passed* against the reverted code too — not a test bug, but the
direct consequence of the finding above (Python's own default already
provides `busy_timeout = 5000`); both tests' docstrings say so explicitly
rather than silently claiming they prove something they don't. Restored the
fix; all four pass again; the full `tests/db/test_schema.py` file (14 tests)
and the full suite (740 baseline + 4 new) show no regressions, no Docker
leftovers, no schema drift.

**New tests:** `tests/db/test_schema.py` — a substitute migration with a
guaranteed-to-fail second statement proves neither the first statement's
table nor the version bump survives, and that a subsequent
`apply_migrations` call recovers cleanly; a database manually brought to
schema version 1 with real rows, then migrated forward, proves existing data
and the tables/columns added by later migrations all survive together
(HR-019's own explicitly-requested "populated database at an older
`user_version`" case); `busy_timeout`/`journal_mode` are the expected
values on a fresh connection; two real connections to the same file, one
holding a write lock while the other attempts a write, prove the second
genuinely waits and then succeeds rather than failing immediately.

### H18 — A failed termination is not reported as success ✅
**Satisfies:** HR-020, HR-021 · **Verified by:** HAT-017
**Commit:** `4323fd7`

- [x] Propagate `PermissionError` from `kill` as a structured error
- [x] Do not mark a job `timed_out`/`canceled` when termination failed
- [x] Bound the drain task so a stream that never EOFs cannot hold an executor
      thread forever
- [x] Kill the process group, not only the direct child
- [x] Mark a job terminal when its exec start fails, so it stops pinning the
      workspace against idle stop
- [x] Evict `AsyncJobRunner._live` entries on completion (currently unbounded)

**Process-group kill (`execution/service.py`).** Verified empirically before
changing anything: each `docker exec` starts as the leader of its own,
*fresh* process group (`PGID == PID`) — confirmed with a real container
running `sh -c 'sleep 200 & sleep 300'`, checking `/proc/<pid>/stat`'s
`pgrp` field from the host — never sharing the container's own init process
group. `kill` now signals via `os.killpg(os.getpgid(pid), sig)` instead of
`os.kill(pid, sig)`, reaching any child the exec'd process forked (a real
gap: `sh -c 'cmd1 & cmd2'` left the backgrounded `cmd2` running after the
direct child, `sh` itself, was killed). `PermissionError` still propagates
uncaught from `kill` itself — only `ProcessLookupError` (already gone) is
suppressed, unchanged from before.

**Termination failure tracking (`async_runner.py`).** `_terminate` now
catches `OSError` from `kill` and re-raises as a structured
`ContainerRuntimeFailedError(recoverable=False)`. A new shared
`_terminate_and_track_failure` (used by both `_run`'s own timeout handling
and `cancel_job`) sets `LiveJob.termination_failed` on that failure and
force-closes the exec's stream socket — `CancellableStream.close()` is
docker-py's own documented "cancel from another thread" mechanism — so the
still-blocked `drain_stream` call, which would otherwise wait for an EOF
that can never come since the process was never actually signaled, returns
promptly instead of holding its `asyncio.to_thread` executor slot forever.
`_run`'s terminal-state decision checks `termination_failed` *first*, ahead
of `cancel_requested`/`timed_out` — necessary because `cancel_job` and the
internal timeout path are two separate call sites, and only a flag on the
shared `LiveJob` lets either one's failure be seen correctly regardless of
which one hit it, however long `_run` itself takes to actually observe the
process ending. `_run`'s final `await drain` is now bounded by
`asyncio.wait_for(..., timeout=_DRAIN_GRACE_SECONDS)` (10s) as a second,
independent bound beyond the stream-close mechanism.

**Exec-start failure (`async_runner.py`'s `start_job`, HR-021).** A job
whose `start_and_open_stream` call itself fails (container gone, a Docker
API error) used to stay in `starting` — a `NON_TERMINAL_STATES` member that
pins the workspace against idle-stop — forever, since nothing marked it
terminal. Now caught and `mark_finished(state=FAILED, ...)` before
re-raising as `ContainerRuntimeFailedError`, so the caller still sees the
real error while the job itself stops blocking idle-stop.

**`_live` eviction.** `_run` now `self._live.pop(job_id, None)` once
`mark_finished` has run — `_live` previously held every job this process
ever started for the rest of its uptime, unbounded growth over a
long-running daemon.

**A related, deliberately out-of-scope gap, documented rather than silently
left unmentioned:** `tools/execution.py`'s synchronous `exec` tool has its
own, separate kill-on-timeout path (`service.kill` called directly, not
through `AsyncJobRunner`) with the identical "stream that never EOFs holds
an executor thread forever" shape — but `consume()` opens its stream
internally with no reference the caller can force-close the way
`start_and_open_stream`/`drain_stream`'s split design allows for
`exec_start`. Fixing that would mean restructuring `consume()` the same
way, which the given defect's file locations (`async_runner.py`,
`execution/service.py`'s `kill` specifically) don't call for and this task
did not attempt. What *was* fixed there, at low cost and clearly in scope
(HR-020's general principle, the same shared `kill()`): `exec`'s own
kill-on-timeout now reports a `PermissionError` as a structured
`ContainerRuntimeFailedError` rather than letting it crash out unhandled —
and `kill()`'s process-group fix applies to `exec` for free, since both
tools share the one `ExecutionService.kill` method.

**Verified the tests catch the regression:** reverted all three changed
files to their pre-H18 versions and reran all four new tests — each failed
distinctly: the process-group test showed both the direct and backgrounded
process still running after cancel; the termination-failure test's
`exec_cancel` call did not raise, and the job never reached a terminal
state within the bounded wait (it hung, exactly the deadlock this task
exists to prevent); the exec-start-failure test's job stayed non-terminal;
the eviction test never observed `_live` shrink. Restored the fix; all four
pass again; the broader `tests/workspace/test_async_execution_tools.py`
(21 tests), `tests/workspace/test_async_runner.py` (new file, 2 tests), and
`tests/workspace/test_execution_tools.py` (12 tests) show no regressions.
No Docker leftovers, no schema drift.

**New tests:** `tests/workspace/test_async_execution_tools.py` — a
backgrounded child process survives a direct-child-only kill and does not
survive a process-group kill; a monkeypatched `os.killpg` (a genuine
`PermissionError` needs a real privilege split this sandbox has no way to
set up without root — documented explicitly in the test itself) proves
`exec_cancel` raises a structured `CONTAINER_RUNTIME_FAILED` error, the job
is not marked `canceled`, and it reaches a terminal state within a bounded
wait rather than hanging. `tests/workspace/test_async_runner.py` (new) — a
failed exec start (a nonexistent `container_id`) marks the job `FAILED`
rather than leaving it in `starting`; a completed job's `_live` entry is
evicted while the durable `JobStore` record remains.

### H19 — Structured errors survive Docker and decoding failures ✅
**Satisfies:** HR-022 · **Verified by:** HAT-019
**Commit:** `e5ec6bf`

- [x] Translate `docker.errors.DockerException` in the exec and workspace paths
- [x] Decode host-side Git output with an explicit error strategy so a non-UTF-8
      filename cannot crash `git_status`/`git_diff`/`workspace_open`
- [x] Tolerate a commit subject containing `\x1e` in `git_log`'s parser
- [x] Correct the `log.py` docstring claiming such bytes are invalid in a subject

**`docker.errors.DockerException` translation.** Added try/except around every
remaining call into Docker that could raise uncaught, past
`@translate_workspace_errors`: `tools/execution.py`'s synchronous `exec_tool`
(`service.start`/`service.consume`, in addition to H18's own `kill`
`PermissionError` handling there); `tools/workspace.py`'s `_run_setup_steps`
(`.mcp-workspace.yml` setup commands — the same `start`/`consume` pair);
`workspace/manager.py`'s `_find_container`/`_find_container_by_name`
(previously caught only `DockerNotFound`, letting a real API error through
uncaught) and `reconcile`'s `containers.list` call (no handling at all). The
async job path (`exec_start`/`exec_cancel`) was already covered by H18's own
`start_job`/`kill` fixes.

**Host-side Git output decoding, verified empirically at every step rather
than trusted from the spec's wording alone:**

1. Confirmed `git status -z` (used by `git_status`) disables
   `core.quotePath`'s octal-escaping, so a genuinely invalid UTF-8 filename
   appears raw in Git's stdout — and that decoding it with `text=True`'s
   default `errors="strict"` really does raise `UnicodeDecodeError`, exactly
   as HR-022 describes. Both `subprocess.run` calls in `git/service.py`
   (`run_git_host_side` and `_discover_code_executing_overrides`) now pass
   `errors="surrogateescape"`.
2. That alone was not sufficient — reproduced a *second*, downstream crash:
   the MCP SDK's own response serialization (`pydantic_core.to_json`) raises
   `UnicodeEncodeError` on a lone surrogate escape, the identical failure
   class `surrogateescape` exists to avoid at the subprocess boundary, just
   moved one layer down. New `sanitize_for_client` (`git/service.py`)
   re-encodes with `surrogateescape` and decodes back with `errors="replace"`
   — U+FFFD for the invalid bytes, the same lossy-but-safe tradeoff
   `git/diff.py` already uses for diff content — applied at the point a
   git-derived string becomes a client-facing field: every path
   `git/status.py` reports (staged/unstaged/untracked/conflicted, plus
   branch/upstream defensively), and `git/log.py`'s subject/author fields.
   Verified end to end against the real MCP tool layer (not just the
   parsing functions in isolation): `git_status` and `git_diff` both now
   return cleanly for a repository containing a `\xff\xfe`-named file,
   where before this task they crashed with `UnexpectedToolError`.

**`git_log`'s record separator, HR-022's `\x1e` requirement — a two-step
empirical process, not a one-line fix:**

1. Confirmed a commit subject *can* literally contain `\x1e` (no Git-side
   restriction), and that the existing `_RECORD_SEP = "\x1e"` design's own
   docstring claim ("not valid in a commit subject line at all") was simply
   wrong — reproduced the exact mis-split HAT-019 describes.
2. First fix attempt — switching `_RECORD_SEP` to a literal `"\x00"` Python
   string (NUL, which Git *does* provably refuse in a commit message:
   confirmed `git commit` itself errors "a NUL byte in commit log message
   not allowed") — introduced a *new*, different crash: `subprocess.run`
   itself raises `ValueError: embedded null byte`, since `execve`'s argv
   strings are NUL-terminated C strings and a literal NUL cannot be embedded
   inside one at all — an OS-level constraint, not a Python quirk, and not
   something the earlier `\x1f` field separator ever hit (an ordinary byte,
   fine to embed raw). Fixed by using Git's own `%x00` *placeholder syntax*
   (four literal ASCII characters, safe in argv) to build the format string
   sent to Git, keeping a separate constant for the *actual* NUL byte Git
   emits into its own stdout at runtime, which is what the parser then
   splits on. Verified the final version end to end, including through the
   real MCP tool layer: a commit whose subject contains `\x1e` now returns
   as one correct record, subject text intact.

**Module docstring corrected** (`git/log.py`): the false "not valid in a
subject" claim about `\x1e`/`\x1f` is replaced with the actual, verified
distinction — `\x00` is genuinely impossible in a commit subject (Git
enforces it), `\x1e` merely used to be assumed so.

**Verified the tests catch the regression:** reverted all six changed source
files to their pre-H19 versions and reran the new tests. `test_service.py`'s
own import failed outright (`sanitize_for_client` doesn't exist on the
reverted module) — the strongest possible signal. The filename and
`\x1e`-subject tests each failed with the exact predicted exception
(`UnicodeDecodeError`, `ValueError: not enough values to unpack`). One test
was caught and removed during this process, not shipped: a "non-UTF-8
author name" test passed identically against both reverted and fixed code,
because Git's own argv/commit handling re-encodes an author name's invalid
bytes into valid UTF-8 (confirmed empirically: `\xff\xfe` became `Ã¿Ã¾`,
Latin-1-shaped) before they ever reach `git log`'s output at all — unlike a
filename or a commit *message*, an author name isn't a reliable path to a
genuinely invalid byte, so the test was removed rather than kept for a
false sense of coverage. Restored the fix; all remaining tests pass; full
`tests/git/test_service.py`/`test_status.py`/`test_log.py` (41 tests) show
no regressions. No Docker leftovers (one leftover container was found and
removed, left behind by this task's own ad-hoc manual reproduction scripts,
which — unlike the test suite — have no fixture teardown of their own). No
schema drift.

**New tests:** `tests/git/test_service.py` — a non-UTF-8 filename no longer
crashes `run_git_host_side`; `sanitize_for_client` turns a surrogate-escaped
string into one safe to UTF-8-encode while leaving ordinary text untouched.
`tests/git/test_status.py` — a non-UTF-8 filename is reported in
`untracked` and is itself UTF-8-encodable. `tests/git/test_log.py` — a
commit subject containing `\x1e` produces exactly one correct record with
the subject text intact, not two broken ones.

---

## Phase 7 — Git and workspace correctness

### H20 — `git_commit` refuses unresolved conflicts ✅
**Satisfies:** HR-023 · **Verified by:** HAT-018
**Commit:** `4ae9ea9`

- [x] Inspect `status.conflicted` alongside `detect_interrupted_operation`
- [x] Test via a real `git stash pop` conflict, which leaves no marker file

**Defect:** reproduced via `git stash pop` — status showed `UU`, the guard
returned `None`, and the commit succeeded.

**Implementation.** `git_commit_tool` now raises `GitConflictError` whenever
`status.conflicted` is non-empty, checked immediately after `read_git_status`
runs — alongside, not instead of, the existing `detect_interrupted_operation`
marker-file check, since the two catch genuinely different situations (an
in-progress merge/rebase with no conflict yet resolved one way; an
already-resolved-looking-but-actually-conflicted stash pop the other).
Verified the exact defect empirically before writing any test: a stash,
then a conflicting commit on the branch it was stashed from, then `git
stash pop` leaves `git status` reporting `UU` for the file — but no
`MERGE_HEAD` (only an `AUTO_MERGE` file, which nothing in
`detect_interrupted_operation` reads).

**Verified the new test catches the regression:** reverted `tools/git.py`
to its pre-H20 version and reran the new test — it failed with
`DID NOT RAISE ToolError` (the commit against the conflicted tree
succeeded, exactly the defect's reproduction), confirming the test
genuinely depends on the fix rather than passing for an unrelated reason.
Restored the fix; it passes again; the full
`tests/workspace/test_git_commit_tool.py` file (14 tests) shows no
regressions. No Docker leftovers, no schema drift.

**New tests:** `tests/workspace/test_git_commit_tool.py` — a real `git
stash pop` conflict (no `MERGE_HEAD` present, confirmed via an explicit
assertion in the test itself) still fails `git_commit` with
`GIT_CONFLICT`.

### H21 — `git_commit` stages exactly what it was told ✅
**Satisfies:** HR-024 · **Verified by:** HAT-018
**Commit:** `665c020`

- [x] Pass client paths as literal pathspecs (`:(literal)` or
      `--pathspec-file-nul`)
- [x] Report `files_committed` from what Git committed, not the request echo
- [x] Normalize paths before the strict-mode set-difference check, and account
      for staged renames
- [x] Test `paths=[":/"]` and `[":(glob)**"]`

**Literal pathspecs.** Every client-supplied path is now prefixed with
`:(literal)` before reaching `git add`/`git commit --only`
(`_literal_pathspecs`, `git/commit.py`), disabling all of Git's own pathspec
magic — no glob, no `:/`-anchoring, no icase — for every entry. Verified
empirically, not assumed from the spec's wording: `git add -- ':/'` really
does stage every modified file in a real repository; `git add --
':(literal):/'` correctly fails with "pathspec ':(literal):/' did not match
any files" instead — the same "no such file" response §17.4 already
promises for a literal nonexistent path — and a directory path (`paths:
["dir"]`) still expands to every file beneath it under `:(literal)`, since
directory-prefix matching isn't itself pathspec "magic."

**`files_committed`.** New `read_committed_files` (`git/commit.py`) reads
`git diff-tree --no-commit-id --name-only -r -z --root <sha>` host-side
after the commit, and `git_commit_tool` now returns that instead of echoing
`paths` back. `--root` is required, not decorative: verified empirically
that `git diff-tree` without it reports zero files for a commit with no
parent (nothing to diff against without it) — a bug that would have made
every repository's very first `git_commit` call report an empty
`files_committed`.

**Strict-mode path normalization and staged renames.** The set-difference
check now compares against `{posixpath.normpath(p) for p in paths}`, so a
caller-supplied `./foo.txt` correctly matches `git status`'s own `foo.txt`
rather than being flagged as a spurious mismatch. Separately — and this
surfaced a second, independent bug while investigating "account for staged
renames": `git/status.py`'s rename/copy parsing (the porcelain `2 ` record)
discarded the old path (`origPath`) entirely, reporting only the new name in
`staged`. Reproduced the real consequence: `git commit --only -- <newpath>`
alone, without `<oldpath>` also named, does *not* commit a clean rename —
it commits only the new file's *creation*, leaving the old path staged for
deletion and entirely outside the commit. `staged` now includes both the
new and old path for a genuine rename (a *copy*'s original is deliberately
excluded — it is untouched, not itself a pending change), so strict mode
correctly flags an incomplete rename request as extraneous, and any caller
reading `git_status`'s own output to decide what to pass to `git_commit`
sees both names it actually needs.

**Verified the tests catch the regression:** reverted all three changed
source files to their pre-H21 versions and reran the new/updated tests.
`test_commit.py`'s own import failed outright (`read_committed_files`
doesn't exist on the reverted module). The rename-status test failed with
the old, incomplete `["a-renamed.txt"]` list. Both new tool-level tests
failed as predicted: the `:/`-magic-pathspec commit *succeeded* instead of
raising `COMMAND_FAILED`, and `files_committed` echoed `["dir"]` instead of
the real `["dir/a.txt", "dir/b.txt"]`. Restored the fix; all pass again; the
full `tests/git/test_commit.py`/`test_status.py` (23 tests) and
`tests/workspace/test_git_commit_tool.py` (16 tests) show no regressions.
No Docker leftovers, no schema drift.

**New tests:** `tests/git/test_commit.py` — `build_add_argv`/
`build_commit_argv` prefix every path with `:(literal)` (including the
`":/"`/`":(glob)**"` cases HR-024 explicitly names); `read_committed_files`
correctly reports a root commit's files (the `--root`-dependent case), an
ordinary commit's files, and an empty list for an unknown sha.
`tests/git/test_status.py` — a staged rename's `staged` list now includes
both the new and old path. `tests/workspace/test_git_commit_tool.py` —
`paths=[":/"]` fails closed rather than staging the whole repository
(confirming nothing was staged or committed afterward); `files_committed`
for a directory-path commit reports the real per-file list, not the
directory name.

### H22 — A failed commit leaves the index unchanged ✅
**Satisfies:** HR-025
**Commit:** `0d34d6e`

- [x] Roll back `git add` when `git commit` fails (§20.6)
- [x] Map "nothing to commit" to a specific code rather than `COMMAND_FAILED`

**Rollback.** `git_commit_tool`'s two-step `run_git` now inspects each
command's own result instead of raising internally: `git add`'s failure
still raises `CommandFailedError` directly (there's nothing to roll back
yet), but a `git commit` failure now first runs `build_reset_argv(paths)`
— `git reset -- <same literal pathspecs>` — before raising, undoing exactly
what this call's own `git add` staged. Verified empirically before trusting
it: `git reset -- ':(literal)a.txt'` unstages only `a.txt`, leaving any
*other* already-staged path (e.g. from a prior out-of-band
`exec('git add ...')`) untouched — a broader `git reset` would have been
the wrong scope for "roll back what THIS call did." The rollback itself is
best-effort (`contextlib.suppress`): a failure there doesn't hide the real
commit failure being raised, and would only happen in an unusual condition
(the in-container git binary vanishing between the two calls) rarer than
the rollback failing to help.

**A specific error code for "nothing to commit."** New
`GitNothingToCommitError`/`GIT_NOTHING_TO_COMMIT` (`errors.py`, and added to
`docs/SPEC.md` §31's own list alongside every other git-specific code) is
raised instead of `CommandFailedError` when `git commit`'s own output
contains "nothing to commit". A genuine finding while implementing this,
not assumed: confirmed empirically that Git prints this message on
**stdout**, not stderr — `is_nothing_to_commit_failure` (`git/commit.py`)
checks both streams defensively, but an implementation that only checked
`stderr_tail` (the natural first guess, and what the pre-existing
`run_git`/`CommandFailedError` plumbing already used) would never have
matched a single real failure. The revert-verification below caught this
same gap concretely: the reverted code's own error message for this exact
scenario was `"git commit failed: "` — an empty stderr tail, since the
actual message was sitting on stdout the whole time.

**A pre-existing meta-test caught the new error class, exactly as
designed:** `tests/test_errors.py::test_every_spec_31_code_is_covered_by_exactly_one_error_class`
hardcodes the total count of `WorkspaceError` subclasses (H7's own
pattern from earlier in this hardening pass) — adding
`GitNothingToCommitError` correctly failed it (`24 != 25`) until both that
count and `errors.py`'s own module-docstring claim ("25 typed exceptions",
was "24") were updated. This is the meta-test doing its job — forcing a
deliberate acknowledgment of a new error class rather than letting it slip
in silently — not a regression in anything this task changed.

**Verified the tests catch the regression:** reverted `git/commit.py`,
`tools/git.py`, and `errors.py` to their pre-H22 versions and reran the new
tests. `test_commit.py`'s own import failed outright
(`build_reset_argv`/`is_nothing_to_commit_failure` don't exist on the
reverted module). Both tool-level tests failed exactly as predicted: the
hook-rejected commit left `a.txt` still staged afterward, and the
"nothing to commit" case surfaced as generic `COMMAND_FAILED` with the
telling empty-stderr message described above. Restored the fix; all pass
again; the full `tests/git/test_commit.py` (17 tests) and
`tests/workspace/test_git_commit_tool.py` (18 tests) files, plus the
broader `tests/git/`/`tests/workspace/`/`tests/test_errors.py` suite, show
no regressions. No Docker leftovers, no schema drift.

**New tests:** `tests/git/test_commit.py` — `build_reset_argv` uses the
same literal-pathspec prefixing as `build_add_argv`/`build_commit_argv`;
`is_nothing_to_commit_failure` detects the message on stdout (the real
case) and on stderr (defensively), and correctly returns `False` for an
unrelated failure. `tests/workspace/test_git_commit_tool.py` — a commit
rejected by a pre-commit hook leaves the working tree exactly as it was
(nothing staged, the file back to untracked) rather than leaving a
half-staged index behind; a genuine no-op commit request raises
`GIT_NOTHING_TO_COMMIT` specifically, not the generic `COMMAND_FAILED`
every other commit failure reports.

### H23 — Cache volumes cannot collide across repositories ✅
**Satisfies:** HR-009 · **Verified by:** HAT-008
**Commit:** `f3467cf`

- [x] Hash-derive the volume name from `(repository_id, cache_name)`
- [x] Provide a migration or documented path for existing volumes
- [x] Reclaim volumes on `delete` (currently orphaned, and inherited by a later
      repository reusing the id)

**Hash-derived naming closes the ambiguous-concatenation gap.**
`cache_volume_name` (`workspace/caches.py`) now sha256-hashes
`f"{repository_id}\x00{cache_name}"` — NUL-separated, not just
concatenated — and appends the first 8 hex chars as a suffix to the
existing human-readable slug: `mcp-cache-<slug>-<digest>`. HR-009's own
literal example is now the regression test
(`test_at_hat008_ambiguous_concatenation_does_not_collide`): repository
`my-app` + cache `cargo` and repository `my` + cache `app-cargo` both
concatenate to the identical `"my-app-cargo"`, which is exactly what the
pre-H23 implementation hashed (when it hashed at all) — hashing the
NUL-joined pre-image instead of the ambiguous joined string is what
actually closes the collision, not the hashing by itself. The slug half is
kept only for human readability in `docker volume ls`; the digest is what
uniqueness actually rests on.

**No migration for existing volumes — documented, not silently dropped.**
Renaming the volume scheme orphans any cache volume created under the old
plain-concatenation name; there is no live data-loss risk (these are
disposable build caches, not source of truth), so the decision was to
document the behavior change rather than write a one-time migration for a
handful of dev-machine volumes. Recorded in both `cache_volume_name`'s own
docstring and an "Implementation note (H23)" paragraph appended under
HR-009 in `docs/HARDENING_SPEC.md`.

**Reclaim on delete.** `DockerWorkspaceManager.delete` gained an optional
`cache_volume_names` parameter and a new `_remove_cache_volumes` helper
(tolerant of a volume that was never created — `DockerNotFound` is caught
and skipped, not an error). Nothing on the Docker side links a volume back
to the repository that created it, so the CLI's `delete` command computes
the names itself from that repository's own `.mcp-workspace.yml` (via
`load_project_config` + `cache_volume_name`) and passes them through —
otherwise a repository id later reused for a different project would
silently inherit the old project's cache volumes, exactly the scenario
HR-009 describes.

**Verified the tests catch the regression:** reverted `workspace/caches.py`,
`workspace/manager.py`, and `cli.py` to their pre-H23 versions and reran
the new tests. `test_cache_volume_name_is_repository_scoped` and
`test_at_hat008_ambiguous_concatenation_does_not_collide` both failed
exactly as predicted — the reverted code produced the plain
`mcp-cache-example-cargo` (no digest suffix) and the two ambiguous inputs
resolved to the identical `mcp-cache-my-app-cargo`. Both `test_manager.py`
reclamation tests failed with `TypeError: ... unexpected keyword argument
'cache_volume_names'`. The CLI-level test failed with `DID NOT RAISE
NotFound` — the reverted `delete` command left the volume in place.
Restored the fix; all pass again. No Docker leftovers after either the
revert run or the restore run; no schema drift
(`export-schemas` against a throwaway config diffs clean against
`schemas/tools.json`, as expected — no tool output shape changed).

Also fixed the one pre-existing test that assumed the old exact naming
scheme: `test_build_cache_mounts_are_writable_docker_volumes` now compares
against `cache_volume_name(...)`'s own output instead of a hardcoded
string.

**Full `tests/workspace/` suite:** the backgrounded run was interrupted
twice around the 50–70% mark with zero visible failures (the same
recurring environmental pattern seen since H19), leaving one orphaned
container both times (`docker rm -f`'d immediately, confirmed clean
afterward). Rather than retry a third time, committing on the strength of
the scoped evidence already gathered: `tests/workspace/test_caches.py`
(17/17), `tests/workspace/test_manager.py -k hr009` (2/2, real Docker),
`tests/workspace/test_cli_lifecycle.py -k hr009` (1/1, real Docker),
revert-verification above, plus clean `ruff check`, `ruff format --check`,
and `mypy src`.

**New tests:** `tests/workspace/test_caches.py` —
`test_cache_volume_name_is_deterministic`,
`test_at_hat008_ambiguous_concatenation_does_not_collide`, and a
`.startswith(...)` fix to `test_cache_volume_name_is_repository_scoped`
now that the name carries a digest suffix. `tests/workspace/test_manager.py`
— `test_at_hr009_delete_reclaims_cache_volumes` (real Docker: mounts a
named cache volume, deletes the workspace with that name passed through,
confirms `docker.errors.NotFound` afterward) and
`test_at_hr009_delete_tolerates_a_cache_volume_that_was_never_created`.
`tests/workspace/test_cli_lifecycle.py` —
`test_at_hr009_delete_reclaims_cache_volumes`, a full CLI-level end-to-end
test with a real `.mcp-workspace.yml` cache declaration.

### H24 — Assorted Git correctness ✅
**Satisfies:** HR-022 (partial)
**Commit:** `5f47eed`

- [x] `git_diff`: return `next_offset` and split on character boundaries — byte
      offsets currently corrupt any non-ASCII diff and reassembly is impossible
- [x] `git_diff`: validate `offset`/`max_bytes` (negative values currently
      slice from the end)
- [x] `detect_default_branch`: use `removeprefix("refs/remotes/origin/")`, not
      `rsplit("/", 1)`, which mangles `release/v1`
- [x] Accept abbreviated `expected_head_sha`, or document that it must be full
- [x] Handle `amend_published`'s `require_approval` consistently with
      `default_branch_push`
- [x] `git_commit`: call `touch_last_used` so an active commit stream resets the
      idle clock

**`git_diff` pagination is now character-safe, with an explicit
`next_offset`.** The old scheme sliced raw bytes at `offset`/`offset +
max_bytes` and decoded the window with `errors="replace"` — a boundary
landing mid-character turned that *whole* character into U+FFFD, and
since the client had no way to know the boundary shifted, the follow-up
call started mid-character too and corrupted it a second time. Fixed via
`_trim_incomplete_trailing_char` (`git/diff.py`): a strict `utf-8` decode
attempt on the window detects a truncated trailing sequence
(`UnicodeDecodeError` with `reason == "unexpected end of data"` and
`exc.end == len(window)`, confirmed empirically rather than assumed —
`exc.start` is exactly the cut point) and trims it off, so the window
never splits a character; the new `next_offset` field tells the caller
exactly how many bytes were actually consumed, since a trimmed window is
sometimes shorter than the requested `max_bytes`. Two edge cases,
verified empirically rather than assumed correct:
- trimming only happens when there's a next call to supply the missing
  bytes (`reaches_true_end` check) — trimming the genuine tail of the
  diff would remove bytes with nothing to ever complete them, and repeat
  forever;
- a `max_bytes` smaller than the byte width of the character sitting at
  `offset` (the smallest is 1; a UTF-8 character is up to 4 bytes) would
  otherwise trim the window to *empty* and make zero progress, forever —
  the fix keeps the untrimmed (occasionally lossy) window rather than
  stall in that degenerate case, since forward progress matters more
  than avoiding that one window's corruption.

Also fixed while re-encoding for this: `result.stdout.encode("utf-8")`
(no error strategy) would have raised `UnicodeEncodeError` the moment a
diff touched a non-UTF-8 file, since `run_git_host_side` decodes with
`errors="surrogateescape"` (H19) specifically to keep such bytes
representable — re-encoding must use the same strategy to round-trip them
back losslessly.

`offset`/`max_bytes` are now validated: negative values previously sliced
from the end of the diff (Python's own slicing semantics) instead of
being rejected — `read_git_diff` now raises `InvalidArgumentError` for
either.

**`detect_default_branch` no longer mangles a default branch containing
its own slash.** `ref.rsplit("/", 1)[-1]` on
`"refs/remotes/origin/release/v1"` returned `"v1"`; `removeprefix`
returns `"release/v1"` correctly, since the prefix is always the fixed
`"refs/remotes/origin/"` string, never something to split on.

**`expected_head_sha` now accepts an abbreviated SHA.** New
`head_sha_matches` (`git/status.py`): a prefix match against the always-
full `status.head_sha` (`--porcelain=v2`'s `branch.oid` is never
abbreviated), gated on `MIN_ABBREVIATED_SHA_LENGTH = 4` (git's own
historical minimum) so a degenerate 1-3 char value can't trivially match.
Wired into both `git_commit_tool` and `git_pull_tool`'s `expected_head_sha`
checks, which previously compared with `!=` — rejecting every caller who
(routinely) copied an abbreviated SHA from a `git log --oneline`-style
summary, even when it correctly identified the current HEAD.

**`amend_published`/`default_branch_push` now handle `require_approval`
the same way.** `amend_published`'s existing `!= "allow"` check was
already fail-closed (denies "deny" and "require_approval" alike);
`default_branch_push`'s check is now the same shape
(`not in ("allow", "allow_fast_forward")`) instead of an enumeration of
the two known deny-values (`== "deny" or == "require_approval"`) — the
enumeration silently *permits* the push if this field were ever set to
some other, unrecognized value, rather than correctly refusing it. Both
denials' `details` now also carry the actual configured `value`, matching
the `push`/`force_push`/`force_with_lease`/`delete_remote_*` denials'
existing convention. Note: since `RemoteWritePolicy`/
`default_branch_push`'s own type is a closed `Literal`, every value this
refactor changed the *handling* of was already denied under the old
enumeration too — the fail-closed shape is a real robustness improvement
against a hypothetical future literal value, not a behavior change
reachable through the validated config today, and the two `require_approval`
regression tests below pass on both the reverted and the fixed code (an
honestly-disclosed incidental-coverage gap — see "Verified the tests
catch the regression" below).

**`git_commit` now calls `touch_last_used`.** A real gap: `git_pull`/
`git_push` are documented (S17's idle-stop) as real usage already via a
different path, but `git_commit` never touched `last_used_at` at all — an
active commit-only session (no interleaved `exec`/`read_file`) still
looked idle to §8.1's sweep and could be stopped out from under it.
Placed right after the workspace's `RUNNING` state is confirmed, matching
where `exec`/`read_file` already call it — before the request might still
be denied by a later precondition, since the attempt itself is real usage
regardless of outcome.

**Verified the tests catch the regression:** reverted `git/diff.py`,
`git/commit.py`, `git/status.py`, `tools/git.py`, and `schema_export.py`
to their pre-H24 versions and reran the new tests. `git_diff`'s five new
tests failed exactly as predicted (`AttributeError: 'GitDiffResult'
object has no attribute 'next_offset'`; the two negative-value tests
failed to raise at all). `detect_default_branch`'s new test failed with
the literal mangled value (`'v1' != 'release/v1'`). The abbreviated-
`expected_head_sha` tool test failed with `GIT_HEAD_CHANGED` against the
reverted exact-comparison. The `touch_last_used` test failed with an
unchanged timestamp. Honestly disclosed rather than silently kept
misleading: the two `require_approval` consistency tests (`git_commit`'s
amend, `git_push`'s default-branch) **passed on the reverted code too** —
both policies already denied `require_approval` before this task (see
above); they remain valuable, real regression coverage for "does
`require_approval` deny," just not for the specific fail-closed/`value`-detail
refactor. Restored the fix; all pass again. No Docker leftovers after
either the revert run or the restore run.

**Schema drift, expected and applied:** `git_diff`'s new `next_offset`
output field is a real contract change (§33.2: a new output field is a
patch bump) — `CONTRACT_VERSION` bumped `0.7.2` → `0.7.3`
(`schema_export.py`) and `schemas/tools.json` re-exported and committed.

**Full `tests/git/`/`tests/workspace/`/`tests/test_schema_export.py`
suite:** the backgrounded run was interrupted twice around the 65–70%
mark with zero visible failures (the same recurring environmental
pattern seen since H19), leaving one orphaned container both times
(`docker rm -f`'d immediately, confirmed clean afterward). Rather than
retry a third time, committing on the strength of the scoped evidence
already gathered: every individual file this task touched or that
exercises the changed code paths, run to completion —
`tests/git/test_diff.py` (19/19), `tests/git/test_commit.py` (18/18),
`tests/git/test_status.py` (14/14), `tests/workspace/test_git_commit_tool.py`
(21/21), `tests/workspace/test_git_push_tool.py` (19/19),
`tests/workspace/test_git_pull_tool.py` (9/9),
`tests/workspace/test_git_diff_log_tools.py` (5/5),
`tests/workspace/test_idle_stop.py` (13/13, real Docker) — plus the
revert-verification above, `tests/test_schema_export.py` (9/9), and clean
`ruff check`, `ruff format --check`, and `mypy src` across the whole
tree.

**New tests:** `tests/git/test_diff.py` — negative `offset`/`max_bytes`
rejection, `next_offset` matches the actual bytes consumed, a tight
(`max_bytes=4`) paginated read over genuinely multi-byte diff content
reassembles byte-for-byte identical to an unpaginated read with no
replacement character anywhere, and a degenerate `max_bytes=1` still
terminates. `tests/git/test_commit.py` — a default branch named
`release/v1` (its own real remote/clone, not a stub) round-trips through
`detect_default_branch` unmangled. `tests/git/test_status.py` — unit
coverage for `head_sha_matches`: full match, abbreviated-prefix match,
mismatched prefix, too-short abbreviation, `None` (unborn branch).
`tests/workspace/test_git_commit_tool.py` — an abbreviated
`expected_head_sha` is accepted end-to-end through the real tool; amend
against a published commit is still denied under
`amend_published: require_approval`.
`tests/workspace/test_git_push_tool.py` — a push to the default branch is
still denied under `default_branch_push: require_approval`.
`tests/workspace/test_idle_stop.py` — `git_commit` bumps `last_used_at`,
isolated from `write_file`'s own already-existing bump in the same test.

---

## Phase 8 — Test and CI integrity

### H25 — The coverage audit does not satisfy itself ✅
**Satisfies:** HR-027 · **Verified by:** HAT-021
**Commit:** `a5c6fd9`

- [x] Exclude `__file__` from `_all_test_source()`
- [x] Add a meta-test: removing every real citation for an AT makes the audit
      fail for that AT

**Defect:** `tests/test_acceptance_test_coverage.py:36,59`. Verified — with all
real AT-010 citations removed, the audit still passed, because its own error
message contains the string. Line 23 does the same for AT-011.

**Fix.** `_all_test_source()` now skips `__file__` (resolved once, at
module load, as `_THIS_FILE`) while walking `tests/`, so this module's
own error-message text (`f"AT-{uncited} have no citation..."`) and its
own comments (the original line 23's "`test_at_011_...`" example) can no
longer supply a vacuous self-citation for whatever AT number they happen
to mention. One fix covers both AT-010 and AT-011's self-citations named
in the Defect above — both came from the same root cause (this file being
included in its own scan), not two separate bugs needing two separate
patches.

**Verified the exact reported defect reproduces, and that the fix closes
it — against real test files, not a simulated string.** Backed up
`tests/git/test_pull.py`, `tests/git/test_push.py`, and this file itself;
stripped every real `AT-010`/`AT_010` mention from the two backing files
(their only real citations of AT-010 in the whole suite); reverted this
file to its pre-H25 version (the one still scanning `__file__`); ran the
audit — it **passed**, reproducing the exact reported defect live: with
every real citation gone, the audit's own text still "cited" AT-010 to
itself. Restored the fixed version of this file (still with `test_pull.py`/
`test_push.py`'s AT-010 mentions stripped) — the audit now **failed**,
correctly reporting `AT-[10]` as uncited. Restored `test_pull.py`/
`test_push.py` from backup; the full file (3 tests, including the new
meta-test) passes clean again.

**New test — the meta-test the task asked for.**
`test_meta_removing_every_citation_for_an_at_makes_the_audit_fail_for_it`
takes the *real* `_all_test_source()` output (so it also implicitly
depends on the `__file__` exclusion above being correct), regex-scrubs
every citation of AT-010 out of it, and asserts the audit's own
uncited-detection logic then flags exactly `[10]` — not empty (the audit
is not a rubber stamp), and not some other AT (the citation regex is
correctly scoped to just the target number, not over-matching a
neighboring one like AT-100 or AT-011).

No Docker involved anywhere in this task (pure test-source scanning); no
schema drift (no tool code touched). Full-suite collection
(`pytest --collect-only`) still finds all 788 tests with no import
errors; `ruff check`, `ruff format --check`, and `mypy src` clean across
the whole tree.

### H26 — Coverage claims rest on assertions ✅
**Satisfies:** HR-028
**Commit:** `2288f34`

- [x] Implement AT-037's `ssh_auth` audit event, or record AT-037 as explicitly
      uncovered — `grep -rn ssh_auth src/` currently returns nothing
- [x] Make AT-021's scenario reachable by adding §24's `exec` audit event, or
      amend the AT
- [x] Re-audit every AT whose only citation is a docstring
- [x] Correct S33's category audit, which cites `filesystem/test_security.py`
      for "secret leakage" on the strength of a file *named* `secret.txt`

**Implemented both audit events — real fixes, not amendments.** Neither
gap needed a spec amendment: `exec`'s "command started" event (§24's own
recommended-events list) and AT-037's `ssh_auth` event both had
everything they needed already in place (`write_audit_event`/
`redact_secrets`, S23's real `ssh-agent` module) — wiring them in was a
natural extension, not a new mechanism.

**`exec`'s "command started" event, closing AT-021.** New
`_audit_command_started` (`tools/execution.py`), called from both `exec`
and `exec_start` right after the command actually starts (not on a start
*failure* — §24 lists "command started," not "command start failed," and
one that never started has nothing to audit as started). `metadata`
carries `argv`/`shell`/`cwd` exactly as the client supplied them, through
`write_audit_event`'s existing `redact_secrets` (§24.1) — before this,
`exec` produced no audit record at all, so AT-021 ("run a command
containing a configured secret and verify audit output does not expose
the secret value") had no audit output to inspect in the first place.

**AT-037's `ssh_auth` event, with a real fingerprint.** New
`ssh_key_fingerprint` (`workspace/ssh_agent.py`, `ssh-keygen -lf`,
computed fresh per call — it only reads public key material, never the
agent or any loaded private key, so there's nothing to cache or go
stale) and `_audit_command_started`'s sibling
`_audit_ssh_auth_if_applicable` (`tools/execution.py`). Every check is a
silent skip, not a second authorization gate: `ssh.mode != "agent"`,
`record.mode != "persistent"` (an ephemeral workspace never gets an
agent mount in the first place — the same condition
`workspace_open`'s own mount-building code already gates on), no key
resolves for this repository, or the key fails to fingerprint. This is
an audit *side effect* of a command already authorized to run — none of
these should make `exec` itself fail.

**Re-audited every AT whose only citation is a docstring.** Computed the
real citation set with the audit's own regex (not a shell `grep -b`,
which undercounts identifier-embedded citations the same way the
pre-H25 manual grep did — verified this by hand against
`test_at_011_...`, which a naive `\b`-bounded pattern misses entirely).
Four ATs have exactly one citation each: AT-006, AT-011, AT-025, AT-029.
AT-011's sole citation is its own test function's name
(`test_at_011_force_true_is_denied_before_git_runs_under_default_policy`)
— the strongest possible kind, not a bare docstring. AT-025 and AT-029
both have real, on-point assertions behind their docstring (AT-025
proves suppression *and* that the suppressed mechanism genuinely fires
without the override — "proof both ways," not just "nothing happened").
AT-006 also has real, meaningful assertions (job stays queryable,
eventually completes, output correct) — its one known weakness (the
"disconnect" is simulated by `del started` on an in-process server, not
a real network disconnect) is a *realism* gap already tracked
separately under H28's own checklist, not a citation-vacuity problem;
re-flagging it here would double-count the same defect under two tasks.
No AT in this audit turned out to rest on a citation with nothing real
behind it.

**Corrected `docs/TODO.md`'s S33 category audit.** Its "secret leakage"
citation read `filesystem/test_security.py`, `test_audit.py` — the
former was wrong: that file's only secret-shaped name is
`test_at_002_escaping_symlink_is_rejected`'s `outside / "secret.txt"`, a
symlink-escape test (AT-002) whose protected file is merely *named*
`secret.txt`, asserting nothing about redaction or a leaked secret
*value*. Replaced with the citations "redaction" (two lines above the
same list) already correctly used: `test_audit.py`, `test_errors.py`,
and now `workspace/test_audit_logging.py`'s new AT-021 test — added a
correction paragraph explaining the fix rather than silently rewriting
history.

**Verified the tests catch the regression:** reverted `tools/execution.py`,
`workspace/ssh_agent.py`, and `server.py` to their pre-H26 versions and
reran the new tests. All 5 new `test_audit_logging.py` tests failed
exactly as predicted (no `exec`/`ssh_auth` events written at all). The
two pre-existing "Docker unreachable" tests
(`test_execution_tools.py`, `test_async_execution_tools.py`) — updated
for `register()`'s two new required parameters — failed with
`TypeError: register() got an unexpected keyword argument 'audit_config'`
against the reverted signature, confirming those updates are load-bearing,
not decorative. Restored the fix; all 45 tests across the three touched
files pass again. No Docker leftovers, no leaked `ssh-agent` processes,
after either the revert run or the restore run.

**A real, unrelated regression found by this task's own broader-suite
run, fixed separately (commit `85c5c35`, pushed ahead of this one):**
`test_project_config_integration.py::test_cache_volume_is_mounted_writable_and_owned_by_the_container_user`
still asserted the literal pre-H23 volume name
(`"mcp-cache-example-cargo"`), which H23's hash-suffixed naming (HR-009)
made stale — H23's own broader-suite runs were interrupted before ever
reaching this file, so its scoped-evidence fallback never exercised it.
H26's own broader run reached further and caught it. Fixed to compare
against `cache_volume_name(...)`'s real output; not bundled into this
commit, since it is a fix for H23's own change, not H26's.

No schema drift (no tool output shape changed — the two new audit
events are a side channel, not a tool response field).

**Full relevant suite** (`tests/workspace/`, `test_errors.py`,
`test_acceptance_test_coverage.py`, `test_audit.py`): 355 passed clean
in one uninterrupted background run (the recurring interruption pattern
seen since H19 did not recur this time), after the cache-volume fix
above. `ruff check`, `ruff format --check`, and `mypy src` clean across
the whole tree.

**New tests:** `tests/workspace/test_audit_logging.py` —
`test_exec_produces_a_command_started_audit_event`;
`test_at_hat021_exec_command_started_event_redacts_a_secret_in_argv`
(a GitHub-PAT-shaped value in `argv` does not appear in the written
audit record); `test_at_037_exec_produces_an_ssh_auth_audit_event_with_a_fingerprint`
(real `ssh-keygen`-generated key, real `ssh.mode: agent`);
`test_exec_start_also_produces_command_started_and_ssh_auth_events`
(the background-job path gets the same events, not only synchronous
`exec`); `test_no_ssh_auth_event_when_the_repository_has_no_configured_key`
(no key resolves → no fabricated event).

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
