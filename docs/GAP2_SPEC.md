# Gap 2 Spec

**Source:** code review of `docs/GAP_TODO.md` G1–G9 plus a general review of
the four largest source modules, 2026-08-30, against `9bc44fd`.

**Series:** `G2R-001`..`G2R-022` (requirements), `G2AT-001`..`G2AT-018`
(acceptance tests).

All nine `GAP_TODO.md` tasks are genuinely implemented, committed one per
commit in the recommended order, and the suite is green (1019 passed,
verified on a clean run with no concurrent Docker activity). This document
is not a re-litigation of that work.

It exists because **G2 does not close the vulnerability it was written
for.** The denylist at `src/dev_workspace_mcp/project_config.py:46`
blocks the variables `docs/GAP_SPEC.md` GR-004 enumerated and nothing
else, and a bypass was reproduced against the current tree in a live
container. Two further findings are regressions the `GAP_TODO.md` pass
itself introduced. The remainder are correctness, security, and
integrity defects found while verifying it.

## A note on adding a seventh document

`docs/GAP_SPEC.md`'s own preamble records `docs/REVIEW_TODO.md`'s warning
that the marginal review layer costs more than it catches, and adds:

> If a finding about **this** document emerges while working it, it goes
> in a correction paragraph here, per `REVIEW_TODO.md`'s rule — not a
> sixth layer.

That rule is honoured here rather than stepped over. **Three findings of
this review are about `docs/GAP_SPEC.md`'s own prose** — GAT-006 is
unsatisfiable as written, GAT-007 contradicts GR-007, and GR-007
contradicts itself. None of them is a requirement in this document. They
are written as corrections in the "Corrections to `docs/GAP_SPEC.md`"
section below, to be applied *to that file*, exactly as its own rule
demands.

**Every numbered requirement here is a defect in shipped code, tests, or
tooling** — the same justification `GAP_SPEC.md` gave for its own
existence, and the reason correction paragraphs cannot carry this work:
a correction paragraph cannot hold a reproduction, a fix, and a test.
One of these (`G2R-001`) is a live privilege-drop bypass reachable
through a project-authored configuration file.

---

## Phase 0 — Security: controls that do not hold

### G2R-001 — `environment:` SHALL NOT supply exported shell functions

`src/dev_workspace_mcp/project_config.py:46` denies six exact names plus
an `LD_` prefix:

```python
_ENTRYPOINT_CONTROL_ENV_KEYS = frozenset({"PATH", "BASH_ENV", "ENV", "SHELLOPTS", "IFS", "HOME"})
```

An **exported bash function** is none of them. `docker/default/entrypoint.sh`
is `#!/bin/bash`; bash imports functions from the environment at startup,
and a shell function **takes precedence over `PATH` lookup**, so the
hardcoded `PATH` that GR-005 added at `entrypoint.sh:43-44` does not
defend against it either. **Both controls fail together**, which is
precisely the outcome GR-005 was specified independently to prevent.

Full chain verified, not inferred:

1. `ProjectWorkspaceConfig(version=1, environment={"BASH_FUNC_id%%": "() { echo 1234; }"})`
   is **accepted** — not an exact-set member, does not start with `LD_`,
   is not `SSH_AUTH_SOCK`, does not start with `MCP_`.
2. `src/dev_workspace_mcp/tools/workspace.py:405` and
   `src/dev_workspace_mcp/cli.py:270`: `extra_env = dict(project_config.environment)`.
3. `src/dev_workspace_mcp/workspace/container_config.py:147-150` strips
   only `_DAEMON_CONTROLLED_ENV_KEYS` (`:57-68`) — the key survives.
4. `entrypoint.sh:68` (`if [ "$(id -u)" != "0" ]`) resolves `id` to the
   imported function, takes the "not root" branch, and `exec "$@"` runs
   the workspace command as uid 0 with **no drop performed**.

Reproduced against the shipped `mcp-dev-ubuntu:24.04` image:

```
CONTROL (no BASH_FUNC):                 REAL_EUID=1234
ATTACK  (-e 'BASH_FUNC_id%%=() { echo 1234; }'):  REAL_EUID=0
```

**Severity — stated exactly as GR-004 stated its own.** Under the default
`container.sudo: true` the client already has container root, so this
grants nothing new. Under `container.sudo: false` it is a real
escalation to container root that additionally nullifies
`no-new-privileges`, since no privilege transition occurs for that flag
to constrain. Contained within the container. **Not a host escape.**

**Requirement.** `ProjectWorkspaceConfig.environment` SHALL reject keys
that define exported shell functions. `BASH_FUNC_` is bash's current
prefix; the check SHALL NOT be a single hardcoded prefix comparison
where a general shape is available, since the spelling has changed
before. **Two checks are needed, because the two spellings differ in
which half carries the signature:**

- Modern bash exports `BASH_FUNC_<name>%%`. The `%%` makes the key an
  invalid environment-variable name, so rejecting any key that does not
  match `^[A-Za-z_][A-Za-z0-9_]*$` closes it. Verified:
  `BASH_FUNC_id%%` fails that pattern; `PATH`, `RUST_LOG`, and
  `GIT_CONFIG_SYSTEM` all match it, so ordinary keys are unaffected.
- Pre-Shellshock bash exported a **bare, identifier-shaped name** whose
  *value* began `() {`. The key pattern above cannot see that, since the
  key is a legal identifier. Rejecting a value matching `^\s*\(\s*\)\s*{`
  closes it.

