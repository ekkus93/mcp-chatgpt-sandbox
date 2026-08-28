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

### H27 — A degraded CI run fails ✅
**Satisfies:** HR-029 · **Verified by:** HAT-022
**Commit:** `0e3e0a3`

- [x] Fail CI when Docker-dependent tests skip (`--strict-markers` plus an
      explicit skip guard, or a minimum-collected assertion)
- [x] Replace the stale `alpine:3.20` pull step, which is no longer the test
      image
- [x] Make the contract check able to fail on an unbumped `contract_version` —
      it currently compares the constant against itself

**Any skip now fails the run — not `--strict-markers` (nothing in this
suite uses an unregistered marker; the actual mechanism is a plain
runtime `pytest.skip()` call, which markers can't intercept).** New
`tests/_skip_policy.py`: `pytest_runtest_logreport` collects every
skipped report, `pytest_sessionfinish` sets `session.exitstatus = 1`
when any were collected. `tests/conftest.py` installs both as real
pytest hooks. Blanket ("any skip fails"), not scoped to a specific skip
message, because there are exactly two `pytest.skip(...)` call sites in
the entire suite (both in `workspace/conftest.py`, both meaning "Docker
is not reachable") — verified via `grep -rn "pytest.skip\|@pytest.mark.skip"
tests/` before relying on it. The module docstring says so explicitly,
so a future genuinely-unrelated skip reason gets a deliberate decision,
not a silent policy violation.

**Stale `alpine:3.20` pull replaced with actually building the real test
image.** `docker/default/Dockerfile` is `FROM ubuntu:24.04` —
`alpine:3.20` was never the image anything in this suite uses; pulling
it proved nothing about whether the real image (built lazily by
`tests/workspace/conftest.py`'s `default_image_tag` fixture, from the
same context) actually builds. Replaced with `docker build -t
mcp-dev-test-s7:24.04 docker/default/` — same tag `DEFAULT_IMAGE_TEST_TAG`
names, so this step's build becomes the fixture's later build's cache
hit, keeping the cost visible as a distinct CI step rather than hidden
inside the first test that needs it (the CI step's own original intent,
which the alpine pull never actually served).

**contract_version bump check, git-history-aware.** The existing "MCP
contract check" step only diffs a fresh `export-schemas` run against the
committed `schemas/tools.json` — both sides derive `contract_version`
from the same `CONTRACT_VERSION` constant, so the check can detect
*staleness* (committed file doesn't match current source) but not a
forgotten version bump (source changed a tool's schema, the developer
correctly regenerated and committed the file, but never touched
`CONTRACT_VERSION` — both sides of the diff would still match). New
step: `git show <base>:schemas/tools.json` against the base commit
(`github.event.pull_request.base.sha` for a PR, `github.event.before`
for a push; skips gracefully with neither available or when the base
predates `schemas/tools.json`'s own existence), then `jq -S .tools`
compared between base and `HEAD` with `contract_version` excluded from
that comparison — if the tools differ but `contract_version` doesn't,
fail. Needs `actions/checkout@v4`'s default shallow (`fetch-depth: 1`)
history widened to `fetch-depth: 0`, so the base commit's blob is
actually reachable via `git show`.

**Verified the logic directly, not just by reading it.** The `jq`
comparison was run by hand against real commits before trusting it: `git
show 3349553:schemas/tools.json` (H23, `contract_version: 0.7.2`)
against the current `schemas/tools.json` (H24, `0.7.3`) — tools differ,
version differs, correctly no violation. Then simulated the actual
defect this check exists for: hand-edited a scratch copy of the current
`schemas/tools.json` with a real schema change (a tool's `description`)
while keeping `contract_version` unchanged — the same comparison
correctly flagged it as a violation. `docker build -t
mcp-dev-test-s7:24.04 docker/default/` was run for real, confirmed it
builds and produces exactly the tag the fixture expects. The workflow
YAML was parsed with `yaml.safe_load` to confirm it's syntactically
valid — this repo has no way to run the real GitHub Actions runner, so
this is the strongest verification available short of an actual CI run.

**Verified the skip-detection tests catch the regression:** reverted
`tests/conftest.py` to its pre-H27 version (no `pytest_plugins`
declaration, no hook installation) and reran the new
`tests/test_skip_policy.py`. All 4 failed — not with "skip didn't fail
the run" (the more obviously-expected failure mode) but with `fixture
'pytester' not found`, since the reverted `conftest.py` no longer
enables that plugin either — still a correct, load-bearing failure
tied directly to what was reverted. Restored the fix; all 4 pass again.

**New tests:** `tests/test_skip_policy.py`, exercised through pytest's
own `pytester` plugin (a real, separate subprocess — `session.exitstatus`
is process-level state a mocked call can't stand in for) against the
*real* `_skip_policy.py` hooks, not a hand-duplicated copy that could
drift from what `conftest.py` actually installs:
`test_a_clean_pass_exits_zero` (no false positives), `test_at_hat022_a_skip_fails_the_run`
(HAT-022 itself), `test_a_mix_of_pass_and_skip_still_fails` (one skip
among passes is still enough), `test_a_genuine_failure_still_fails_without_the_hooks_help`
(an ordinary assertion failure was never the gap).

No Docker leftovers after the revert or restore runs. No schema drift.
`ruff check`, `ruff format --check`, `mypy src` clean across the whole
tree; `tests/test_skip_policy.py` (4/4) and the broader `tests/`
collection (`pytest --collect-only`, no import errors) — the full
background suite run was interrupted at the ~84% mark with zero visible
failures (the same recurring environmental pattern seen since H19,
this time on the *entire* suite rather than a scoped subset, since H27
touches global test infra); proceeding on the direct evidence above
rather than retrying, since none of it depends on that specific run
completing.

### H28 — Adversarial coverage for every input boundary ✅
**Satisfies:** HR-030
**Commit:** `5cce337`

- [x] For each control in Phases 1–5, add a hostile-input test alongside the
      well-formed one
- [x] Fix the tests the review found asserting nothing meaningful: AT-006's
      "disconnect" (`del started` on an in-process server), `test_write_is_
      flushed_immediately_for_concurrent_readers` (single-threaded), and
      `test_reconcile`'s hand-set `pid` (a shape production never produces)
- [x] Add restart-survival coverage for jobs, which currently has none

**Phase 1–5 audit, not a blind fill-in.** Rather than assume every
control needed a new test, every control in §4–§8 (HR-001 through
HR-016, plus HR-009/HR-024 from Phase 7, named explicitly in the task)
was individually re-checked against the actual test bodies already in
the suite — not just the TODO doc's own narrative. 15 of 16 controls
already had genuine, on-point hostile-input coverage (real
option-injection strings, real attempted code execution via a planted
shell driver with a host-side side-effect probe, a sustained
2000-iteration symlink race, redaction cases chosen specifically to
evade the *other* three redaction mechanisms, and more) — this
codebase's own prior hardening tasks were already unusually rigorous
about this. **Exactly one genuine gap surfaced**, and it was already
self-documented as deliberately deferred, not silently missing:
HR-011's `write_file` mkdir side effect (below).

**Closed the one real gap: `write_file`'s mkdir-side symlink race
(HR-011).** `path.parent.mkdir(parents=True, exist_ok=True)` walked the
*resolved path string* — a pre-existing malicious symlink at an
intermediate component could make it create directories through it,
even though the existing fd+readlink checks already guaranteed no
*data* ever lands outside the root. New
`FilesystemSecurity.ensure_parent_directories`: the full per-component
`O_NOFOLLOW` walk HR-011 itself names as the alternative technique —
starting from the repository root's own trusted fd, each intermediate
component is `mkdir`'d (a no-op if it already exists) and then *opened*
with `O_NOFOLLOW` before descending, atomically refusing a symlink at
that name whether it was already there or swapped in between the
`mkdir` and the `open` (the kernel checks what's actually there at the
moment of the `open` syscall itself). Deliberate, documented tradeoff:
a previously-legitimate layout with an in-bounds symlinked intermediate
directory is now rejected too, not only a malicious one — a `mkdir`
that gets `FileExistsError` cannot distinguish "existed benignly before
this call" from "just swapped in by a racing attacker," so accepting
either would silently reopen the exact race this closes.

**Verified the exact reported defect reproduces, and that the fix
closes it — a real race, not a simulated string.** Reused
`test_service.py`'s existing `_symlink_flipper` fixture (a background
thread continuously flipping a directory between a real directory and
a symlink to outside the repository), targeting a *nested* path
(`d/nested/pwned.txt`) rather than the single-level target the
existing HAT-009 write test uses — a single-level target can't reach
this gap at all, since `resolve()` itself already rejects the whole
call if the immediate parent is currently an escaping symlink at
resolution time (there being no intermediate component left to
`mkdir`). Reverted `ensure_parent_directories` back to the old
`mkdir(parents=True)` and reran: a real `outside/nested/` directory was
left behind on the host, confirmed by the new test's own failure.
Restored the fix; 2000 iterations, no leftover directory.

**AT-006's "disconnect" is now genuinely cross-session.** The
predecessor `del`'d the `exec_start` response inside the *same* Python
`server` object and the *same* `AsyncJobRunner` — proving nothing about
whether job state survives independent of the in-process object graph
that started it, since nothing ever severed a connection.
`create_server` opens its own real, file-backed `sqlite3.Connection`
and its own fresh `AsyncJobRunner` on every call — a *second*,
independent `create_server(config)` instance queries the job with no
Python reference to anything the first session created, so
`exec_status`/`exec_output` can only be reading real, persisted
`JobStore` state, not an in-memory shortcut.

**The concurrent-reader test is now genuinely multi-threaded.** The
predecessor's single-threaded write-then-reread *did* catch a missing
`flush()` (verified by disabling it and confirming the old test failed)
— its problem was the "concurrent readers" claim in its own name, not
that it asserted nothing. New test: a real reader thread polls the log
file while a writer thread is still writing 200 chunks; every
intermediate snapshot the reader observes must be a genuine prefix of
the eventual final content (a torn write or a stale read would show up
as a snapshot the final content doesn't start with), and the reader
must have observed genuine growth over time (multiple distinct sizes),
not just caught the final state once. Stable across 5 repeated runs.

**`test_reconcile`'s hand-set pid is now a real, realistic one.** The
old `pid=2**30` is a magnitude no real `Popen.pid` could ever take
(this host's own `pid_max` is 4194304) — a shape `mark_running`'s real
callers never produce, and `_pid_alive`'s `os.kill` can't actually
distinguish "never a valid pid at all" from "was valid, has since
exited" (both raise `ProcessLookupError` identically), so an
out-of-range placeholder risked exercising the wrong scenario without
anyone noticing. Now a real subprocess, spawned and `wait()`-reaped
(genuinely dead, genuinely realistic) before its pid is used.

**Restart-survival coverage for jobs, genuinely new — the existing
`test_serve_reconciles_non_terminal_jobs_and_warns` (`tests/test_cli.py`,
pre-dating this hardening pass) already covered the CLI's real wiring
into `reconcile_jobs`, but only against a hand-inserted `JobStore` row
(`pid=1`, no real process, no real output ever written) — proving
reconciliation flips a DB column, not that a job's already-captured
output is still correctly *served*, through the real `exec_status`/
`exec_output` MCP tools, by a different server instance afterward. New
`tests/workspace/test_job_restart_survival.py`: a real, Docker-backed
job produces real output, is abandoned mid-run with no clean shutdown
("crashed"), goes through the real `serve` CLI startup sequence
(`docker_available`/`run_server` mocked, everything else — config
load, reconciliation — genuinely runs), then is queried by a *third*,
independent `create_server` instance standing in for the restarted
daemon — the closest approximation of an actual restart this harness
can produce. Not a fix for a defect (`reconcile_jobs`/the CLI wiring
already worked correctly) — new coverage of already-correct behavior
this task's own audit found untested, verified by writing the test and
watching it pass, not by reverting anything.

**Full relevant suite:** `tests/filesystem/`, `tests/tools/test_filesystem.py`,
`tests/execution/`, `tests/jobs/`, `tests/test_cli.py` (157 passed),
`tests/workspace/test_async_execution_tools.py` +
`tests/workspace/test_job_restart_survival.py` (22 passed, real
Docker). No Docker leftovers after any run. No schema drift. `ruff
check`, `ruff format --check`, `mypy src` clean across the whole tree.
The full background `tests/` run was interrupted at the ~82% mark with
zero visible failures (the same recurring environmental pattern seen
since H19), leaving one orphaned container (`docker rm -f`'d,
confirmed clean); proceeding on the direct evidence above rather than
retrying.

**New tests:** `tests/filesystem/test_service.py` —
`test_at_h028_write_file_never_mkdirs_through_an_intermediate_symlink`
(2000-iteration race, real `outside/nested/` reproduction on revert).
`tests/workspace/test_async_execution_tools.py` — AT-006 rewritten
cross-session. `tests/execution/test_log_capture.py` — a real reader
thread racing a real writer thread. `tests/jobs/test_reconcile.py` — a
real, reaped subprocess pid. `tests/workspace/test_job_restart_survival.py`
— `test_at_h028_a_crashed_jobs_output_survives_a_real_restart`,
`test_at_h028_a_finished_jobs_output_survives_a_real_restart` (the
non-crash counterpart — reconciliation correctly leaves an
already-terminal job alone), `test_at_h028_exec_status_on_an_unknown_job_after_restart_fails_closed`
(a hostile-input companion: a plausible but never-real `job_id` against
a fresh post-restart session with no in-memory fallback still fails
closed with `JOB_NOT_FOUND`).

---

## Phase 9 — Defaults, dead configuration, observability, documentation

### H29 — Bound output by default ✅
**Satisfies:** HD3, `HARDENING_SPEC.md` §9 (§15.5 amendment)
**Commit:** `64baedd`

- [x] Default the `exec_output` response cap to ~64 KiB, with the full capture
      reachable only through explicit ranged retrieval
- [x] Validate `byte_start`/`byte_end`/`line_start`/`line_end` (negatives
      currently slice from the end)
- [x] Reconcile byte offsets against `stream_total_bytes`, which currently index
      different things and silently mislead a paging client
- [x] Stream rather than slurping in `read_log_bytes`
- [x] Bound `read_file` by stat before reading, not after

**Defect:** the current default is `max_output_bytes + tail_keep_bytes` =
10.25 MiB, defeating §15.5's stated purpose. AT-007 passes only because that
test reconfigures the cap to 4096.

**A real config default, per HD3.** New `ExecutionConfig.default_output_response_bytes`
(64 KiB), distinct from `max_output_bytes`/`tail_keep_bytes` (what stays
*captured on disk*, §15.6, unchanged) — `exec_output` now defaults to
this when `max_bytes` is omitted; the full on-disk capture stays
reachable, just only through an explicit, larger `max_bytes`. This is
the actual code fix — `docs/SPEC.md` §15.5's own text (H34's job,
`docs/HARDENING_SPEC.md` §9's amendment list) still describes the old
default and is updated there, not here, to keep one task per commit.

**Negative offsets rejected outright.** `byte_start`/`byte_end`/
`line_start`/`line_end`/`max_bytes` all used to accept a negative value
silently — Python's own slicing semantics turn that into "N from the
end," a meaning the documented contract ("position N from the start")
never intended. All five now raise `InvalidArgumentError` for a
negative value.

**`stored_bytes` reconciles what byte offsets actually index into.**
`stream_total_bytes` (§15.6's uncapped production total) and what
`byte_start`/`byte_end` can actually retrieve are not the same number
once a stream has ever been truncated on disk — the gap between them
was silently invisible to a paging client before. New `stored_bytes`
field (`os.stat`, not a read) reports the real, retrievable count;
`byte_start`/`byte_end` are documented as indexing into it, not
`stream_total_bytes`.

**`read_log_bytes` seeks; `exec_status`'s tail reads backward in
chunks.** `read_log_bytes` gained `offset`/`max_bytes` (seek + bounded
read, not a full-file slurp followed by Python slicing) — `exec_output`'s
byte-range and no-range cases (the common ones) now use it directly.
New `read_log_tail` for `exec_status`'s "last N lines": reads backward
from disk in growing chunks (64 KiB doubling) rather than reading the
whole capped capture — up to 10.25 MiB with the stock config — just to
keep its final 200 lines, on *every poll* of a still-running job.
`line_start`/`line_end` retrieval in `exec_output` still reads the whole
on-disk capture: line boundaries aren't determinable without scanning,
and this is a real, deliberate, documented scope decision, not an
oversight.

**`read_file` bounds its content buffer by the known limit before
reading, streams the rest for the hash.** `total_size` now comes from
what was actually counted while streaming (more accurate than a
pre-read `fstat` under a concurrently-growing file, and — unlike a
`stat()` snapshot — exactly the "size == what was really read"
semantics the code already had). For the common no-range case: reads
`limit + 1` bytes (a peek, to detect "more" without holding it), then
streams the remainder through `hashlib.sha256` in
`_READ_FILE_HASH_CHUNK_BYTES`-sized chunks — `full_sha` (§13.2's
contract: the *whole* file's hash regardless of truncation, since
`write_file`'s `expected_sha256` conflict check depends on this being
correct) is still computed over every byte, just never all held in
memory simultaneously. A documented, narrower scope, not silently
applied everywhere: `byte_range`/`line_range` can name a window
anywhere in the file, so that path still reads the whole content to
slice at an arbitrary position — unchanged from before this task. Binary
detection for the bounded no-range path now only inspects the bytes
actually kept as content, not bytes beyond the limit — a real, small
semantic narrowing (a file that's valid UTF-8 for its first `limit`
bytes but has an invalid byte far beyond that would now read as
non-binary where it previously wouldn't), explicitly acknowledged
rather than silently introduced; in practice a genuinely binary file
almost always has non-text bytes within the first few KiB, so this
rarely changes real-world behavior.

**Verified the tests catch the regression:** reverted `config/models.py`,
`execution/log_capture.py`, `tools/execution.py`, `filesystem/service.py`,
and `schema_export.py` to their pre-H29 versions and reran the new
tests. `tests/execution/test_log_capture.py` failed to even collect
(`ImportError: cannot import name 'read_log_tail'`).
`tests/filesystem/test_service.py -k h029` failed the same way
(`_READ_FILE_HASH_CHUNK_BYTES` doesn't exist on the reverted module).
All 7 new `tests/workspace/test_async_execution_tools.py -k h029` tests
failed against real Docker: the default-cap test got the old ~10 MiB
response back untruncated; all 5 negative-offset tests raised nothing
(`DID NOT RAISE ToolError`); the `stored_bytes` test hit a `KeyError` —
the field didn't exist. Restored the fix; all pass again. No Docker
leftovers after either run.

**Schema drift, expected and applied:** `exec_output`'s new
`stored_bytes` output field is a real contract change (§33.2: a new
output field is a patch bump) — `CONTRACT_VERSION` bumped `0.7.3` →
`0.7.4` and `schemas/tools.json` re-exported and committed.

**Full relevant suite:** `tests/execution/test_log_capture.py` (22
passed) + `tests/filesystem/`/`tests/tools/test_filesystem.py` (127
passed) + `tests/workspace/test_async_execution_tools.py`/
`test_execution_tools.py`/`test_job_restart_survival.py` (41 passed,
real Docker) — all clean. No Docker leftovers. `ruff check`, `ruff
format --check`, `mypy src` clean across the whole tree; no schema
drift beyond the intentional `stored_bytes` addition. The full
background `tests/` run was interrupted at the ~82% mark with zero
visible failures (the same recurring environmental pattern seen since
H19), leaving one orphaned container (`docker rm -f`'d, confirmed
clean); proceeding on the direct evidence above rather than retrying.

**New tests:** `tests/execution/test_log_capture.py` —
`read_log_bytes`'s offset/max_bytes windowing (including past-EOF and
zero-length requests), `stat_log_bytes`, `read_log_tail` (last-N-lines
correctness, a growing-chunk case with lines wider than the initial
64 KiB window, shorter-than-requested files, missing paths,
zero/negative `max_lines`). `tests/filesystem/test_service.py` — a
multi-megabyte file (genuinely larger than one hash-stream chunk, unlike
the pre-existing 100-byte truncation tests) confirms `sha256`/`size`
still match a full read; a `byte_range` deep inside a large file
confirms the still-full-read ranged path is unaffected.
`tests/workspace/test_async_execution_tools.py` — the default cap
bounds a ~200 KiB real job's output to ≤64 KiB while the full capture
stays reachable via an explicit `max_bytes`; all five fields reject a
negative value (parametrized); `stored_bytes`/`stream_total_bytes`
genuinely diverge under a forced-small on-disk cap, and a `byte_start`
at the edge of `stored_bytes` still returns real content (the kept
tail, not an error).

### H30 — Enforce or delete declared configuration ✅
**Satisfies:** HR-010
**Commit:** `787d7c3`

- [x] Enforce or remove the `security:` block (`allow_docker_socket`,
      `allow_privileged_containers`, `allow_additional_host_mounts`)
- [x] Enforce or remove `git_policy.validate_remote`
- [x] Clamp client `timeout_seconds` to `execution.sync_timeout_seconds`
- [x] Normalize `cwd` before the containment test (`/workspace/../etc` currently
      passes a string-prefix check)
- [x] Add cross-field validation: `api_auth: token` with no token path,
      `scope: shared` with no `shared_key`, `user: root` with `sudo: true`
- [x] Extend the §53 secrets-outside-repository check to `audit.path` and
      `state_db_path`
- [x] Implement §73 step 7 — repository roots are never checked for existence
- [x] Catch non-`ConfigError` exceptions at load (`workspace: 5` currently
      raises a raw `TypeError`)

**`security:` and `validate_remote` removed, not enforced.** Neither had
anything to gate: `grep -rn "allow_docker_socket\|allow_privileged_containers\|
allow_additional_host_mounts"` across `src/` outside `config/models.py`
returned nothing — no structured tool, `exec` path, or project-config
concept can mount the Docker socket, run privileged, or mount an
additional host path in the first place, so implementing new enforcement
for these three would have been building a gate for a door that doesn't
exist (speculative code the task's own HD3/§18.9 principle — "a policy
key with no tool to enforce it is not a policy" — argues against).
`validate_remote` similarly had no reader; remote-identity validation
(`git/remote.py`) is unconditional, foundational security behavior
(AT-009, HR-014) this daemon does not offer an opt-out from — wiring the
toggle up would mean *adding* a way to disable a core protection, which
is the opposite of what HR-010 asks for. Both removed from
`config/models.py` (and `config/__init__.py`'s re-export); `docs/SPEC.md`'s
two example configs (§18.9, §52) updated to match, since this is a
config-schema removal, not a tool-contract change H34's amendment list
already covers.

**Cross-field validation: investigated all three suggested examples,
added none of them — each one turned out to conflict with intentional,
already-tested graceful-degradation behavior, not close a real gap.**
Checked against actual runtime/test behavior before trusting the
TODO's own suggestion, the same discipline this session has applied
throughout:
- `scope: shared` with no `shared_key`: `resolve_ssh_key_path`'s own
  docstring documents this as intentional ("`None` when nothing is
  configured for it ... callers treat `None` as 'nothing to provision,'
  not an error"), and
  `test_resolve_ssh_key_path_shared_scope_none_when_unset`
  (`tests/workspace/test_ssh_agent.py`, pre-existing) exercises exactly
  this shape as a real, supported case.
- `api_auth: token` with no `api_token_path`: `github_api.py`'s own
  remote-protection query already handles this combination gracefully —
  falling back to `_unverified(default_branch, "api_auth: token but no
  api_token_path configured")` rather than failing.
- `user: root` with `sudo: true`: `sudo` defaults to `true`, and
  multiple existing, real tests (`tests/workspace/test_container_config.py`,
  `tests/workspace/test_default_image.py`'s `test_at_016_...`) construct
  `ContainerConfig(user="root", sudo=True)` or `ContainerConfig(user="root")`
  (picking up the default) to exercise the unrelated "root refused
  without user-namespace remapping" behavior — genuinely redundant
  (the container's own uid is already 0), but not unsafe (`sudo` under
  an already-root process is a no-op, not a privilege change).

A first draft implemented all three as hard `ConfigError`s via
`@model_validator`; running the existing suite against it caught all
three conflicts immediately (a real regression this task's own testing
step exists to catch, not a defect this task introduced) — retracted,
with the reasoning above left in place as a comment at each
considered-but-rejected site, so the decision is visible, not silently
absent.

**`timeout_seconds` is a real ceiling.** New `_resolve_timeout`
(`tools/execution.py`), used by both `exec` and `exec_start`: a
client-supplied value above `execution.sync_timeout_seconds`/
`default_job_timeout_seconds` is clamped down to it, not honored as
requested; a non-positive value is rejected outright (`InvalidArgumentError`)
rather than reaching `asyncio.wait_for` with a nonsensical timeout.

**`cwd` is normalized before the containment test.** `_validate_cwd` now
returns the normalized value, which is also what's actually used for
the exec call afterward — not just what's checked, matching this
codebase's own established principle elsewhere (validate what's
actually used, not a separate representation of it). The old
`cwd.startswith(workspace_path + "/")` check accepted
`"/workspace/../etc"` outright, since it textually starts with
`"/workspace/"` even though it resolves entirely outside the workspace.

**§53's secrets-outside-repository check now covers `audit.path`/
`state_db_path`.** Neither is a credential file itself, but both hold
the same class of information §53 exists to keep off a client-readable
path — an audit log records command argv (best-effort redacted, not a
guarantee, HR-013) and the state DB is the daemon's own internal state.

**§73 step 7, implemented: repository roots validated for existence at
startup.** `RepositoryRegistry`'s own discovery
(`repositories.py`'s `_discover_under_root`) silently returns nothing
for a `repository_roots` entry that doesn't exist — correct there (an
unrelated request has no business failing over an unrelated discovery
root), but that left an operator with a typo'd or since-removed root
never told anything was wrong: repositories they expected to be
auto-discovered just weren't, with no error and no warning. New
`_check_repository_roots_exist`, called once at `load_config` time.
Scoped to `repository_roots` specifically (not `repositories[].path`,
whose absence already fails loudly and visibly the first time a
workspace against it is opened, a materially different situation).

**Every config-load failure now surfaces as `ConfigError`.** Two
layers: `_parse` itself now catches the specific reproduced case
(`workspace: 5`, a scalar reaching `WorkspaceConfig.from_yaml_value`'s
own `dict(raw)` and raising a bare `TypeError`) with a
`"workspace: ..."`-prefixed message; `load_config` also wraps its whole
sequence of checks in a broad `except Exception` fallback (re-raising
`ConfigError` unchanged) as the general safety net for anything else,
matching the same "must never crash the caller" boundary
`write_audit_event`'s `OSError` handling and `_external_git_dir`'s
broad `except Exception: return None` already establish elsewhere in
this codebase.

**Verified the tests catch the regression:** reverted `config/models.py`,
`config/__init__.py`, `config/loader.py`, and `tools/execution.py` to
their pre-H30 versions and reran the new tests. All 5 new
`tests/config/test_loader.py` tests failed correctly (the `TypeError`
escaped uncaught; `audit.path`/`state_db_path`/nonexistent-`repository_roots`
all went unchecked). Both `tests/config/test_models.py` tests failed
with `DID NOT RAISE ValidationError` (`security`/`validate_remote`
still accepted). The `cwd`-traversal and non-positive-timeout
`tests/workspace/test_execution_tools.py` tests failed against real
Docker (`DID NOT RAISE ToolError`; a `timeout_seconds: 0` request
reached `asyncio.wait_for` and surfaced as a confusing `COMMAND_TIMEOUT`
instead of a clean `INVALID_ARGUMENT`). The timeout-clamp test wasn't
re-confirmed via a clean assertion failure on reverted code — the
reverted behavior honors the full unclamped 100s wait for a 60s
command, so it simply hangs well past any reasonable revert-verification
window instead of raising promptly; the hang itself is exactly the
predicted defect, so it was accepted as sufficient evidence rather than
tying up Docker for an extra 100+ seconds to watch it complete. Restored
the fix; all pass again. No Docker leftovers after either run.

No schema drift (server config, not the exported tool contract — no
tool output shape changed).

**Full relevant suite:** `tests/config/` (62 passed) +
`tests/workspace/test_execution_tools.py`/`test_async_execution_tools.py`
(43 passed, real Docker) — all clean. No Docker leftovers. `ruff check`,
`ruff format --check`, `mypy src` clean across the whole tree. The full
background `tests/` run was interrupted at the ~78% mark with zero
visible failures (the same recurring environmental pattern seen since
H19), leaving one orphaned container (`docker rm -f`'d, confirmed
clean); proceeding on the direct evidence above rather than retrying.

**New tests:** `tests/config/test_loader.py` — `audit.path`/
`state_db_path` under a repository root rejected; a `repository_roots`
entry that doesn't exist, or is a file rather than a directory,
rejected (plus the accepted, existing-directory case); `workspace: 5`
surfaces as `ConfigError`, not a raw `TypeError`.
`tests/config/test_models.py` — `security`/`git_policy.validate_remote`
both rejected as unknown fields. `tests/workspace/test_execution_tools.py`
— the literal `"/workspace/../etc"` exploit rejected, a legitimately
dotted-but-still-inside `cwd` still accepted; a client `timeout_seconds`
above the configured ceiling clamped (proven by elapsed wall-clock time,
not just the resulting error code); `timeout_seconds: 0`/negative
rejected. `tests/workspace/test_async_execution_tools.py` — the same
two `exec_start` fixes confirmed via its own shared helpers.

### H31 — Operational logging ✅
**Satisfies:** HR-031
**Commit:** `ce17f66`

- [x] Introduce `logging` with a configurable level
- [x] Log swallowed exceptions, container and ssh-agent lifecycle,
      reconciliation outcomes, and policy denials
- [x] Route secrets through HR-013's redaction
- [x] Replace `except Exception: exit_code = None` with a logged, typed handler

**Introduced `logging`, configurable, gap closed exactly as reported.**
`getLogger` appeared nowhere in this codebase before this task (`grep -rn
getLogger src/` — zero matches). New `LoggingConfig` (`level`, one of the
five standard names, defaulting to `INFO`) and `config/models.py`'s
`ServerConfig.logging` field; new `logging_config.py`'s `configure_logging`,
called once from `cli.py`'s `serve()` immediately after config validation
succeeds — `force=True` on the underlying `logging.basicConfig` call, since
its documented "no-op if the root logger already has a handler" behavior
would otherwise make a second `configure_logging` call in the same
process (a test-harness reusing one, most plausibly) silently keep
whichever level was configured first — caught by this task's own tests,
not assumed correct.

**Every module gets its own `logging.getLogger(__name__)`** — the
standard, idiomatic pattern, not a shared singleton — in each of the six
files this task actually touched.

**Every HR-031-named category, real instrumentation, not just the one
literal reproduced defect.**
- **Swallowed exceptions:** `execution/async_runner.py`'s
  `except Exception: exit_code = None` — the exact gap HR-031's own
  "Gap" paragraph names — now logs via `logger.exception(...)` (captures
  the traceback) before continuing to swallow it; the swallow itself is
  correct (a drain failure genuinely has no better recovery than
  reporting the job failed) and is unchanged, only its total invisibility
  is fixed. Also instrumented `filesystem/security.py`'s
  `_external_git_dir`'s own `except Exception: return None` (`debug`
  level — correctly still non-fatal, additive protection failing is not
  a reason to fail an unrelated filesystem request) and
  `workspace/manager.py`'s `reconcile_all`'s per-workspace
  `except ContainerRuntimeFailedError: continue` (a second genuine
  "swallowed exception," beyond the one literal defect named, found by
  reading through the codebase's own catch-all sites rather than only
  fixing the cited example).
- **Container lifecycle:** `workspace/manager.py`'s `_run_container`
  (create, success and failure), `start`, `stop`, `delete` — each now
  logs at `info` (success) or `warning` (failure).
- **ssh-agent lifecycle:** `workspace/ssh_agent.py`'s `_start`,
  `terminate`, and `reconcile`'s own restart-count summary.
- **Reconciliation outcomes:** `jobs/reconcile.py`'s `reconcile_jobs`
  (per-job `warning`, plus an `info` summary count) and
  `workspace/manager.py`'s `reconcile_all` (`info` summary, `warning`
  per skip — the same swallowed-exception log doing double duty as the
  reconciliation-outcome log for that specific case).
- **Policy denials:** `tools/git.py`'s `audit_denials` decorator now
  logs at `warning` alongside the audit event it already wrote — the
  same denial, both its client-operation-facing record (§24) and its
  operational-visibility one.

**Secrets routed through HR-013's redaction.** New
`logging_config.redacted_extra` — a thin, explicit wrapper over the
identical `errors.redact_secrets` HR-013 already applies to a
client-facing error and an audit record, not a third redaction
implementation — used specifically at the one log call whose `extra`
data (`tools/git.py`'s denial `metadata`) can carry a client-supplied
`remote`/`branch`. The `async_runner.py`/`filesystem/security.py` calls
deliberately do *not* route through it: their own arguments are
`job_id` (daemon-generated), `root` (a host path this daemon already
trusts), and Python's own exception repr via `exc_info` — not
`details`-shaped client data redaction is built for.

**`except Exception: exit_code = None` — logged; deliberately still
broadly typed, not narrowed.** "A logged, typed handler" is satisfied
by adding the logging (the actionable, unambiguous half) while keeping
`exit_code: int | None`'s existing, already-correct typing; the except
clause itself stays `Exception`, deliberately — this is the drain
path's last-resort safety net for a genuinely *unforeseen* failure
(the comment right above it already names two known-different paths —
`TimeoutError`, cancellation — handled separately); narrowing the
catch-all to a specific type would mean some *other* unexpected
failure escapes uncaught again, defeating the reason this handler
exists at all.

**Verified the tests catch the regression:** reverted all 9 changed
source files (and removed the new `logging_config.py` entirely) and
reran the new tests. `tests/test_logging_config.py` failed to even
import (`ModuleNotFoundError`). All 5 real per-category tests failed
for the right reason against real Docker/real processes: no
`"lost"`/`"job_1"` log record from `reconcile_jobs`; no `"created"`/
`"deleted"` container-lifecycle records; no `"started"`/`"terminated"`
ssh-agent records; the forced `drain_stream` failure produced zero log
records (`caplog.records == []`) even though the job still correctly
ended up `FAILED`; no `"denied"` policy record. Restored the fix; all
pass again. No Docker leftovers after either run. A genuine bug this
task's own testing caught before trusting it: the first
`configure_logging` draft (no `force=True`) made a second call in the
same process silently keep the first-configured level — caught by
`test_configure_logging_sets_the_root_level`'s own second-call
assertion, not assumed correct from reading `logging.basicConfig`'s
docs alone.

No schema drift (server config + operational logging, not the exported
tool contract — no tool output shape changed).

**Full relevant suite:** `tests/test_logging_config.py` (3 passed) +
`tests/jobs/test_reconcile.py` (13 passed) +
`tests/workspace/test_manager.py`/`test_ssh_agent.py`/
`test_async_runner.py`/`test_git_push_tool.py` (58 passed, real
Docker) — all clean. No Docker leftovers. `ruff check`, `ruff format
--check`, `mypy src` clean across the whole tree. The full background
`tests/` run was interrupted at the ~78% mark with zero visible
failures (the same recurring environmental pattern seen since H19),
leaving one orphaned container (`docker rm -f`'d, confirmed clean);
proceeding on the direct evidence above rather than retrying.

**New tests:** `tests/test_logging_config.py` — `configure_logging`
actually changes the root level on a *second* call (the bug this task's
own first draft had); `redacted_extra` genuinely delegates to
`redact_secrets`. `tests/jobs/test_reconcile.py` — a reconciliation
outcome is logged; a clean reconciliation (nothing non-terminal) logs
nothing (no false positives).
`tests/workspace/test_manager.py`/`test_ssh_agent.py` — container/
ssh-agent lifecycle logged by name and workspace id.
`tests/workspace/test_async_runner.py` — a forced, genuinely
unexpected `drain_stream` failure is captured at `ERROR` with a real
traceback (`exc_info`), not silently swallowed — the HR-031 "Gap"
paragraph's own literal reproduction.
`tests/workspace/test_git_push_tool.py` — a real policy denial through
the MCP tool layer is logged, not only audited.

### H32 — Close the remaining spec gaps ✅
**Satisfies:** `HARDENING_SPEC.md` §9
**Commit:** `ee98a5c`

- [x] Report OOM through a real surface — detection exists, reporting does not
- [x] Query GitHub rulesets, not only branch protection (§18.5, FR-032), and
      distinguish "confirmed absent" from "not visible to this token"
- [x] Ship pinned GitHub host keys, or document operator provisioning — the box
      is ticked, the file is created empty, and a fresh install cannot push
- [x] Make `GIT_AUTH_FAILED` reachable (it is the likeliest real push failure
      under deploy-key auth and currently surfaces as `COMMAND_FAILED`); same
      for `GIT_REFSPEC_NOT_ALLOWED`, `NETWORK_FAILED`, `INTERNAL`
- [x] Warn when `.devcontainer/devcontainer.json` is present and ignored (§30
      requires reporting, not support)
- [x] Validate `.mcp-workspace.yml`'s `version:` — `999` is currently accepted
      and recorded
- [x] Re-run setup steps after `reset`, or document that reset discards project
      configuration; a failed step currently leaves a permanently
      half-provisioned workspace that the next open silently accepts
- [x] Give `reset` and `rebuild` distinct behavior or merge them in the spec
- [x] Restore project `environment:` and cache mounts on `reset`/`rebuild`

Nine sub-items, one commit — each is a distinct gap `HARDENING_SPEC.md` §9's
amendment table names (the OOM/rulesets/§14.2/§14.4/§8.4 rows were already
read in full during H30/H31; the remaining rows land here), but none is large
enough alone to warrant splitting the ralph-loop discipline across nine
commits.

**1. OOM reporting.** `workspace/manager.py`'s `_state_from_docker_container`
already folded an OOM kill into the same generic `FAILED` state as any other
crash — detection existed, but `container_state: "failed"` alone could never
tell a client *why*. Added `WorkspaceRecord.oom_killed: bool` (a new
`workspace` column, migration v4→v5 in `db/schema.py`, following the exact
transaction-safety pattern HR-019 already established) and a companion
`_oom_killed_from_docker_container` read from the same `container.attrs`
snapshot `_state_from_docker_container` reads, at all three of that
function's call sites. Deliberately *not* a sticky "this workspace once
OOM'd" flag: it reflects the *current* container, reset to `False` whenever
a Docker-API-level failure (create/start/stop/restart raising) sets `FAILED`
for an unrelated reason, and when a container disappears entirely (`ABSENT`).
Surfaced on `workspace_open`/`workspace_info` via a new `oom_killed` field on
`WorkspaceInfoOutput` — contract bump.

**2. GitHub rulesets.** `github_api.py`'s `_query_branch_protection` only
ever queried `GET /repos/{owner}/{repo}/branches/{branch}/protection` — the
*classic* branch-protection endpoint, which 404s unconditionally on GitHub
Free private repositories regardless of whether protection exists, and the
old code treated that 404 as "confirmed missing" without evidence (exactly
the defect §9's §18.5 row names). Replaced it with `_query_branch_rules`,
querying the unified `GET /repos/{owner}/{repo}/rules/branches/{branch}`
endpoint instead — GitHub's own documentation states rulesets and classic
branch protection "work alongside each other, and all applicable rules are
enforced" for a branch, and this endpoint reports that combined,
already-translated set as one array of rule objects. GitHub documents only
`200` as a possible response for it; a `404` here has no documented "not
protected" meaning, so it is left to propagate as an `HTTPError` and degrade
to `unverified` ("not visible to this token") rather than asserting
`missing`. A genuinely rule-free branch (`200` with an empty array) *is*
real evidence and is reported as `missing`. Rule types map directly:
`non_fast_forward` → `force_push_blocked`, `deletion` → `deletion_blocked`,
`pull_request` → `requires_pull_request`.

**3. Pinned GitHub host keys.** `config/loader.py`'s
`ensure_known_hosts_file_exists` created an *empty* file — satisfying
neither half of §19.3's "`known_hosts_mcp` SHALL contain GitHub's published
SSH host keys, pinned. The daemon SHALL NOT rely on trust-on-first-use":
trust-on-first-use was avoided only by trusting nothing at all, so a fresh
install's first structured Git operation failed host verification before it
ever reached the (absent) auth stage. Added `GITHUB_SSH_HOST_KEYS`, GitHub's
current RSA/ECDSA/Ed25519 host keys, verified two independent ways before
being pinned — a live `ssh-keyscan -t rsa,ecdsa,ed25519 github.com` against
the real internet, byte-for-byte diffed against GitHub's own published
fingerprint documentation, matching exactly. Seeded whenever the file is
genuinely empty (does not exist yet, *or* exists at zero bytes — an install
from before this task, when the old code only ever `touch`ed it); a file
with any existing content is never touched, since that can only be an
operator's own deliberate customization or this same seed from an earlier
run.

**4. `GIT_AUTH_FAILED`/`GIT_REFSPEC_NOT_ALLOWED`/`NETWORK_FAILED`
reachable.** All three existed in `errors.py` but were never raised
anywhere — every push/pull transport failure fell through to a generic
`CommandFailedError`. New `git/transport.py` classifies the two transport-
level failures (shared by fetch and push, since both go through the same
underlying `ssh`/`http` client): `is_auth_failure` (`"Permission denied"`,
`"Authentication failed"`) and `is_network_failure` (`"Could not resolve
host"` — a substring of OpenSSH's own `"Could not resolve hostname"` too —
`"Connection refused"`, `"Connection timed out"`). Every marker was produced
empirically in this sandbox before being trusted: a real SSH auth rejection
against a real, publicly reachable host (reusing the same real-network
harness `tests/workspace/test_git_pull_tool.py`/`test_git_push_tool.py`
already established), a real HTTPS credential rejection against a real
GitHub repository, a real DNS failure against a `.invalid` hostname, a real
refused connection on an unbound loopback port, and a real SSH connect
timeout against a `192.0.2.1` (TEST-NET-1) address with a short
`ConnectTimeout`. Wired into `tools/git.py`'s fetch-failure site
(`git_pull_tool`) and push-failure site (`git_push_tool`), checked before
falling through to `CommandFailedError`, mirroring the existing
`is_non_fast_forward_failure` precedent exactly. Separately,
`git/push.py`'s `validate_branch_name` was raising `InvalidArgumentError`
for the exact rule §20.3 names (`GIT_REFSPEC_NOT_ALLOWED`'s own reason for
existing) — changed to raise `GitRefspecNotAllowedError` instead, the
error code that actually fits. `INTERNAL_ERROR`/`InternalError` remains
unreachable, deliberately: see `errors.py`'s own comment on the class —
wiring it up would mean a blanket `except Exception` in the tool layer,
directly contradicting `translate_workspace_errors`'s own documented design
("nothing from an unexpected exception reaches the client" — a real bug
SHALL crash loudly, not get silently reshaped into a generic, actionless
"unknown cause" error). The same shape of tension H30's three retracted
validators found; resolved the same way — explained, not forced.

**5. `devcontainer.json` warning.** New `devcontainer_json_is_ignored`
(`project_config.py`) — a plain existence check for
`.devcontainer/devcontainer.json`, independent of whether
`.mcp-workspace.yml` is also present (§9's precedence ranking `.mcp-
workspace.yml` above it does not make the second file's existence stop
mattering). The existing `capabilities.workspace.devcontainer: False`
server-wide flag only satisfies §30's "reported clearly" for a client that
already knows to check it against a repository it already knows has the
file; this is the per-repository half, surfaced as a new
`devcontainer_ignored` field on `WorkspaceInfoOutput`
(`workspace_open`/`workspace_info`) and audited on every `workspace_opened`
event.

**6. `.mcp-workspace.yml` `version:` validation.** `ProjectWorkspaceConfig.
version` was a bare `int` — `999` parsed cleanly and got recorded as the
workspace's `config_version` (§9.2's reproducibility record), exactly as
happily as `version: 1`, the only schema this module has ever actually
implemented. New `SUPPORTED_PROJECT_CONFIG_VERSIONS = frozenset({1})` and a
`field_validator` rejecting anything else with a clear message naming what
*is* supported.

**7/8/9. `reset`/`rebuild`: setup re-run, distinct behavior, restored
environment/caches.** All three live in the same `cli.py` code path and
were fixed together. Before this task, `DockerWorkspaceManager.rebuild` and
`.reset` were byte-for-byte identical (`§9`'s own §8.4 row: "they are one
code path"), and `cli.py`'s `_recreated_run_kwargs` deliberately omitted
`.mcp-workspace.yml`'s `environment:` and cache-volume mounts, reasoning
they were "tied to state this standalone operator command has no reason to
duplicate" — true of an `ssh.mode: agent` socket (a live process, genuinely
not reconstructible without one) but not of `environment:` or a cache
volume's name, both pure functions of `repository_id` and the project
config alone (`resolve_caches`/`cache_volume_name`), the same values
`workspace_open` itself would compute. Fixed: `_recreated_run_kwargs` now
builds `extra_env` and cache mounts the same way `workspace_open` does, and
returns the loaded `ProjectWorkspaceConfig` alongside `(image, run_kwargs)`.
`rebuild` now pulls the image fresh before recreating the container (best-
effort — a locally-built-only image, this project's own default
`docker/default/Dockerfile` included, was never pushed anywhere, so a pull
failure there is expected, not fatal, and falls back to the cached image
with a warning); `reset` does not, matching its own documented "discard
container-local state, keep everything else exactly as it was" semantics.
Both now re-run `.mcp-workspace.yml`'s `setup` steps against the freshly
recreated container afterward — extracted from `tools/workspace.py`'s
`_run_setup_steps` closure into a module-level `run_setup_steps` function
(now a thin closure wrapper there) so `cli.py` can call the identical logic
without a second implementation. Without this, a `setup` step could never
run again for an *already-existing* workspace id: `workspace_open`'s own
`was_created` check only asks "did a DB row already exist," which a CLI
`reset`/`rebuild` never changes — the container comes back genuinely fresh
but the next `workspace_open` silently treats it as already-provisioned,
permanently. `_run_async`, a small helper, runs the now-needed async setup
step logic from these plain-`def` click commands correctly whether or not
an event loop is already running (real in this task's own async test
suite, driving `main` through `CliRunner` from inside `async def` tests;
never true in real standalone CLI use) rather than assuming `asyncio.run`'s
usual "no loop yet" precondition always holds.

**Revert-verification:** backed up every changed source file to the
scratchpad, reverted each to `git show HEAD:<file>` in turn, confirmed the
corresponding new test(s) failed for the right reason, restored from
backup. Notably: `git/push.py` reverted → `test_validate_branch_name_
rejects_dangerous_or_invalid_names` raised `InvalidArgumentError` instead of
`GitRefspecNotAllowedError` (wrong type, not just wrong message).
`workspace/manager.py`/`db/schema.py` reverted together (the migration and
the field are inseparable) → `test_at_h032_a_real_oom_kill_is_reported_
distinctly_not_just_as_failed` failed with `AttributeError:
'WorkspaceRecord' object has no attribute 'oom_killed'`. `git/transport.py`
removed + `tools/git.py` reverted together → `tests/git/test_transport.py`
failed to even import (`ModuleNotFoundError`), and the two real-network
`..._reaches_the_real_github_auth_stage_cleanly` tests in
`test_git_pull_tool.py`/`test_git_push_tool.py` failed on `assert
"GIT_AUTH_FAILED" in message`, the real GitHub auth rejection falling back
to `COMMAND_FAILED` exactly as before this task. `github_api.py` reverted
alone → 6 of 13 tests in `tests/test_github_api.py` failed with
`AttributeError: 'list' object has no attribute 'get'` — the old classic-
endpoint parsing code choking on the new tests' rules-array-shaped response
bodies, not a silent pass. `config/loader.py` reverted → `tests/config/
test_loader.py` failed to even import (`ImportError: cannot import name
'GITHUB_SSH_HOST_KEYS'`), the whole file's test dependency on the new
constant. `project_config.py` reverted → `tests/test_project_config.py`
likewise failed to import (`ImportError: cannot import name
'devcontainer_json_is_ignored'`). `tools/workspace.py` reverted alone →
`cli.py` failed to import (`ImportError: cannot import name
'run_setup_steps'`). `cli.py` reverted → all four new
`test_at_h032_*` tests in `test_cli_lifecycle.py` failed: no setup-step
re-run, no pull-attempt warning distinguishing `rebuild` from `reset`, no
restored `RUST_BACKTRACE`/cache-volume mount. All restored; full relevant
suite passed again on each restore.

No Docker leftovers after any run in this task.

**Schema drift:** `workspace_open`/`workspace_info` gained `oom_killed` and
`devcontainer_ignored` — `CONTRACT_VERSION` bumped `0.7.4`→`0.7.5` (patch:
purely additive fields, no new tools; also backfilled the missing 0.7.3/
0.7.4 history-comment entries H24/H29 never added). `remote_protection`'s
own shape is unchanged (item 2 only changed how it's computed, not what it
reports), so no separate bump for that. Re-exported and diffed against a
throwaway config — exactly the expected diff (version bump plus the two new
fields on both tools' output schemas), nothing else.

**Full relevant suite:** `tests/git/` + `tests/workspace/` + `tests/config/`
+ `tests/db/` + `tests/test_github_api.py` + `tests/test_project_config.py`
+ `tests/test_cli.py` (616 passed, real Docker throughout) — clean. Full
unscoped `tests/` background run also completed cleanly this time (no
interruption at the usual 60-83% mark). `ruff check`, `ruff format --check`,
`mypy src` clean across the whole tree. No Docker leftovers.

**New tests:** `tests/git/test_transport.py` — marker-string coverage for
`is_auth_failure`/`is_network_failure`, plus two tests reproducing a real
DNS failure and a real refused connection rather than hand-typed stderr.
`tests/workspace/test_manager.py` — a real cgroup OOM kill (16 MiB limit,
128 MiB allocated) is reported distinctly; an ordinary non-OOM exit under
the same `FAILED` state is not. `tests/test_github_api.py`/`tests/workspace/
test_remote_protection.py` — a `404` from the rules endpoint is `unverified`
(not `missing`); a genuinely empty rules array is `missing`; rule-type
mapping for all three checks; `require_remote_protection: deny` treats a
`404` as `unverified`/warn, not a hard failure, through the full
`workspace_open` path. `tests/config/test_loader.py` — a fresh install gets
the real pinned keys; an existing-but-empty file (the pre-fix state) gets
seeded too; an existing non-empty file is untouched; a real `ssh` connection
to real `github.com` using exactly the seeded file passes host verification.
`tests/test_project_config.py` — `999`/`0`/`-1`/`2` all rejected;
`devcontainer_json_is_ignored` true/false/alongside-a-configured-`.mcp-
workspace.yml`. `tests/workspace/test_project_config_integration.py` — a
present `devcontainer.json` is reported on both `workspace_open` and
`workspace_info`; absent is not. `tests/workspace/test_cli_lifecycle.py` —
`rebuild` attempts a pull (visible via its fallback warning against the
locally-built-only test image) and `reset` does not; a `setup` step re-runs
on `reset` (proven against a workspace whose step already ran once, not
merely that it runs the first time); a failing post-reset setup step is a
clear CLI error, not a crash; `RUST_BACKTRACE` and a cache-volume write both
survive `reset`, with the volume confirmed to be the *same* one (not
recreated).

### H33 — Documentation matches the shipped system ✅
**Satisfies:** HR-032
**Commit:** `32e8831`

- [x] `docs/TOOLS.md`: list all 21 tools (six structured Git tools are missing),
      drop "the six future structured Git tools", fill in `contract_version`
      0.7.2
- [x] `docs/CONFIGURATION.md`, `SECURITY.md`, `THREAT_MODEL.md`: write them or
      state plainly that `docs/SPEC.md` is the only reference
- [x] `docs/DEVELOPMENT.md`: `--config` no longer merely checks existence
- [x] Add a docs check to CI so the tool table cannot drift again

`docs/TOOLS.md` was missing all six structured Git tools (`git_status`,
`git_diff`, `git_log`, `git_commit`, `git_pull`, `git_push` — S19-S27's own
entire deliverable) and still called them "future," even though `docs/
SPEC.md` §34.1 itself already says "21 tools." Rewrote it to list the full,
current 21-tool surface, grouped by area (repositories, filesystem,
workspace/server state, execution, structured Git) rather than one flat
table — with 21 entries and six of them added in one pass since this
task, grouping is what keeps the table itself scannable. Dropped the
`contract_version` bullet's literal "0.7.2" in favor of pointing at
`schemas/tools.json`'s own field as the authoritative current value: the
file already avoided hardcoding it once (`docs/TOOLS.md`'s own prior
version deliberately said "`contract_version` in `schemas/tools.json`"
rather than a number) — hardcoding it now would just recreate the same
staleness this task exists to fix, since H24/H29/H32 alone bumped it three
times past whatever number would have been written down today.

`docs/DEVELOPMENT.md` claimed `--config` "currently only checks the file
exists; full validation lands with S2" — S2 landed long before H1, and
`load_config` (`config/loader.py`) has done full Pydantic validation
against §52's schema, plus the HR-006/HR-007/HR-010 invariant checks, this
entire session. Corrected to describe what actually happens: a malformed
or invalid config prints a clear `error: ...` message and exits nonzero
(§73 step 1), not a bare existence check.

`docs/CONFIGURATION.md`/`SECURITY.md`/`THREAT_MODEL.md` already took the
"state plainly `docs/SPEC.md` is the only reference" option this task's
own bullet allows — but each said so as a *temporary* placeholder ("will
become a field-by-field reference... once S2 lands," "will grow into
deployment-facing security guidance... once the corresponding phases
land"), promises that were never fulfilled even though S2 and every phase
they gestured at have long since landed. Rewrote all three to state the
same thing as a settled, permanent design decision instead of a stale
IOU — `docs/SPEC.md` (and `docs/HARDENING_SPEC.md`, where relevant) *is*
the reference this project maintains, not a placeholder waiting to be
promoted into a second copy of it. `CONFIGURATION.md` and `SECURITY.md`'s
own section pointers were re-checked against the current `docs/SPEC.md`
section numbers (§7/§9/§11/§18.9/§19/§26/§39/§53/§74) rather than assumed
still correct; `SECURITY.md` gained an explicit pointer to
`docs/HARDENING_SPEC.md`'s HR-001..HR-033, which post-dates the original
placeholder text.

New CI step, `docs/TOOLS.md drift check (HR-032)`: extracts every
backtick-quoted tool name from `docs/TOOLS.md`'s markdown table rows via
`grep -oP` and diffs it, both directions, against `jq -r '.tools[].name'
schemas/tools.json` — a tool present in one list but not the other fails
the build. Bidirectional deliberately: the actual historical defect this
task fixes was tools *missing* from the docs, but a stale row left behind
after a future rename/removal is the same class of drift and deserves the
same guard, not a narrower one that only prevents recurrence of the exact
failure already found.

**Revert-verification:** removed one row (`git_push`) from `docs/TOOLS.md`
and reran `tests/test_docs_tools.py` — `test_every_registered_tool_is_
documented` failed with `tool(s) registered but not in docs/TOOLS.md:
['git_push']`, the exact right-reason failure; restored, all 3 pass again.
The CI step's own `grep -oP` pattern was verified directly against both
the real `docs/TOOLS.md` (21 names, matching `jq`'s 21 names byte-for-byte)
and against the row-removed version (20 vs 21, diff exits nonzero) before
being trusted, not merely assumed to parse correctly from reading the
regex.

No Docker leftovers. `ruff check`, `ruff format --check`, `mypy src` clean
across the whole tree — this task touched no `src/` behavior, only `docs/`,
`.github/workflows/ci.yml`, and one new test file, so the blast radius was
narrow, but the full unscoped `tests/` background run was still run in
full per this loop's own established discipline: 876 passed (873 before
this task's 3 new tests), no interruption, no leftovers.

**New tests:** `tests/test_docs_tools.py` — every tool the real, live
server actually registers (via `create_server(...).list_tools()`, not the
committed `schemas/tools.json`, so this catches the docs drifting from the
*source* even in the hypothetical where the schema export itself were
also stale) has a row in `docs/TOOLS.md`; every row in `docs/TOOLS.md`
corresponds to an actually-registered tool (the reverse direction); a
meta-test (HR-027's "coverage checks SHALL NOT satisfy themselves" lesson,
applied here) that the extraction regex found at least 20 names at all,
so a broken pattern matching zero rows can't make the other two tests
pass vacuously.

### H34 — Apply the `docs/SPEC.md` amendments ✅
**Satisfies:** HD2, `HARDENING_SPEC.md` §9
**Commit:** `be8abee`

- [x] Apply all sixteen amendments in `HARDENING_SPEC.md` §9
- [x] Bump `docs/SPEC.md`'s version and date
- [x] Note in `HARDENING_SPEC.md` §2 that precedence has returned to
      `docs/SPEC.md` for each amended point

A documentation-only task (no `src/`/`tests/` change) — each of the sixteen
`HARDENING_SPEC.md` §9 rows was applied to the exact `docs/SPEC.md` section
it names, re-verified against the actual current implementation before
being written (not transcribed from the amendment table's own summary
column, which is a defect description, not amendment prose ready to paste
in) — several amendments turned out to need correction against what the
code actually does, caught only by checking:

- **§5.3** — the root refusal now explicitly covers `.mcp-workspace.yml`
  `environment:` (HR-006's `MCP_UID` bypass), not only `container.user`.
- **§8.4** — `reset`/`rebuild` given the real operational distinction H32
  implemented (pull-first vs. not; both re-run `setup` steps).
- **§11** — the "per workspace/repository/global" `SHALL` downgraded to
  what's actually implemented (per-repository only); HR-009's
  collision-free naming and the "no trusted-executable-input" property
  both stated explicitly, where before neither was.
- **§12.1** — repository-root and nested-`.git` refusals for `delete_file`
  added, checked against `filesystem/service.py` directly: the root case is
  `INVALID_ARGUMENT`, *not* `PATH_GIT_METADATA` as first drafted (the code
  raises `PathGitMetadataError` only for the *nested* case) — caught by
  reading `delete_file`'s actual raise sites rather than assuming.
- **§14.2/§14.4** — normalized-path cwd containment and client-timeout
  clamping, matching H30's fix (the amendment table's own suggested HR-030
  citation for the cwd fix turned out to be wrong — HR-030 is "adversarial
  coverage," unrelated; there is no dedicated HR number for this specific
  row, only the §9 table entry itself and H30's commit, so that's what's
  cited instead of a fabricated-sounding one).
- **§15.5** — the 64 KiB response default stated explicitly, distinguished
  from §15.6's on-disk capture limits by name.
- **§18.5** — the unified rules endpoint and the confirmed-absent-vs-
  not-visible distinction from H32 stated (this section already said
  "rulesets," so the defect was narrower than the table's summary implies
  — the missing piece was the confirmed/unverified distinction, not the
  word "rulesets" itself).
- **§18.7** — replaced with HR-002's actual four-part control, checked
  against `git/service.py`'s real neutralization list rather than
  reconstructed from memory, plus the "governs where any future mechanism
  falls short" framing the amendment specifically asks for.
- **§19.1** — the key-file refusal now explicitly covers `ssh.shared_key`,
  not only per-repository `ssh_key` (HR-007).
- **§20.1** — HR-001/HR-002 cited explicitly, and `workspace_open`/
  `workspace_info`'s own path through `read_basic_git_state` named as
  reaching the same host-side read code, not only the three read tools.
- **§20.2** — HR-014's `pushurl`/`pushInsteadOf` push-destination
  resolution added, including the "both SHALL be validated when they
  differ" nuance (verified against `git/remote.py`'s actual
  `validate_remote_identity_for_push` docstring, which validates both, not
  the push URL alone as a first-draft reading of the amendment summary
  might suggest).
- **§20.7** — `policy` added to the example audit record.
- **§24** — rewritten from an aspirational "recommended events" list to
  what is actually emitted (`workspace_opened`, `workspace_stopped_idle`,
  `repository_authorization_failure`, `exec`, `ssh_auth`, `git_commit`/
  `git_pull`/`git_push`), with file-write/file-delete/container-lifecycle/
  cancellation audit events named as a real, undone gap rather than
  claimed — the amendment's own "or amend AT-021 to a scenario the system
  can actually produce" alternative turned out unnecessary: AT-021 ("run a
  command containing a configured secret, verify audit output doesn't
  expose it") is already satisfied by the `exec` audit event H26 added,
  confirmed by reading that event's own code comment, which cites AT-021
  by name. §24.1's redaction layer described as fixed, not configurable —
  `redact_secrets` has no config knob, confirmed by reading it.
- **§26** — OOM given its real reporting surface from H32
  (`oom_killed`); disk/pid exhaustion stated plainly as undetected, not
  downgraded to a vague "SHOULD" that implies partial coverage.
- **§44.1** — reordered to match §73's real sequence (jobs → containers →
  agents, confirmed against `cli.py`'s actual `serve()` body, not assumed
  from the amendment table's one-line summary), and step "6" (inspect
  running jobs) folded into the single unconditional "mark lost" step it
  actually is — `reconcile_jobs`' own pid-liveness check informs a log
  message, not the job's resulting state, so describing it as two
  separable steps (inspect, then decide) misstated what the code does.

`docs/SPEC.md` bumped **0.1 → 0.2**, dated 2026-08-27, with a version-line
pointer to this task. `docs/HARDENING_SPEC.md` §2 gained an explicit
"precedence has returned to `docs/SPEC.md`" note naming all sixteen
sections.

**Revert-verification:** not applicable in the usual sense — this task
changed no executable code or test, only prose, so there is no source
behavior for a revert to break a test against. Verified instead by
`tests/test_acceptance_test_coverage.py` (which parses `docs/SPEC.md` for
every `AT-nnn` heading and cross-references citations) and `tests/
test_docs_tools.py` continuing to pass unmodified — confirming no `###
AT-nnn`/tool-table structure this task's prose edits sit alongside was
accidentally disturbed — plus a direct structural check (`## \d+\.` header
count: 78, sequential 1-78, matching before and after) that no section was
duplicated, dropped, or renumbered by an editing mistake.

No Docker leftovers (this task touched no Docker-adjacent code, but the
established discipline's own checks were still run). `ruff check`, `ruff
format --check`, `mypy src` clean — unaffected, since no `src/` file
changed. Full unscoped `tests/` background run completed cleanly, same
876 passed as H33 left it (this task added no new tests of its own, since
there is no new behavior to test — only existing structural checks
re-confirmed).

### H35 — Reconcile the checkboxes ✅
**Satisfies:** HR-033
**Commit:** `d4dda22`

Untick or qualify these in `docs/TODO.md`, each with a pointer to the H-task
that closes it. The review's finding was not that tasks were unbuilt — most were
built substantially — but that a box is ticked and the prose beneath then walks
the claim back.

| Task | Correction |
| --- | --- |
| S2 | §19.1 invariant unguarded for `shared_key` (H7); §73 step 7 unimplemented (H30); invalid YAML escapes as `TypeError` (H30) |
| S3 | Migrations not atomic (H17); no concurrency configuration (H17) |
| S6 | §69 reconciliation reaches `absent` but never recovers (H15) |
| S9 / S10 | `max_patch_bytes` unenforced and `patch_file` truncation destroys data (H5); `delete_file("")` wipes the repository (H4); §12.1 rule 3 loses its specific `PATH_GIT_METADATA` classification (a generic `PATH_OUTSIDE_REPOSITORY` instead) when only `repositories[]` is configured — the request is still correctly refused, this is a classification gap, not a bypass (H37) |
| S12 | `cwd` check bypassable, timeout unclamped, no Docker error handling (H19, H30) |
| S15 | "no race between running and having a pid" is false ~95% of the time; `update_output_counts` is dead code |
| S21 | Argument injection (H2); bounded-diff offsets break on non-ASCII (H24) |
| S22 | Commits through unresolved conflicts (H20); pathspec magic defeats strict mode (H21); failed commit leaves paths staged (H22) |
| S24 | "Pinned GitHub host keys" ships zero keys (H32) |
| S28 | Rulesets never queried (H32) |
| S29 | Redaction covers `details` only (H11); denial fidelity below §20.7 (H13) |
| S31 | OOM reporting absent; pid exhaustion has no daemon-side detection — genuinely unimplemented, uncredited to any H-task (H32's own nine items do not address it; the prior citation of H32 here was itself inaccurate) |
| S33 | AT-037 has no test, AT-021's scenario is unreachable, coverage audit self-satisfies (H25, H26) |

S32's deferral was verified as accurate against §22.1, §22.2, §20.6 and §34.3 —
no correction needed.

For every row above, added a `**Correction (H35, ...HR-033):**` paragraph to
`docs/TODO.md` directly under the relevant task — no checkbox was unticked,
since in every case the underlying work described by the box was genuinely
done and the correction is about a defect *found later*, closed by a
specific, cited H-task, not about the original task's claim being false at
the time it shipped. This follows the exact precedent H26 already set in the
same file (its own `**Correction (H26, ...HR-028):**` paragraph on S33),
rather than introducing a new convention.

**Not a transcription exercise — every citation was re-verified against the
current code before being written**, and three did not survive that check
unchanged:

- **S9/S10's own listed defect ("§12.1 rule 3 fails open") had no H-task
  citation in the table above**, unlike every other row — checked why, by
  reproducing it directly (a real linked worktree, `authorized_roots=[]`).
  **The empirical result contradicts "fails open":** the request is still
  correctly refused, via the always-active plain containment check, since
  the resolved target is by construction outside the workspace root. What
  is actually lost is the specific `PATH_GIT_METADATA` classification (a
  generic `PATH_OUTSIDE_REPOSITORY` instead) — a real, still-open, and
  still-unscheduled gap, but a narrower one than the table's own wording
  implies, and one no H-task closes. Documented precisely as found (not
  as the more alarming "fails open" phrasing) and tracked as new **H37**
  in this file's own Deferred section, rather than silently left as an
  implication with no owner.
- **S15's "no race" claim was re-checked against the current code and
  found accurate today** — `AsyncJobRunner.start_job` calls `job_store.
  mark_running(job_id, pid=pid)` exactly once, with the real pid already
  obtained; it is the only call site in the codebase, and none passes
  `pid=None`. Whatever version the review evaluated had this race; the
  shipped one does not. Documented as re-verified-and-accurate rather
  than corrected, since there is nothing left to correct.
- **S31's table citation of H32 for "pid exhaustion has no daemon-side
  detection" is itself inaccurate** — checked against H32's actual nine
  items (`docs/HARDENING_TODO.md`, this file), none of which address pid
  exhaustion. Confirmed still genuinely unimplemented by grepping the
  manager/tool modules directly. Documented as still open and uncredited
  to any H-task, matching disk exhaustion's own already-honest "not
  implemented" framing, rather than repeating the table's own
  mis-citation into `docs/TODO.md`.

**Revert-verification:** not applicable — a docs-only task, same as H34;
verified instead by `tests/test_acceptance_test_coverage.py` and `tests/
test_docs_tools.py` continuing to pass unmodified after the edits (neither
`### AT-nnn` headings this task's prose references nor `docs/TOOLS.md`'s own
table were disturbed).

No Docker leftovers (checked per the established discipline; this task
touched no Docker-adjacent code). `ruff check`, `ruff format --check`,
`mypy src` clean — unaffected, no `src/` file changed. Full unscoped
`tests/` background run completed cleanly: 876 passed, same as H33/H34
left it (this task adds no new tests, since there is no new behavior to
test — only existing documentation).

**New tracked item:** **H37** (this file's own Deferred section) — the
§12.1 rule-3 classification gap found above, with its fix already scoped
(extend `authorized_roots` to include every individually configured
`repositories[].path`, not only `repository_roots`, at all four call
sites) for whoever picks it up.

### H37 — §12.1 rule 3 classification gap when only `repositories[]` is configured ✅
**Commit:** `9c0fbd5`

- [x] `config/models.py`: add `ServerConfig.authorized_git_dir_roots`, the union
      of `repository_roots` and every configured `repositories[].path`
- [x] `server.py`: pass it, not raw `config.repository_roots`, into both
      `filesystem_tools.register(...)` and `workspace_tools.register(...)`
- [x] `cli.py`: same, in `_recreated_run_kwargs`'s `build_workspace_mounts` call
- [x] `tools/filesystem.py`'s two call sites and `tools/workspace.py`'s: no
      change needed — both already receive `authorized_roots`/`repository_roots`
      as a parameter from `register()`, so `server.py`'s fix reaches them
      transitively

`RepositoryRegistry` (`repositories.py`, own module docstring) has always
treated `repositories:` and `repository_roots:` as the two equally-authorized
sources of a repository. `resolve_git_dir`/`FilesystemSecurity`'s
`authorized_roots` — a different, narrower list gating whether a *resolved
external Git directory* (§12.1 rule 3: a linked worktree's or submodule's real
`.git`, which lives outside its own checkout) is trusted — never reflected
that: every one of the four call sites this file's own Deferred entry named
was passed `config.repository_roots` alone. A repository authorized only
through `repositories:`, with no `repository_roots:` configured, was never in
its own `authorized_roots` — and since `_is_under_any`'s check is "is the
resolved directory under *some* authorized root," this only recovers when a
*second* `repositories[]` entry's path happens to be an ancestor of the
target (e.g. a linked worktree's main repo, also individually configured) —
plausible, ordinary configurations.

**Empirically verified this is two distinct defects of different severity,
not one — H37's own Deferred write-up (and `docs/TODO.md`'s S9/S10
correction it cites) only characterized the milder of the two:**

- **`tools/filesystem.py`'s two call sites** (`FilesystemSecurity`
  construction): as already documented, a classification-only gap.
  `filesystem/security.py`'s `_external_git_dir` wraps `resolve_git_dir` in a
  broad `except Exception: return None` (deliberately best-effort — its own
  docstring), so the unauthorized-root failure is swallowed; rule 3 goes
  inert, but the always-active plain containment check still refuses an
  escaping request, just as `PathOutsideRepositoryError`
  (`PATH_OUTSIDE_REPOSITORY`) instead of the more specific
  `PathGitMetadataError` (`PATH_GIT_METADATA`). Reproduced directly: a
  worktree checkout with a symlink resolving into its main repo's real
  `.git`, `authorized_roots=[]` — `fs.resolve(...)` still raises, just the
  coarser error.
- **`tools/workspace.py`'s call site (`open_workspace` → `build_workspace_
  mounts`) and `cli.py`'s `_recreated_run_kwargs` (the same function) — a
  genuine functional bug, not merely a classification gap.**
  `workspace/mounts.py`'s `build_workspace_mounts` calls `resolve_git_dir`
  with **no** `try`/`except` around it — nothing swallows the
  `PathOutsideRepositoryError` `resolve_git_dir` raises when the resolved
  common directory isn't under any authorized root. Reproduced directly: the
  identical worktree/main-repo fixture, `authorized_roots=config.
  repository_roots` (empty, no `repository_roots:` configured) —
  `build_workspace_mounts` raised outright. Concretely: `workspace_open` (and
  the CLI's `reset`/`rebuild`, which shares this same mount-building call)
  could not open a workspace **at all** for a linked-worktree or submodule
  repository authorized only via `repositories:` — not a coarser error code,
  a hard failure. This is worse than either this file's Deferred entry or
  `docs/TODO.md`'s S9/S10 correction described; both were written from the
  `tools/filesystem.py` call path alone.

Fix: `ServerConfig.authorized_git_dir_roots`, a new read-only property
computing `[*repository_roots, *(repo.path for repo in repositories)]` once,
threaded through the existing `repository_roots`/`authorized_roots` parameter
names at every call site — no downstream signature changes needed, since
`tools/filesystem.py` and `tools/workspace.py` already receive this value as
a parameter from `register()`, not by reading `ServerConfig` directly.

**Revert-verification:** reverted `config/models.py`, `server.py`, `cli.py`
to `HEAD` (backed up first), reran the four new tests below — 3 failed with
`AttributeError: 'ServerConfig' object has no attribute
'authorized_git_dir_roots'`, the exact right-reason failure (the fourth,
`test_h37_repositories_only_authorization_downgrades_rule_3_to_plain_
containment`, exercises `FilesystemSecurity` directly with hand-built
`authorized_roots` lists rather than the new property, so it correctly
stayed green — it proves the underlying mechanism, not the `ServerConfig`
wiring). Restored from backup; all four passed again.

Broader-relevant suite (`tests/config/`, `tests/workspace/`,
`tests/filesystem/`, `tests/test_cli.py`) run via a background task: 549
passed, 0 failed. `ruff check`, `ruff format --check`, `mypy` clean on every
changed file (one pre-existing, unrelated `unused-ignore` mypy note in
`tests/workspace/test_mounts.py`, confirmed present before this task's edits
too via `git stash`). No Docker leftovers (`docker ps -a --filter
"name=mcp-dev-"` and `docker volume ls` both empty of test artifacts) after
either run. Full unscoped `tests/` background run: **974 passed, 0 failed**
(970 before this task's 4 new tests).

**New tests:**
- `tests/config/test_models.py::test_h37_authorized_git_dir_roots_combines_
  both_sources` / `..._is_empty_when_neither_source_is_configured` — the
  property itself, pure `ServerConfig` construction, no filesystem needed.
- `tests/workspace/test_mounts.py::test_h37_linked_worktree_authorized_only_
  via_repositories_fails_workspace_open_without_the_fix` — the more severe
  finding: a real `ServerConfig` with two `repositories:` entries (main repo
  + linked worktree) and no `repository_roots:`; `build_workspace_mounts`
  raises with `config.repository_roots`, succeeds and returns the correct
  two mounts with `config.authorized_git_dir_roots`.
- `tests/filesystem/test_security.py::test_h37_repositories_only_
  authorization_downgrades_rule_3_to_plain_containment` — the milder
  finding: the same worktree/main-repo shape, proving `authorized_roots=[]`
  downgrades `PathGitMetadataError` to `PathOutsideRepositoryError` (not a
  bypass) while `authorized_roots=[main_repo]` restores the specific
  classification.

---

## In progress

### H36 — Move Git reads into the container 🚧
**Supersedes:** HD1

Reverses HD1's original decision, on explicit request — the reasons HD1 gave
(§20.1's speed exception, §73's degraded start, `git_dir_mode: none` keeping
`git_status`/`git_diff`/`git_log` working) are real, accepted costs, not
oversights; see this task's own commits for the full before/after and the
`git_dir_mode: none` consequence specifically (previously undocumented even in
HD1/H36's own deferred note).

Scope, settled before implementation started: **full migration**, not only the
five tool-reachable reads (`git_status`, `git_diff`, `git_log`,
`workspace_open`, `workspace_info`) — `run_git_host_side` is also used
*internally* by `git_commit`/`git_pull`/`git_push`'s own precondition checks
(default-branch detection, fast-forward checks, remote-URL resolution,
tracking-branch checks), which are just as client-reachable (HR-000's own
framing) and need to migrate too for `run_git_host_side`/HR-002's control
surface to actually be deletable, matching this task's own "eliminates the
host-side execution class structurally" rationale rather than only relocating
part of it.

- [x] (1/8) `ExecutionService.capture` + `git/service.py`'s
      `run_git_in_container` — the shared in-container primitive every other
      step reuses. **Commit:** `e89fdc0`
- [x] (2/8) `git/status.py` (`read_git_status`) + its five internal call sites
      in `run_git_commit`/`run_git_pull`/`run_git_push` + `git_status_tool`
      (now requires a running workspace, matching `git_commit`/`git_pull`/
      `git_push`). **Commit:** `fab8b1e`
- [x] (3/8) `git/diff.py` (`read_git_diff`) + `git_diff_tool`. **Commit:**
      `dbde877`
- [x] (4/8) `git/log.py` (`read_git_log`) + `git_log_tool`. **Commit:**
      `9a67350`
- [ ] (5/8) `git_state.py` (`read_basic_git_state`) + `tools/workspace.py`'s
      `build_workspace_info`/`open_workspace`/`workspace_info_tool` (git block
      empty when the workspace is not currently running, not only when the
      repository is unborn/invalid)
- [ ] (6/8) `git/commit.py`'s `read_committed_files`/`detect_default_branch`/
      `is_head_published`
- [ ] (7/8) `git/remote.py`'s `resolve_remote_url`/`resolve_remote_push_url`/
      `validate_remote_identity`/`validate_remote_identity_for_push`
      (preserving RR-002's two-read TOCTOU timing — validate early, re-verify
      immediately before the push, refuse on disagreement — now with both
      reads in-container instead of one host-side and one in-container),
      `git/push.py`'s `build_force_with_lease_arg`, `tools/git.py`'s own two
      remaining inline calls
- [ ] (8/8) Delete `run_git_host_side` and the HR-002 control surface
      (`_CODE_EXECUTING_CONFIG_KEY_RE`, `_discover_code_executing_overrides`,
      `_host_side_git_env`, the env-scrub tuples) once no caller remains;
      amend `docs/SPEC.md` §20.1 (drop the read-only host-side exception),
      §73 (degraded start no longer covers `workspace_info`/Git reads), the
      `git_dir_mode` table (`read-only`/`none` rows — all six structured Git
      tools now run in-container, so `none` masks Git from them too); amend
      `docs/HARDENING_SPEC.md` §4/§4.1 (HR-002 superseded — nothing left for
      it to govern; HR-001's option-injection protection stays, reframed as
      in-container rather than host-side)

---

## Definition of done

All six critical defects are closed and covered by HAT-001..HAT-008; the full
suite passes with no Docker-dependent test skipped; a suite run leaves foreign
Docker state untouched; `docs/SPEC.md` carries the §9 amendments; and no ticked
checkbox in either TODO overstates what the code does.