Current bash does not import the bare form, so the second check guards a
downgraded or patched `/bin/bash` rather than the shipped one — it is
cheap, and without it the claim "closes the shape rather than one
spelling" is false.

**This requirement alone closes the reproduced bypass.** Because
`BASH_FUNC_id%%` fails the key pattern, the malicious `environment:`
block is rejected before any container is created, on the image as
shipped. G2R-003 is genuine defence-in-depth against a *future* input
path, not a co-requisite for closing today's vulnerability — see its own
section, which states this from the other side.

Verified by G2AT-001.

### G2R-002 — `environment:` SHALL NOT redirect Git's own configuration

`entrypoint.sh:64` runs, as root before the drop:

```sh
git config --system url."git@github.com:".insteadOf "https://github.com/"
```

`GIT_CONFIG_SYSTEM` is accepted by the validator and redirects that
write to an attacker-chosen path, creating or appending as root.
Reproduced with git 2.43.0: `GIT_CONFIG_SYSTEM=/tmp/fakesys.cfg git
config --system …` created `/tmp/fakesys.cfg` with the expected content.

The written content is fixed, so this is not direct code execution. It
is arbitrary root-owned file creation, and appending gitconfig syntax
into an existing root-owned file can corrupt it.

**Requirement.** `environment` SHALL reject `GIT_CONFIG`,
`GIT_CONFIG_SYSTEM`, `GIT_CONFIG_GLOBAL`, and `GIT_CONFIG_COUNT`. The
broader `GIT_*` family SHALL be assessed and the decision recorded: the
review found no privilege gain from `GIT_SSH`, `GIT_SSH_COMMAND`,
`GIT_EXTERNAL_DIFF`, `GIT_DIR`, or `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
because the root-phase git invocation is only `config --system` (no
repository, no diff, no transport) and post-drop git runs as the
identity the client already controls. That assessment SHALL be recorded
in code rather than re-derived, so a future entrypoint change that adds
a root-phase `git` call against a real repository is evaluated against
it.

Verified by G2AT-002.

### G2R-003 — The entrypoint SHALL neutralize inherited interpreter state it cannot otherwise defend

GR-005 set `PATH` inside the script. That closes `PATH`, and nothing
else: `BASH_ENV` is sourced by bash **before `entrypoint.sh` line 1
executes**, and an exported function is imported at the same moment, so
no assignment *within* the script can defend against either. G2R-001 and
G2R-002 close today's input path at the validator, which is the only
reachable route through the daemon (`extra_env` is populated solely from
`project_config.environment`) — but GR-005 exists precisely so that a
future config surface cannot reopen what a denylist misses, and as
written it does not provide that for anything but `PATH`.

**Priority note.** G2R-001 alone closes the reproduced bypass, because
`BASH_FUNC_id%%` fails its key pattern and is rejected before a
container exists. This requirement is defence-in-depth for a future
input path — a daemon-level `docker.environment`, an image change, or a
denylist that misses a spelling — and SHALL NOT be described as required
to close the live vulnerability.

**Requirement.** The container SHALL NOT execute
`docker/default/entrypoint.sh`'s root phase under a shell that has
already imported unvalidated interpreter state from the environment.

**`env -i` is not a usable mechanism here, and SHALL NOT be adopted
without the caveat below.** The entrypoint reads seven variables from
its environment — `MCP_UID`, `MCP_GID`, `MCP_USER`, `MCP_SUDO`,
`MCP_WORKSPACE_PATH`, `MCP_SSH_MODE`, `MCP_CACHE_PATHS` — plus
`SSH_AUTH_SOCK` for the workspace command. A bare
`exec env -i PATH=… /entrypoint.sh "$@"` clears all of them, and each
then falls back to its default at `entrypoint.sh:46-50`. That does not
merely break caches and SSH: **`MCP_SUDO` defaults to `true`**, so an
operator who deliberately configured `sudo: false` would silently get
passwordless root back. A hardening change that re-enables the privilege
it was written to protect is worse than the gap it closes.

Acceptable mechanisms, in preference order:

1. A `#!/bin/sh` wrapper (or `ENTRYPOINT` shim) that `unset`s
   `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS` and every
   `BASH_FUNC_*`-shaped variable, then `exec`s the bash script with the
   rest of the environment intact. `dash` does not import functions from
   the environment, so the wrapper itself is not compromised before it
   runs.
2. `env -i` **with every required variable explicitly re-passed**. This
   is an allowlist that must be kept in sync with the entrypoint's own
   reads by hand; if chosen, the two SHALL be adjacent in the file with
   a comment binding them, and a test SHALL assert that each variable
   survives.

Whichever is chosen, the shell's behaviour SHALL be asserted by test —
that a function present in the environment is *not* imported — never
inferred from the shebang. Ubuntu's `/bin/sh` is `dash` today; the image
is configurable.

The write-up SHALL state plainly which inherited-state classes the
chosen mechanism does and does not cover, replacing GR-005's
"closes the mechanism regardless of what future surface supplies the
environment", which is true only of `PATH`.

Verified by G2AT-003.

### G2R-004 — Redaction SHALL NOT return a secret it was asked to redact

GR-007 carved `frozenset` and `memoryview` out of scope, reasoning only
about JSON-serializability. That carve-out has a second consequence its
own justification did not consider: `_redact`
(`src/dev_workspace_mcp/errors.py:294-378`) has no branch for either
type, so both reach the terminal `return value` at `errors.py:378`
**regardless of `key_flags_secret`**, and the secret is returned in the
clear. Reproduced:

```
set   : {'secret': ['[REDACTED]']}
frozen: {'secret': frozenset({'hunter2'})}     <-- leaked
list  : {'secret': ['[REDACTED]']}
tuple : {'secret': ('[REDACTED]',)}
```

A leak is strictly worse than the crash GR-007 fixed. Like GR-007 this
is **latent** — no caller constructs a `frozenset` or `memoryview` in
`details`/`metadata` today — and SHALL be described as latent.

**Requirement.** Every container type `_redact` recurses into SHALL be
redacted, or `_redact` SHALL fail closed on a type it does not
recognize. The in-code comment at `errors.py:324-333` SHALL be corrected:
it documents the serialization consequence of the exclusions and not the
redaction-bypass consequence, which is the more serious of the two.

**Recommended: fail closed, with the JSON primitives allowlisted.**
Recursing into `frozenset` and `memoryview` fixes the two types this
review happened to try, and leaves the next unhandled type — `Decimal`,
`Path`, `datetime`, a dataclass — with the same leak. The terminal
`return value` at `errors.py:378` is the actual defect: it is an
open-by-default fallthrough, the same shape as the denylist that
produced G2R-001. Replacing it with an allowlist of the JSON primitives
(`str`, `int`, `float`, `bool`, `None`) plus the existing container
recursion, and returning a type-named placeholder
(`"[UNREPRESENTABLE: frozenset]"`) for everything else, closes the leak,
the `json.dumps` crash, and every future type at once — and is the only
option that makes GR-007's literal "every value … SHALL be
JSON-serializable" true rather than requiring the carve-out it
contradicts.

The cost is that a caller who today passes an exotic-but-harmless value
sees a placeholder instead. No caller does; `details=`/`metadata=` sites
in `src/` all pass primitives or `str(...)`-coerced values, verified by
grep. Record that verification in the write-up, since it is what makes
fail-closed safe here.

Note that `redact_secrets` also raises `AttributeError` on a non-`str`
dict key (`errors.py:295`, `key.lower()`), a contract violation nothing
enforces at either `json.dumps` call site. Closing it is in scope for
this requirement's fail-closed option and out of scope otherwise.

Verified by G2AT-004.

### G2R-005 — A post-open re-check SHALL enforce every rule its pre-open resolve enforced

`FilesystemSecurity.resolve()` enforces three rules: the external-git-dir
check, containment, and `_reject_in_tree_git_metadata`.
`verify_fd_within_root` (`src/dev_workspace_mcp/filesystem/security.py:290-306`)
re-checks **only containment** after the open. `write_file` has the same
shape (`filesystem/service.py:439-445`: `resolve_open_fd` +
`is_within_root`).

Under the default `container.git_dir_mode: "read-write"`
(`config/models.py`), `.git` lives *inside* the repository root. A
container process that wins the documented race between `resolve()` and
the open — the module's own docstring records reproducing this "on the
very first race iteration" — by replacing the target with a symlink to
`.git/config` produces an fd whose real path **is inside root**, so
`verify_fd_within_root` passes and §12.1's "`.git` is never readable via
the filesystem tools" is bypassed. The `write_file` variant redirects a
write into `.git/hooks/pre-commit`, a code-execution primitive.

Under `git_dir_mode: "none"`/`"read-only"` with an external git dir,
containment alone does catch it; the gap is specific to the in-tree
default, which is the shipped default.

**Requirement.** The post-open verification SHALL apply the same rule set
as `resolve()`, not a subset. Both `verify_fd_within_root` and
`write_file`'s inline sequence are in scope.

Verified by G2AT-005.

---

## Phase 1 — Regressions introduced by the `GAP_TODO.md` pass

### G2R-006 — A read that never reports a path SHALL NOT fail on a lossy decode

G4's write-up records discovering that an unconditional `stdout_lossy`
guard wrongly blocked callers that inspect only `branch`/`head_sha`, and
fixing it by threading `strict_utf8` through `read_git_status`. The fix
was applied to `git_commit`'s two precondition calls and to no others:

| call site | `strict_utf8` | fields actually consumed |
| --- | --- | --- |
| `tools/git_commit.py:141` | `False`, explicit | `conflicted`/`branch`/`head_sha` |
| `tools/git_commit.py:394` | `False`, explicit | `head_sha` |
| `tools/git_pull.py:102` | **inherits `True`** | `branch`, `head_sha` only |
| `tools/git_pull.py:249` | **inherits `True`** | `head_sha` only |
| `tools/git_push.py:147` | **inherits `True`** | `branch`, `head_sha` only |

`git status --porcelain` lists the whole repository, so **`git_pull` and
`git_push` now fail outright with `COMMAND_FAILED … not valid UTF-8` on
any repository containing a single unrelated non-UTF-8 filename
anywhere** — the exact failure mode G4 identified, corrected for one
tool and left in place for two others. No test covers it, which is why
the suite stayed green.

**Requirement.** Every `read_git_status` caller that consumes no path
SHALL pass `strict_utf8=False` explicitly, with the same in-code
justification `git_commit`'s two sites carry.

Verified by G2AT-006.

### G2R-007 — A repository-local hook path SHALL NOT disable hooks configured outside it

`core.hooksPath` is a single directory, not a search chain. G8 set it
repo-locally, which shadows the user's global hook directory outright:

```
file:~/.gitconfig    core.hookspath=/home/phil/.config/git/hooks
file:.git/config     core.hookspath=.githooks
```

`~/.config/git/hooks/` contains `commit-msg`, `post-checkout`,
`post-commit`, `post-merge`, and `pre-push`; `.githooks/` contains
`pre-commit` alone. **Five hooks are inert in this clone**, including the
`commit-msg` hook that refuses a `Co-Authored-By:` trailer — the hook
`docs/GAP_TODO.md`'s own task-loop step 7 depends on. Verified by running
it directly (exits 1 with its refusal message) and confirming git cannot
reach it (`.githooks/commit-msg` does not exist).

G8 satisfied GR-002 and disabled an unrelated, load-bearing control as an
undocumented side effect.

**Requirement.** Activating the repository's hooks SHALL NOT disable
hooks the developer configured elsewhere. Either `.githooks/` SHALL
carry the shadowed hooks, or `.githooks/pre-commit` SHALL dispatch to
the previously-configured `core.hooksPath`, or `docs/DEVELOPMENT.md`
SHALL document the shadowing and the required manual step. The first two
are preferred; documentation alone repeats GR-002's own finding that
advisory documentation is what already failed.

Verified by G2AT-007.

### G2R-008 — The pre-commit gate SHALL verify what is being committed

`.githooks/pre-commit` runs `ruff format --check .`, `ruff check .`, and
`mypy src` against the **working tree**. A commit records the **index**.
Two divergences follow:

- *False negative.* Stage a file that fails a check, then fix it in the
  worktree without staging. The hook passes; the broken version is
  committed and reaches CI red. This is the G1 failure mode by another
  route — the hook exists to prevent exactly it.
- *False positive.* An unrelated dirty or untracked `.py` file blocks
  every commit until cleaned, including commits that do not touch it.

**Requirement.** The hook SHALL check the content that would be
committed, and SHALL NOT be able to corrupt the developer's working tree
while doing it.

**`git stash --keep-index` is not safe here — verified, not assumed.**
It looks like the obvious mechanism, but `git stash pop` performs a
three-way merge to restore what it stashed, and that merge can conflict
with the *fix* deliberately left unstaged. Reproduced: stage a broken
version, leave a fix unstaged that touches the same line, stash with
`--keep-index`, then pop — `git stash pop` reports `CONFLICT (content):
Merge conflict`, and the working file is left containing literal
`<<<<<<< / ======= / >>>>>>>` markers instead of either version. That is
precisely the scenario this requirement exists to make routine (stage a
failing version, fix it unstaged) — a mechanism that corrupts the file
on exactly its own intended use is worse than the defect it closes.

**Checking out the index into a temporary worktree does not have this
failure mode**, because it never touches the real working tree at all:
`git write-tree` snapshots the index as a tree object (index-only, no
working-tree interaction, cannot conflict with anything), `git
commit-tree` wraps it as a commit, and `git worktree add --detach` checks
that commit out into a scratch directory the checks run against.
Cleanup is `git worktree remove`, not a merge. Verified fast enough not
to matter: worktree creation and a fresh `uv run` (creating its own
`.venv` via `uv`'s package cache) together took under 150ms against this
repository.

**A second pitfall, found implementing this, not merely theorized:**
`git` sets `GIT_INDEX_FILE=.git/index` (a relative path) in every
hook's environment, and a hook script's own `git` subprocesses inherit
it. `git worktree add` reached from *inside a real hook invocation* —
not from an interactive shell, where this environment variable is
unset — fails outright with `fatal: .git/index: index file open
failed: Not a directory`, on every single commit, not only the conflict
scenario above: a worktree's `.git` is a *file* (a pointer to the real
git dir), not a directory, so resolving the inherited relative path
against it breaks. `unset GIT_INDEX_FILE` before any `git` command runs
is the fix, confirmed sufficient by isolating it as the only change
between a failing and a succeeding run.

Verified by G2AT-008.

---

## Phase 2 — Correctness defects in shipped code

### G2R-009 — Filesystem listing SHALL survive concurrent mutation

`filesystem/service.py:175`, inside `_describe`:

```python
stat = path.stat()
```

Unguarded. `_is_git_metadata` immediately above it (`:150-156`) *was*
explicitly hardened against this class (`PathOutsideRepositoryError`,
`RuntimeError` for symlink loops); `_describe` was not. `FileNotFoundError`
is not a `WorkspaceError`, and `translate_workspace_errors`
(`errors.py:432`) catches only `WorkspaceError`, so it escapes as an
unstructured crash.

Trigger: `list_files("", recursive=True)` while a container process
deletes an entry between `start.rglob("*")` enumeration and `_describe`
— reachable with `exec_start "rm -rf build/"` against the same
bind-mounted checkout. The `path.is_symlink()`/`path.exists()`/
`path.resolve()` calls at `:160-170` share the exposure.

**Requirement.** An entry that disappears between enumeration and
description SHALL be skipped or reported, not propagated as an untyped
exception.

Note: the local `stat` at `:175` shadows the `stat` module, which is used
in sibling methods (`:215`, `:548`). Renaming it is in scope as an
adjacent, zero-risk correction.

Verified by G2AT-009.

### G2R-010 — `write_file` SHALL NOT destroy existing content on a failed write, and SHALL report what reached disk

`filesystem/service.py:469-470`:

```python
os.ftruncate(fd, 0)
os.pwrite(fd, data, 0)
```

Two defects:

- **Data loss.** Target exists, disk full. `ftruncate` succeeds,
  `pwrite` raises `ENOSPC`. The cleanup at `:471` unlinks only when
  `not existed_before`, so the client's pre-existing file is left
  **empty**, and the `OSError` escapes untyped — the client cannot
  determine the resulting state.
- **Unverified result.** `os.pwrite` may transfer fewer bytes than
  requested; the return value is discarded. `WriteResult` (`:506`)
  reports `sha256_of(data)` and `len(data)` — computed from the intended
  buffer, never from disk. A short write returns a hash no subsequent
  `read_file` reproduces, poisoning the `expected_sha256` optimistic-
  concurrency protocol §22.2 depends on.

**Requirement.** A failed write SHALL NOT leave the previous content
destroyed, and the reported `sha256`/`size` SHALL describe what is on
disk. Writing to a temporary file in the same directory and `os.replace`
-ing it over the target satisfies both, and preserves the `dir_fd`
containment the current code correctly uses. A short write SHALL be
looped or SHALL raise.

Verified by G2AT-010.

### G2R-011 — Opportunistic background work SHALL NOT fail the request that triggered it

`workspace/manager_lifecycle.py`'s `sweep_idle_workspaces` calls
`self.stop(record.id)` with no exception handling; `stop()` raises
`ContainerRuntimeFailedError` on any `DockerException`. It is invoked
from `tools/workspace.py:501` — *after* `ensure_workspace` succeeded,
*before* the `workspace_opened` audit event (`:507`) and the return.

Trigger: a client opens workspace A successfully; an unrelated idle
workspace B's `container.stop()` errors. `workspace_open("A")` returns
`CONTAINER_RUNTIME_FAILED` though A is running and healthy, A's audit
record is never written, and the remaining idle workspaces are never
swept.

This contradicts the reasoning `delete()`'s own RR-012 comment
(`manager_lifecycle.py:530-547`) applies at length to post-completion
cleanup: it "SHALL never be a reason to report this call itself as
failed."

**Requirement.** `sweep_idle_workspaces` SHALL be best-effort per
workspace: a failure to stop one SHALL be logged and SHALL NOT abort the
sweep or the triggering request. The same audit SHALL be applied to
`prune_job_logs`, which is scheduled the same opportunistic way from
`exec_start`.

Verified by G2AT-011.

### G2R-012 — Setup steps SHALL run whenever a container is newly provisioned

`tools/workspace.py:451-456` computes `was_created` from **database row
existence** before `ensure_workspace`; `:484` runs setup only
`if was_created`. The row and the container have independent lifetimes,
so the two diverge:

- *Failure path.* First `workspace_open` for a repo with
  `setup: ["…"]`. The row is inserted, the container created, a step
  fails transiently. Retry: the row now exists, `was_created` is `False`,
  **setup is skipped permanently**. The workspace reports healthy and is
  missing its toolchain.
- *Recreate path.* The row exists but the container was removed out of
  band (`docker system prune`). `ensure_workspace`'s HR-017 branch
  (`manager_lifecycle.py:114-116`) builds a genuinely new, empty
  container — and setup does not run. `run_setup_steps`' own docstring
  records fixing this exact bug for `cli.py`'s `reset`/`rebuild`; the
  prune-recovery path was missed.

**Requirement.** Setup SHALL be driven by whether a container was newly
created, not by whether a database row was newly inserted, and SHALL be
re-attempted after a failed run. Recording setup completion on the
workspace row satisfies both halves.

Verified by G2AT-012.

### G2R-013 — `workspace_open` SHALL translate Docker image errors

`tools/workspace.py:476`:

```python
image_digest = docker_client.images.get(effective_image).id
```

No `try`. `docker.errors.ImageNotFound` is not a `WorkspaceError` and
escapes as an unstructured crash, unlike every other Docker call in the
tool layer, which HR-022 deliberately wrapped.

Trigger: a persistent workspace is already RUNNING; the operator edits
`.mcp-workspace.yml` to an image not present locally. `reconcile()`
returns RUNNING without touching Docker images, so nothing pulls it, and
`images.get` raises.

**Requirement.** The lookup SHALL raise a typed, translated error naming
the missing image.

Verified by G2AT-013.

### G2R-014 — A workspace's reported image and digest SHALL describe the same image

`tools/workspace.py:476-482` writes the digest of `effective_image` into
the workspace row **unconditionally**, but `ensure_workspace` never
rebuilds a running container when the configured image changes. With the
new image present locally, the container still runs the old one, while
`WorkspaceInfoOutput.image` reports `record.image` (old) and
`image_digest` reports the new image's digest. §9.2 calls this pair the
reproducibility record; it describes two different images.

**Requirement.** The recorded digest SHALL be the digest of the image the
running container was created from. If the configured image has changed
without a rebuild, that SHALL be reported (a `rebuild_required` signal or
equivalent) rather than silently reconciled into a contradictory record.

Verified by G2AT-014.

### G2R-015 — Deleting a workspace SHALL reclaim its job logs

`manager_lifecycle.py:551-552` deletes the workspace's `job` rows.
Nothing deletes `<state_dir>/jobs/<job_id>/{stdout,stderr}.log`.
`prune_job_logs` (`jobs/retention.py:41-43`) enumerates candidates from
`job_store` — rows that no longer exist — so the files are unreachable by
retention forever.

Trigger: `exec_start` a job producing 500 MB of output, let it finish,
delete the workspace. The 500 MB persists indefinitely and is invisible
to `job_log_max_total_bytes` accounting, so the size cap under-counts and
prunes still-tracked jobs instead.

**Requirement.** Deleting a workspace SHALL remove its jobs' log
directories, or retention SHALL enumerate the log directory rather than
the job table. Either satisfies this; the choice SHALL be recorded.

Verified by G2AT-015.

### G2R-016 — The setup-step timeout path SHALL handle kill failures as `run_exec` does

`tools/workspace.py:199-206` kills a timed-out setup step with no
exception handling. `ExecutionService.kill` (`execution/service.py:330-370`)
*deliberately* propagates `PermissionError` (HR-020) and
`DockerException` from `exec_inspect` (RR-013) — its docstring is
explicit. `tools/execution.py:419-428` handles precisely this for
`run_exec` (`except OSError` → `ContainerRuntimeFailedError`); the setup
copy does not.

Trigger: a setup step exceeds its timeout and the container was removed
out of band → `exec_inspect` raises → escapes untyped.

**Requirement.** The two timeout-and-kill blocks SHALL handle kill
failure identically. Extracting the shared sequence — they also carry
separate `KILL_GRACE_PERIOD_SECONDS` / `_SETUP_KILL_GRACE_PERIOD_SECONDS`
constants that `workspace.py:81-84` admits must be kept in sync by hand,
and which have already diverged — satisfies this more durably than
copying the handler.

Verified by G2AT-016.

### G2R-017 — `exec_output` SHALL reject an inverted byte range

`tools/execution.py:627-634`. With `byte_start=100, byte_end=50`:
`requested = -50` → `capped = max(min(-50, limit), 0) = 0` → one byte is
read → `truncated = 1 > 0` is `True` and `text` is empty. The client
receives empty content plus `truncated: true`, which invites an infinite
paging loop. The adjacent validation loop at `:568-576` checks for
negatives but never `byte_end >= byte_start`.

**Requirement.** An inverted range SHALL be rejected as an invalid
argument.

### G2R-018 — `list_files` SHALL bound its work by the requested limit

`filesystem/service.py:114`:

```python
candidates = sorted(start.rglob("*") if recursive else start.iterdir())
```

The whole tree is walked and sorted before the `if len(entries) >= limit:
break` at `:116` can apply, making the break decorative for cost
purposes. `list_files("", recursive=True, max_entries=1)` on a repository
containing `node_modules/` performs a full recursive walk and sort to
return one entry. Client-controllable; no configuration bounds it.

**Requirement.** Enumeration SHALL stop once the limit is reached.
Sorted output SHALL be preserved if it is a contract — a bounded
heap/merge over the iterator satisfies both.

---

## Phase 3 — Test, tooling, and reference integrity

### G2R-019 — Every acceptance-test series SHALL be audited

`tests/test_acceptance_test_coverage.py:71-99` registers `AT`, `HAT`,
`RAT`, and `VAT`. `docs/AUDIT_SPEC.md`'s `AAT-001..AAT-004` and
`docs/GAP_SPEC.md`'s `GAT-001..GAT-008` are not registered, though
`GAP_SPEC.md:586` <!-- X20 (`docs/GAP2_TODO.md`): was `:545`, shifted by this same task's own edits to that file --> uses the identical "Each SHALL be an automated test"
language the audit's own docstring cites as its justification for
covering the other three.

Measured: `GAT` has **zero** citations anywhere under `tests/` — the GAP
tests are named after the requirement (`test_at_gr004_*`), not the
acceptance test. `AAT-001`/`002`/`003` are cited; `AAT-004` is not. So
twelve acceptance tests currently drift with nothing to catch a deleted
or renumbered one.

**Requirement.** `AAT` and `GAT` SHALL be registered in `_SERIES`.
Requirement-id naming has precedent in this codebase and is not itself a
defect, so the uncited numbers SHALL be recorded in `known_uncited` with
the reason, exactly as `HAT`/`RAT`'s pre-existing gaps already are —
not back-filled with citations, and not left unregistered.

No `G2AT` — this requirement is verified by the audit's own new rows
plus the existing meta-test that proves the audit can fail
(`test_meta_removing_every_citation_for_an_at_makes_the_audit_fail_for_it`),
which is the same way the `AT`/`VAT` series' own coverage is verified.

### G2R-020 — Import classification SHALL be unambiguous

The repository has a top-level `docker/` directory, and `pyproject.toml`
sets neither `known-first-party` nor `src` for ruff. Ruff's source
detection therefore classifies bare `import docker` as **first-party**
(the local directory) while `from docker.errors import …` remains
third-party — the same distribution split across two import sections.
That ambiguity is what let two ruff versions disagree and produce G1;
`manager_core.py:20-25` still carries the resulting shape, which the
pinned version demands and a differently-configured tool would not.

G1 pinned the tool. It did not remove the ambiguity, so any tool with
different source detection reopens it.

**Requirement.** `pyproject.toml` SHALL declare `docker` as third-party
(`[tool.ruff.lint.isort] known-third-party = ["docker"]`) or SHALL set
`src` so the local `docker/` directory is not treated as a package root.
The resulting import order SHALL be regenerated with the pinned tool.

### G2R-021 — G4's call-site audit SHALL be completed and corrected

`docs/GAP_TODO.md` G4 tabulates nine `run_git_in_container` call sites
and states "Every one … was audited and set explicitly". There are
**eleven** (verified: the three unlisted ones already existed at
`ea4d4ad^`, and `ea4d4ad` never opened their files), and four of the
eight tabulated sites are left at the implicit default rather than set
explicitly. The three unaudited sites:

| site | consequence of the strict default |
| --- | --- |
| `git_state.py:76` | `workspace_info`'s `git.clean` silently becomes `null` for any repository containing a non-UTF-8 path (absorbed at `:86-87`) |
| `tools/git_pull.py:264` | none — `merge-base --is-ancestor` stdout is always empty |
| `tools/git_push.py:168` | none — stdout is a SHA |

**Requirement.** All eleven sites SHALL carry an explicit `strict_utf8`
value with its justification, and the correction SHALL be recorded in
`docs/GAP_TODO.md`'s G4 write-up per the convention that document family
uses for superseded claims. `git_state.py:76`'s silent degradation SHALL
be decided deliberately — either accepted with a comment, or reported.

### G2R-022 — Concurrent suite runs SHALL NOT silently collide

Workspace container names are deterministic (`mcp-dev-ws-<repo>-<hash>`).
Two concurrent runs of `tests/workspace/`, or one run alongside a live
daemon serving a repository of the same id, collide: the second run's
fixture finds a container already at its name and takes
`manager_lifecycle.py:246-260`'s adoption early-return, so lifecycle
assertions fail. `tests/workspace/conftest.py`'s RR-024/HR-026 tracking
prevents destroying *other* processes' resources but does nothing about
this.

Observed twice independently during this review: a full-suite run
alongside concurrent Docker activity produced 11 failures and 2 errors,
all in `tests/workspace/`, all `workspace_open`-related; the identical
suite run cleanly immediately afterwards produced **1019 passed**. The
failure mode is a silent false *failure*, which is expensive to diagnose
and reads as flakiness.

**Requirement.** The collision SHALL be prevented or documented.
Namespacing test workspace names with a per-run token prevents it;
documenting it in `docs/DEVELOPMENT.md`'s troubleshooting section is the
minimum. This requirement SHALL NOT be satisfied by adding a retry.

---

## Corrections to `docs/GAP_SPEC.md`

Per `docs/GAP_SPEC.md`'s own rule, these are corrections to that
document, not requirements here. They SHALL be applied as correction
paragraphs in `docs/GAP_SPEC.md` itself.

**GAT-007 is contradicted by GR-007, in the same document.** GAT-007
requires that `_redact` on a secret-flagged `set` "returns a **`set`**,
asserted by `isinstance`". GR-007 (`GAP_SPEC.md:341-343`) explicitly
endorses "coercing `set` to `list`", which G5 implemented — making a
returned `set` the defect, not the requirement. The test correctly
asserts `list`. GAT-007's text was written against pre-G5 behaviour and
was never updated when G5 landed.

**GAT-006 is unsatisfiable as written.** It requires "a cache volume
**mounted at `.ssh`** whose mount point is itself a symlink". A Docker
volume mount always creates a real directory at its own mount point, so
the mount point can never be a symlink. G6's implemented test mounts at
`$HOME` with `cache_paths=["."]` and genuinely exercises the directory
refusal; `docs/GAP_TODO.md:649-661` explains the substitution but
`GAP_SPEC.md` was never corrected.

**GR-007 contradicts itself.** Its requirement sentence is "Every value
`_redact` can return SHALL be JSON-serializable"; two paragraphs later it
declares `frozenset`, `memoryview`, and namedtuple degradation
"deliberate, documented exclusions … out of scope". Both cannot hold.
Measured, the residue is not three named types but *every* type outside
the handled set — `Decimal`, `complex`, `Path`, `datetime`, and any
custom object all reach `errors.py:378` untouched and crash
`json.dumps`. The correction SHALL state which of the two sentences
governs; G2R-004 assumes the carve-out governs for serializability and
closes the redaction half regardless.

**Stale citations.** `GAP_SPEC.md:321` cites `errors.py:389` for
`to_tool_error`'s `json.dumps`, now `errors.py:412`. GR-008's table cites
`tools/git_commit.py:255` for `capture_index`, now `:260`. GR-009 cites
`entrypoint.sh:209`/`:214` for the two `.ssh` refusals, now `:225`/`:230`
(moved by G3's own PATH block) — `docs/GAP_TODO.md:619,623,665` and
`tests/workspace/test_default_image.py:355` carry the same stale pair.
One row in `docs/REMEDIATION_TODO.md` that G7 did not touch keeps a wrong
line number: `test_rr016_…` is at `tests/git/test_diff.py:382`, not
`:364`.

---

## Recorded, not required

- **`rebuild` and `reset` are byte-identical.** Both call
  `self._recreate(..., state=WorkspaceState.REBUILDING)`. Their
  docstrings promise different semantics and §8.4 distinguishes them;
  the code does not. This is either an unimplemented feature or two
  names for one operation, and nothing marks which. Resolving it is a
  design decision, not a defect fix, and no task in
  `docs/GAP2_TODO.md` closes it.
- **`test_at_hat019` and GR-006 are in tension.**
  `tests/git/test_status.py:216` asserts "a repository containing a
  non-UTF-8 filename SHALL be statusable"; GR-006 now makes the
  `git_status` *tool* fail on exactly that. There is no runtime conflict
  (that test is parser-level, host-side), but the two requirements are
  not reconciled anywhere. Reconciling them is a specification decision.
- **`_redact`'s comment-to-code ratio.** ~40 lines of logic under ~90
  lines of rationale covering types no caller constructs. The history is
  valuable; its placement at the point of maximum reading cost is a
  judgement call, not a defect.

---

## Acceptance tests

Each SHALL be an automated test. G2R-019 through G2R-022 carry no
`G2AT`: they are tooling and reference corrections verified by
inspection and by the existing coverage audit agreeing with them.

- **G2AT-001** — Two halves, both required, both belonging to G2R-001
  and neither depending on G2R-003:
  1. A `ProjectWorkspaceConfig` whose `environment:` sets
     `BASH_FUNC_id%%` — and one whose value begins `() {` under an
     identifier-shaped key — is rejected at validation, naming the
     offending key. Host-side unit test.
  2. A `workspace_open` against a repository whose `.mcp-workspace.yml`
     carries such a key fails, and **no container is created**. This is
     the end-to-end half: it proves the rejection actually prevents
     provisioning, and it SHALL go *through* project config rather than
     around it (contrast G2AT-003, which deliberately goes around it to
     reach the entrypoint).
- **G2AT-002** — The same, for `GIT_CONFIG_SYSTEM`.
- **G2AT-003** — A container started with an exported `BASH_FUNC_id`
  function in its environment still drops privileges: the workspace
  command reports a non-zero uid. Real-container test, sibling to
  `test_at_gr005_*`. SHALL be built at the `run_kwargs`/`extra_env`
  layer, not through project config, so it exercises the entrypoint
  rather than G2R-001's validator; and SHALL assert via a shell builtin
  (`echo $EUID`), never `id -u`, which the attack itself shadows.
- **G2AT-004** — `_redact` on a secret-flagged `frozenset` and
  `memoryview` does not return the secret in the clear.
- **G2AT-005** — A `read_file`/`write_file` whose target is replaced
  with a symlink into `.git` after `resolve()` and before the open is
  refused, under the default in-tree `git_dir_mode: read-write`. May
  drive the race deterministically via the same injection point the
  existing race tests use rather than by looping.
- **G2AT-006** — `git_pull` and `git_push` succeed against a repository
  containing an unrelated non-UTF-8 filename. Real-container test; SHALL
  create the path with `os.fsdecode` of raw bytes.
- **G2AT-007** — Activating the repository hook path leaves a
  `Co-Authored-By:` trailer refused. May be a test of the dispatcher, or
  an assertion that `.githooks/` carries a `commit-msg`.
- **G2AT-008** — A file staged in a failing state is blocked by the hook
  even when the working tree has been fixed unstaged.
- **G2AT-009** — `list_files` completes when an enumerated entry is
  deleted between enumeration and description, rather than raising an
  untyped `FileNotFoundError`.
- **G2AT-010** — A `write_file` whose write fails partway leaves the
  previous content intact, and a successful `write_file`'s reported
  `sha256` matches a subsequent `read_file`.
- **G2AT-011** — A `workspace_open` succeeds and is audited when an
  unrelated idle workspace fails to stop during the sweep.
- **G2AT-012** — Setup steps run again on the next `workspace_open`
  after a failed run, and run when a container is recreated behind an
  existing database row.
- **G2AT-013** — `workspace_open` against a configured image absent
  locally returns a typed error naming the image, not an unstructured
  crash.
- **G2AT-014** — `workspace_info`'s `image` and `image_digest` never
  describe different images.
- **G2AT-015** — Deleting a workspace reclaims its jobs' log
  directories.
- **G2AT-016** — A setup step that times out against a removed
  container returns a typed error rather than an unstructured crash.
- **G2AT-017** — `exec_output` called with `byte_end < byte_start`
  returns an invalid-argument error, rather than empty content with
  `truncated: true`.
- **G2AT-018** — `list_files` with a small `max_entries` over a large
  tree does not enumerate the whole tree. Asserted by counting work (a
  patched or wrapped iterator), never by wall-clock.

---

## Definition of done

`environment:` cannot supply an exported shell function or redirect
Git's configuration, and the entrypoint does not execute its root phase
under a shell that has already imported unvalidated interpreter state;
redaction never returns a value it was asked to redact; a post-open
re-check enforces every rule its pre-open resolve enforced; `git_pull`
and `git_push` work on repositories containing non-UTF-8 paths;
activating this repository's hooks does not disable hooks configured
elsewhere, and the hook verifies the index rather than the working tree;
a listing survives concurrent mutation; a failed write neither destroys
existing content nor reports a hash that was never on disk;
opportunistic background work cannot fail the request that triggered it;
setup steps run whenever a container is newly provisioned; every
acceptance-test series is audited; import classification is unambiguous;
G4's call-site audit is complete and its overstatement corrected;
`docs/GAP_SPEC.md` carries the four corrections above; G2AT-001..G2AT-018
all pass; the full suite passes with no Docker-dependent test skipped and
no foreign Docker state left behind — **run with no concurrent Docker
activity, per G2R-022**; and no claim in `docs/GAP2_TODO.md` was written
without being re-run against the code first (RR-033).

**Carried forward, and still binding:** if working this list produces a
finding about **this** document, it goes in a correction paragraph here —
not an eighth layer.
