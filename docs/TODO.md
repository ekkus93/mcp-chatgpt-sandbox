# Development Workspace MCP Server — Implementation TODO

**Source of truth:** `docs/SPEC.md` (§ references below point into it)
**Status:** not started
**Convention:** tasks are `S<n>`. Each names the spec sections it implements, the
functional requirements it satisfies, and the acceptance tests that prove it.

A task is done when its subtasks are checked, its acceptance tests pass, and
`schemas/tools.json` has been re-exported if the task changed a tool contract
(§33.1).

---

## Open Decisions

These block the tasks that cite them. Decide before starting the task, not during.

- [x] **D1 — Implementation language.** **Decided: Python** (§54), using the
      official `mcp` SDK, `docker` SDK, `pydantic`, `uv` for dependency
      management. Blocked S1; unblocked 2026-08-26.
- [x] **D2 — Read-only GitHub API token.** §19.5. **Decided 2026-08-26: no
      token for now.** S28 is built and fully tested against the
      no-credential path — `remote_protection` reports `unverified` and
      `require_remote_protection` degrades `deny` to `warn` (§18.5), exactly
      as §19.5/§18.5 specify for an unconfigured credential. A real
      read-only PAT can be added later via `github.api_token_path` with no
      code change; unblocked S28 without needing one now.
- [x] **D3 — Deploy-key provisioning is manual or automated.** §19.1 requires a
      per-repository deploy key with write access. **Decided 2026-08-26:
      manual**, per the MVP allowance this note already named: the operator
      creates the deploy-key pair and registers the public half on GitHub by
      hand; S23 builds only the per-workspace `ssh-agent` that loads and
      serves an already-provisioned private key file. Automated provisioning
      (needing an `administration: write` credential well beyond D2's
      deliberately read-only token) remains out of scope. Unblocked S23.
      *(Corrected 2026-08-26: this note previously said "Blocks S21"/"Blocks
      S26" — stale against a since-reordered task numbering; the per-task
      `**Blocked by:**` annotations on S23/S28 were already correct.)*

---

## Phase 0 — Scaffolding

### S1 — Project skeleton and CI ✅
**Spec:** §54, §71, §72 · **Commit:** a33b6dd

- [x] Scaffold the project in Python (`pyproject.toml`, `uv`)
- [x] MCP server entrypoint that starts a transport and serves an empty tool list
- [x] `serve --config <path>` CLI shape per §73
- [x] CI: format, lint, static analysis, unit tests
- [x] `docs/` placeholders per §72 (`SECURITY.md`, `CONFIGURATION.md`, `TOOLS.md`,
      `DEVELOPMENT.md`, `THREAT_MODEL.md`)

### S2 — Configuration load and validation ✅
**Spec:** §52, §53, §73 · **Satisfies:** FR-002 (partly) · **Commit:** 4025604

- [x] Config model covering every §52 block: `repository_roots`, `repositories`,
      `docker`, `workspace`, `container`, `ssh`, `github`, `execution`,
      `git_policy`, `security`, `audit`
- [x] Validate at startup; refuse to serve on invalid config
- [x] Reject a `repositories[].ssh_key` that resolves under the host user's `.ssh`
      (§19.1) — this is an invariant, not a policy
- [x] Secrets loaded from files outside repository roots only (§53)
- [x] Startup sequence per §73, in order, with a degraded health state when Docker
      is unavailable
      (steps 1-2 and 8 implemented; steps 3-4 land with S3/S14, step 5 stays
      lazy per S6, steps 6-7 land with S23/already folded into step 1)

### S3 — State database ✅
**Spec:** §67, §68, §69 · **Commit:** 720e4f2

- [x] SQLite schema for `Repository`, `Workspace`, `Job`, `AuditEvent`
      (`WorkspaceLock` deferred to S31)
- [x] Migrations (`PRAGMA user_version`-tracked, idempotent)
- [x] Container identity never depends solely on SQLite — Docker labels are
      authoritative for reconstruction (§69). The reconciler itself is S6/S18;
      this task only avoids treating `workspace.container_id` as ground truth.

### S4 — Error model ✅
**Spec:** §31 · **Commit:** df90018

- [x] Every code in §31's list as a typed error (24 `WorkspaceError` subclasses)
- [x] Responses carry stable code, concise message, recoverable flag, structured
      details; never secrets (`redact_secrets`, by key name and value shape)
- [x] Map internal errors to MCP errors at the interface layer (§6.1) —
      `to_tool_error` / `@translate_workspace_errors`, wrapping the SDK's
      `ToolError`; anything not a `WorkspaceError` still crashes as the SDK
      intends, rather than being laundered into a clean result

---

## Phase 1 — Core Workspace

Acceptance for the phase: a client can modify a real local repository and run a
command in its container, with AT-033 and AT-015 passing.

### S5 — Repository registry and authorization ✅
**Spec:** §7, §7.1, §7.2 · **Satisfies:** FR-001, FR-002 · **Verified by:** AT-022 · **Commit:** c4561c3

- [x] Resolve repository IDs to canonical host paths
- [x] Optional discovery beneath `repository_roots`, resolving symlinks and
      confirming the result stays under an authorized root (one level deep;
      discovered repositories get `github: None` — unknowable without Git state)
- [x] `repository_list` tool (first real tool; `create_server` now takes `ServerConfig`)
- [x] Fail closed for unauthorized repositories (`RepositoryNotAllowedError`)
- [x] AT-022: a symlink under an allowed root pointing elsewhere is not authorized

### S6 — Docker lifecycle ✅
**Spec:** §8.1, §8.2, §50, §51, §55, §69 · **Satisfies:** FR-003, FR-004, FR-005 · **Commit:** 3c25003

- [x] Docker control via SDK or API (CLI acceptable for MVP, §55) — `docker` SDK
- [x] Lifecycle states and explicit transitions per §8.2
- [x] Deterministic, collision-safe container and volume names (§51)
- [x] Labels per §50; the daemon manages only its own containers
- [x] Reconcile unexpected Docker state on the next workspace operation (§69: missing
      container → `absent`; unrecorded labeled container → adopted; more than one → fail
      closed, `ContainerRuntimeFailedError(recoverable=False)`)
- [x] Internal `restart` / `rebuild` / `reset` / `delete` semantics (§8.4) —
      operator-facing, **not** MCP tools (§34.3)
- [x] Persistent mode (FR-004): container and installed tooling reused across
      sessions until rebuilt or deleted
- [x] Ephemeral mode (FR-005): `mode="ephemeral"` creates a workspace with a
      unique id every call — for clean-room validation and reproducing CI
      (§8.1); resource-limit tightening deferred to §26/S26+

Also fixed: `workspace.repository_id` is a foreign key into `repository`
(§67), but nothing else in the system persists a `repository` row —
`RepositoryRegistry` (S5) is in-memory only. The manager now upserts the
repository row when creating a workspace, since it is the first thing that
needs it to exist.

### S7 — Container identity and default image ✅
**Spec:** §5.3, §9.1, §41 · **Satisfies:** FR-036 · **Verified by:** AT-014, AT-015, AT-016 · **Commit:** a51adfa

- [x] Default image: §9.1 contents, including `sudo` (passwordless) and
      `openssh-client`; no baked-in uid (`docker/default/Dockerfile`)
- [x] Create the container user at start with the checkout owner's uid/gid and a
      persistent home (`docker/default/entrypoint.sh`, via `setpriv` — not `gosu`,
      already in Ubuntu's `util-linux`, no extra download)
- [x] `container.user: root` refused unless userns remapping is active — fail with
      `CONTAINER_CREATE_FAILED` (AT-016), before any container is created
- [x] Hardening flags per §41 — **do not** set `no-new-privileges` while
      `container.sudo: true`, it breaks `sudo`
- [x] AT-014: `sudo apt-get install` works while default identity is the checkout owner
      (real install, real network, real non-root identity)
- [x] AT-015: files written from the container are owned by the checkout owner on
      the host and are writable by the human — verified with a real bind mount

Tests use one-shot containers (`containers.run(..., remove=True)`) for the
identity proofs, not `exec_run` on a long-lived one — `docker exec` uses the
image's configured user by default (root), not whatever PID 1 dropped to via
`setpriv`; a one-shot container's command *is* PID 1. Which user a later
`exec` call should run as is S12's problem.

### S8 — Mounts ✅
**Spec:** §5.1, §5.4, §18.2 · **Satisfies:** FR-006, FR-026 · **Verified by:** AT-012, AT-023, AT-024 · **Commit:** 3411542

- [x] Working tree bind-mounted read/write at `/workspace`
- [x] `.git` mounted per `git_dir_mode`: `read-write` (default, no second mount
      needed for an ordinary in-tree repo), `read-only` (ro bind over the parent
      mount), `none` (masked with an empty read-only tmpfs — a subpath of a bind
      mount cannot be "not mounted")
- [x] Gitfile resolution (§18.2): detect `.git` as a file, resolve `gitdir:` and
      `commondir`, verify the target is under an authorized root, mount the main
      `.git` **at its host absolute path** so both references resolve unchanged
      (needed even under `read-write` — a gitfile's target lives outside the
      working-tree mount regardless of mode)
- [x] Mount nothing beyond §5.4's list (at most two mounts, by construction)
- [x] Container-local `/etc/gitconfig` with `safe.directory <workspace_path>`;
      `GIT_TERMINAL_PROMPT=0` — added to `entrypoint.sh` (S7), since it already
      runs once as root at container start
- [x] AT-012: no `/var/run/docker.sock` in the container
- [x] AT-023 / AT-024: under `read-only`, writing `.git/hooks/pre-commit` and
      setting `core.fsmonitor` both fail — verified against real Git repos
      (`git init`) and real mounts; the same writes verified to *succeed* under
      the default `read-write`, since that is documented behaviour (§18.3), not
      a gap

### S9 — Filesystem service (host-side) ✅
**Spec:** §6.3, §12, §12.1, §48, §49 · **Satisfies:** FR-011, FR-031 · **Verified by:** AT-001, AT-002, AT-033 · **Commit:** 35be9f7

- [x] Runs **host-side in the daemon** (§6.3) — pure filesystem logic, no container
      required (`FilesystemSecurity`)
- [x] Path validation per §12: normalize, resolve `.`/`..`, canonicalize, confirm
      containment, reject symlink escapes
- [x] Git-metadata exclusion per §12.1, all three rules: `.git` at root, a `.git`
      component at any depth, and the gitdir target of a gitfile →
      `PATH_GIT_METADATA` (rule 3 checked *before* the containment check, since its
      target is by construction always outside the root — otherwise every rule-3
      hit would report as a generic escape instead of the more specific error)
- [ ] Exclude `.git` from `list_files` / `search_files` output by default — S10
- [x] AT-001 / AT-002 / AT-033 exercised directly against `FilesystemSecurity`;
      full tool-level exercise (`read_file`/`write_file`/etc.) completes with S10

Found and fixed before it shipped: `PurePosixPath`'s `/` operator *replaces* the
left operand entirely when the right side is absolute
(`Path("/root") / PurePosixPath("/etc/passwd") == Path("/etc/passwd")`) — a
client-supplied `path="/etc/passwd"` would have silently discarded the
repository root and resolved straight to the real host file. Fixed by stripping
a leading `/` before joining; a client-supplied path is always
repository-relative, never absolute, regardless of what it looks like.

### S10 — Filesystem tools ✅
**Spec:** §13, §12.2, §46, §47, §48 · **Satisfies:** FR-007, FR-008, FR-009, FR-010 · **Verified by:** AT-003, AT-018 · **Commit:** f7d4679

- [x] `list_files` — relative path, type, size, mtime, symlink status, hash
- [x] `read_file` — byte/line range, max response size, truncation flag, `sha256`
      in the response (§13.6, so it can be passed back as `expected_sha256`)
- [x] `write_file` — optimistic concurrency via `expected_sha256`
- [x] `patch_file` — unified diff at minimum; report applied/changed/old+new
      hashes/rejected hunks (hand-rolled applier, atomic, cross-checked against
      real `diff -u` output — not shelling out to `patch(1)`)
- [x] `delete_file` — recursive deletion explicit
- [x] `search_files` — glob, text, regex; honours `.gitignore` by default with
      `include_ignored` override (§49). Pure Python, not ripgrep-backed — "SHOULD
      preferably" is optional-preference language, not a requirement
- [x] Size limits per §12.2 (new `filesystem:` config block); binary handling
      per §47 (`is_binary` reported, no accidental UTF-8 misinterpretation)
- [x] AT-003 / AT-018 exercised at both the service layer and through real MCP
      tool calls (`server.call_tool`)

New `state_db_path` config field (§68) and `create_server()` now opens the state
database — the first task needing `workspace_id` to actually resolve to
something. Found and fixed before it shipped: `@dataclass(slots=True)` used as
a *bare* tool return type, with an Optional field and no explicit default,
broke the installed MCP SDK's output-schema generation (it read the slot
descriptor itself as the field's default and failed to serialize it) — silent
for a `list[...]`-wrapped return, only surfacing on `patch_file`/`write_file`/
`read_file`/`delete_file`'s bare-dataclass returns. Fixed by dropping `slots`
from every tool I/O dataclass, with a comment recording why.

### S11 — `workspace_open` and `workspace_info` ✅
**Spec:** §8.3, §25, §34.1, §62 · **Satisfies:** FR-003, FR-030, FR-035 · **Verified by:** AT-032 · **Commit:** 169d1a7

- [x] `workspace_open` per §8.3, creating and starting as needed — the point
      where S5 (registry), S6 (lifecycle), S7 (identity), and S8 (mounts)
      actually come together into a running container
- [x] `workspace_info` returning the same payload (§34.1)
- [x] Report `git_dir_mode`, `ssh_key_mode`, `ssh_key_scope`, `remote_protection`
      (§25 — SHALL, not SHOULD). `remote_protection.status` is honestly
      `"unverified"` — no API credential/check exists until S28
- [x] `server_info` with `version`, `contract_version`, `capabilities` (§33.3),
      plus real (not placeholder) `docker_available`, `active_workspaces`,
      `configured_repositories`
- [x] AT-032: reported modes match actual configuration

New `git_state.py`: a minimal host-side Git reader (branch/head_sha/clean/
upstream) for §62's `git` block — not the structured `git_status` tool (Phase
3), which `git_state.py`'s plumbing is written so that tool can reuse rather
than duplicate.

Found and fixed before it shipped: `docker.from_env()` performs API version
negotiation eagerly and raises immediately if the daemon is unreachable —
calling it unconditionally inside `create_server` would have made the whole
server fail to start whenever Docker is down, directly contradicting §73's
"starts degraded" contract (and the CLI's own docker-unavailable warning,
which already assumed `create_server` would still succeed). `docker_client` is
now `Optional` throughout; `workspace_open`/`workspace_info` fail clearly with
`ContainerRuntimeFailedError` when Docker is unreachable, while `server_info`
degrades gracefully and reports `docker_available: false` rather than raising.

### S12 — Synchronous `exec` ✅
**Spec:** §14, §16 · **Satisfies:** FR-012, FR-013 · **Commit:** 7e8d20b

- [x] argv and shell forms (`bash -c` for shell); argv preferred; exactly one required
- [x] `cwd` defaults to `/workspace`, constrained to allowed container paths
      (must equal or be beneath `workspace_path`)
- [x] Env overrides — passed to `docker exec` directly; never included in error
      `details` (only key names would be, and no path currently needs to)
- [ ] Strict maximum duration; on overrun promote to a job — **deferred**: job
      promotion needs the job store, which does not exist until S14. Overrun
      currently fails the call with `COMMAND_TIMEOUT` rather than silently
      hanging or claiming to promote a job that was never created. Revisit when
      S14/S15 land.
- [x] Bounded output per §15.5 (`max_output_bytes`, `response_tail_lines`,
      already in `ExecutionConfig` from S2)

Runs as the same uid `entrypoint.sh` (S7) already created in the container's
`/etc/passwd` — Docker resolves `$HOME` correctly from that entry for an
`exec`'d process even though it does not inherit anything the entrypoint's
shell `export`ed at PID 1 startup (confirmed empirically).

Two real bugs, both caught by a test that checked the actual process was gone,
not just that the call raised:

- **The timeout was fake.** `asyncio.wait_for` around a blocking read does not
  stop the read — it stops *waiting* for it. A deadline check inside the
  blocking loop is equally useless: a command producing no output (`sleep
  100`) blocks the stream's iterator waiting for the *next* chunk, so a
  deadline check placed there never runs while blocked. Fixed by splitting
  `ExecutionService` into `start`/`consume`/`kill`: `consume` has no timeout
  logic of its own and is run in a thread under `asyncio.wait_for`, which is
  the one thing that can actually interrupt the wait from the outside; `kill`
  is then called explicitly to stop the command, not just stop watching it.
- **The kill target was wrong.** `exec_inspect`'s `Pid` is the process's PID in
  the *host's* PID namespace, not the container's own — confirmed empirically
  (that number doesn't exist when looked up from inside the container). A
  second `docker exec kill <pid>` therefore always failed silently
  ("No such process") without ever raising, since it runs inside the
  container's namespace where the number means nothing. Fixed with `os.kill()`
  directly from the daemon process, which runs on the host (§5.3, §40) and can
  signal that PID correctly — no second `exec` needed at all.

12 new tests, 11 against the real S7 image: argv/shell forms, exactly-one
validation, cwd containment, real non-root identity, real sudo, real env
passthrough, host-visible writes, real output truncation, workspace-not-running,
degraded mode, and the timeout test that verified the process was actually
gone (`ps aux | grep '[s]leep 60'` — not `pgrep -f`, which matched its own
checking command's argv, and not `pgrep -x sleep`, which matched the
container's own PID 1 `sleep infinity`; both were caught as test bugs while
getting this one right).

### S13 — Tool contract export ✅
**Spec:** §33.1, §33.2, §71

- [x] `export-schemas` emitting the full `tools/list` with `inputSchema` and
      `outputSchema` per tool to `schemas/tools.json`
- [x] Commit the file; CI fails when the export differs from the committed copy
- [x] `contract_version` bump rules per §33.2 enforced in review

**Commit:** `2c6d785`

`src/dev_workspace_mcp/schema_export.py` collects every registered tool's
name/description/input_schema/output_schema (sorted by name for a stable
diff) and renders them as `{"contract_version": ..., "tools": [...]}` —
deterministic `json.dumps(..., indent=2, sort_keys=True)` plus a trailing
newline, so re-exporting an unchanged server is byte-for-byte identical to
what's committed. `dev-workspace-mcp export-schemas --config <path> [--out
schemas/tools.json]` is the new CLI command; `schemas/tools.json` (all 11
tools registered through S12) is generated and committed, not hand-written.
`CONTRACT_VERSION = "0.1.0"` is bumped by hand per §33.2's rules — a
diff cannot tell a loosened constraint (patch) from a tightened one
(breaking major), so this stays a reviewed, deliberate edit.

CI gained a dedicated "MCP contract check" step (§71): it builds a throwaway
config, runs the real `export-schemas` CLI, and `diff`s the raw bytes
against `schemas/tools.json`, failing the build on any drift — including
formatting-only drift a semantic JSON comparison would miss.
`tests/test_schema_export.py` covers the same property at the unit-test
level (`collect_tool_schemas` covers every registered tool and is sorted;
`render_schema_document` is deterministic; `export_schemas` writes what it
returns and creates parent directories; a dedicated test loads the
committed `schemas/tools.json` and asserts it still matches a fresh export).

---

## Phase 2 — Persistent Execution

Acceptance: long builds survive individual MCP request timeouts.

### S14 — Job store and lifecycle ✅
**Spec:** §15.1, §15.2, §15.4, §23, §67 · **Satisfies:** FR-014

- [x] Job states per §15.1; records per §15.2 using `stdout_total_bytes` /
      `stderr_total_bytes` / `output_truncated` / `logs_pruned`
- [x] Jobs survive MCP reconnect and outlive the request that created them
- [x] On daemon restart, reconcile against container processes; unrecoverable → `lost`

**Commit:** `f4e347c`

`src/dev_workspace_mcp/jobs/store.py`: `JobStore`, backed by the `job` table
S3 already created. `JobRecord` mirrors §15.2/§67's fields exactly.
Lifecycle transitions are named methods (`mark_starting`, `mark_running`,
`update_output_counts`, `mark_finished`, `mark_lost`) rather than an open
`update(**fields)`, mirroring `DockerWorkspaceManager`'s state methods
(S6) — the small, known set of legal transitions is what makes an illegal
one (e.g. `queued` → `succeeded` with no process ever run) visible as a
call that simply doesn't exist. Because a job's identity lives in SQLite
rather than on some request handler, it survives an MCP reconnect and
outlives the request that created it (§23) for free — any later request,
or even a later daemon process (after a restart), can look it up by id.

`src/dev_workspace_mcp/jobs/reconcile.py`: `reconcile_jobs`, run once at
daemon startup (new §73 step 4, inserted before container reconciliation).
A job left `queued`/`starting` never reached a real process, so it is
unconditionally `lost`. A job left `running` did reach a real `docker
exec`, and its `pid` is a *host* PID-namespace value (S12's finding), so
`os.kill(pid, 0)` is a meaningful liveness probe — but liveness alone
can't resume `running`: the daemon's output-stream reader died with the
old process, and there is no stored `exec_id` to re-attach to (§67's
schema has `pid` only). So `running` is marked `lost` too, on the
conservative side of §15.4's "SHALL transition to lost"; whether the
process is still alive is reported alongside (`(job_id, pid_still_alive)`)
so an operator can clean up an orphan by hand. Resuming a still-running
job "where feasible" (a SHOULD) needs a way to re-attach to a live exec,
which is out of S14's scope.

Found and fixed a real gap while implementing this: §73's startup
sequence never listed a step for job reconciliation at all. Added it as
step 4, renumbering the later steps (container/ssh-agent reconciliation,
repo-root validation, transport start) accordingly; `cli.py`'s `serve()`
now performs steps 3-4 explicitly (open a short-lived connection, run
`reconcile_jobs`, echo a warning per lost job) before building the server
and starting the transport.

`tests/jobs/` (`test_store.py`, `test_reconcile.py`, plus a `conftest.py`
that seeds a real workspace row for the `job` table's foreign key) cover
every transition, idempotent re-reconciliation of an already-terminal
job, and the pid-liveness flag (using a real `subprocess.Popen` for the
"still alive" case, `2**30` for "not alive"). `tests/test_cli.py` gained
an end-to-end test: a real `running` job seeded before `serve` runs,
verifying both the warning text and the row's final `lost` state in the
database — not mocked, so it covers the CLI's actual wiring.

### S15 — Async execution tools ✅
**Spec:** §15.3, §63, §64 · **Satisfies:** FR-015, FR-016, FR-017 · **Verified by:** AT-006 · **Commit:** `6164edf`

- [x] `exec_start`, `exec_status`, `exec_output` (offset/range/stream/line window),
      `exec_cancel`
- [x] Cancellation: graceful termination, then force after a grace period
- [x] AT-006: start a long build, disconnect/reconnect, job still queryable

`src/dev_workspace_mcp/execution/service.py` gained `start_and_open_stream`
/ `drain_stream` / `pid_of`, splitting what the synchronous `exec` tool's
`start`/`consume` do into pieces the async runner needs: docker-py's
`exec_start(stream=True)` issues its HTTP POST — which is what actually
starts the process server-side — eagerly, before returning (verified
against docker-py's own source); only reading the response it returns is
lazy. So the exec can be started and its real pid obtained *before*
handing the stream to a background task to drain, with no race between
"job is running" and "job has a pid". `consume`/`start`/`kill` (S12) are
unchanged.

`src/dev_workspace_mcp/execution/async_runner.py`: `AsyncJobRunner`, one
instance per server process. `JobStore` (S14) makes a job's *identity*
survive an MCP reconnect (§23); `AsyncJobRunner`'s in-memory `LiveJob` per
job id is this process's only source for a running job's actual output
and live progress — deliberately unbounded and unpersisted, a stand-in
for §15.6's capped, host-side log files (S16), not a finished
implementation of them. `output_truncated` stays `False` throughout S15.
A job this process never started (most commonly one `lost` by S14's
startup reconciliation) has no `LiveJob`; its output is simply gone,
consistent with what "lost" means. A background task is kept in a
`set[asyncio.Task]` with a done-callback rather than fired via a bare
`asyncio.create_task(...)` with no reference kept — asyncio's own docs
warn a task with no other strong reference is eligible for GC at any
await point, which would silently abandon a running job mid-stream; a
real correctness bug caught before it shipped, not a lint nit.

`timeout_seconds` (§63) is enforced with `asyncio.wait({drain}, timeout=...)`
rather than `asyncio.wait_for` (S12's synchronous `exec` uses the latter
just fine, since it discards the exec afterward) — `wait_for` would
cancel *awaiting* the drain future on timeout, but the underlying thread
draining the stream keeps running regardless, and there would be nowhere
left to `await` its eventual result once the process is killed and the
stream ends. `asyncio.wait` keeps the future so it can be re-awaited
after termination.

`tools/execution.py`'s new tools read job identity/terminal fields from
`JobStore` and live output/progress from `AsyncJobRunner`; `register()`
gained a required `state_dir: Path` parameter (`state_db_path.parent`) so
`exec_start` can compute §15.6's deterministic
`<state_dir>/jobs/<job_id>/{stdout,stderr}.log` paths at creation time,
even though nothing is written there until S16.

Also fixed while here: `server_info` (S11) had `contract_version`
hardcoded to a literal `"0.1.0"` separate from `schema_export.py`'s own
`CONTRACT_VERSION` (now imported, not duplicated), `running_jobs`
hardcoded to `0` with a `# jobs land in S14` comment (now a real
`JobStore` query), and `capabilities["exec.jobs"]` hardcoded `False` (now
`True`). `schemas/tools.json` re-exported (15 tools);
`CONTRACT_VERSION` bumped `0.1.0` → `0.2.0` per §33.2 (new tools are a
minor bump). `docs/TOOLS.md`'s placeholder, stale since S13 actually
landed (it still claimed `schemas/tools.json` "does not exist until S13
lands"), replaced with a hand-maintained index — §72 asks for this to be
generated where practical, but no task builds a generator, so this stays
manual and may drift; `schemas/tools.json` is what governs.

`tests/workspace/test_async_execution_tools.py` (15 tests, real Docker):
job start/status/success/failure/not-found, output by full/line-range/
byte-range/stream, cancellation (confirms the process is actually gone,
same `ps aux | grep '[s]leep 60'` idiom as S12), a real timeout marking
`timed_out`, the degraded-mode error, and AT-006 itself (query a job by
id alone, discarding the `exec_start` response, as a reconnecting client
would).

### S16 — Job log storage ✅
**Spec:** §15.5, §15.6, §46 · **Verified by:** AT-007 · **Commit:** `bb2ddba`

- [x] Capture streams to `<state_dir>/jobs/<job_id>/{stdout,stderr}.log` — never in
      the repository, never in the container
- [x] `max_output_bytes` as the per-stream per-job on-disk cap
- [x] Past the cap: stop appending, keep counting, keep a `tail_keep_bytes` ring
      buffer appended at job end, mark the gap `--- <N> bytes omitted ---`
- [x] Retention by `job_retention_days` and `job_log_max_total_bytes`; running jobs
      never pruned; pruned jobs still queryable with `logs_pruned: true`
- [x] AT-007: multi-megabyte output stays bounded in responses, retrievable
      incrementally

`src/dev_workspace_mcp/execution/log_capture.py`: `StreamCapture`, one per
job per stream. Every write is flushed immediately — a job's log must be
"retrievable incrementally" (§15.5) while still running, from a different
thread than the one appending to it, and an unflushed write would make a
concurrent reader see less than what was actually produced. Past
`max_output_bytes`, writes stop landing in the file but keep being
counted (`total_bytes`) and kept in a `tail_keep_bytes` ring buffer;
`finish()` (called once, when the job ends) appends the
`--- <N> bytes omitted ---` marker and the kept tail. A stream that never
wrote anything never opens a file at all.

This replaces S15's unbounded in-memory `LiveJob.stdout`/`.stderr`
bytearrays — keeping those around after adding a real cap would have
defeated §15.6's actual point ("§15.5 bounds what a *response* carries;
this section bounds what is *kept*"). `LiveJob` now wraps a
`StreamCapture` per stream instead, and — since disk is the single
source of truth for content once captured — `exec_status`/`exec_output`
read tails/ranged output straight from the log file
(`log_capture.read_log_bytes`) rather than from `AsyncJobRunner`'s
memory; only cheap counters (`total_bytes`/`truncated`) still come from
the live capture object while a job is running (JobStore's own byte
counts are only current once a job finishes). A useful side effect: a
job's *content* now survives this daemon process's own lifetime (subject
to retention), not just its identity (§14) — S14's "no `LiveJob` → output
gone" framing held only until this task gave the log files themselves
somewhere to survive to.

`src/dev_workspace_mcp/jobs/retention.py`: `prune_job_logs`, pruning
completed jobs' log files (never a running job's, never the `JobStore`
row) by two independent triggers — age (`job_retention_days`) and total
on-disk size (`job_log_max_total_bytes`, oldest-finished-first).
§15.6 asks for retention "on a schedule and on demand"; nothing else in
the daemon has a periodic timer (idle-stop, S17, is likely to want one
too), so `tools/execution.py` calls this opportunistically on every
`exec_start` as a practical stand-in for the scheduled half — documented
honestly as that, not a real scheduler.

Found and fixed while testing: `exec_output`'s default response byte
limit was `execution_config.max_output_bytes` alone — the same value
already governing the on-disk cap — so a response with no explicit
`max_bytes` re-truncated right where the omission marker and kept tail
live, hiding the one part of a capped log that says something was
capped. Fixed to default to `max_output_bytes + tail_keep_bytes` (the
whole on-disk capture, itself already bounded), with `max_bytes` still
able to ask for less. Also fixed: `prune_job_logs` was first wired up via
`asyncio.to_thread`, which crashed immediately —
`sqlite3.ProgrammingError: SQLite objects created in a thread can only
be used in that same thread` — since `job_store` shares the request's
connection; it now runs inline (cheap: small metadata reads, occasional
`stat`/`unlink`).

`tests/execution/test_log_capture.py` (18 tests): cap enforcement,
mid-chunk splicing at the cap boundary, the ring buffer keeping only the
most recent `tail_keep_bytes`, the marker text, immediate flush for a
concurrent reader, and `read_log_bytes`'s empty-on-missing/`None`
behavior. `tests/jobs/test_retention.py`: age-based pruning, size-based
eviction oldest-first, a running job never eligible, idempotent
re-pruning, and a `lost` job's logs pruning too. Two new real-Docker
tests in `tests/workspace/test_async_execution_tools.py`: AT-007 itself
(a real ~3.3 MiB job, response bounded, a byte-range read retrieves
exactly what was asked, the on-disk file stays near the cap) and the
omission marker appearing in a real capped job's `exec_output`.

### S17 — Idle stop ✅
**Spec:** §8.1, §27 · **Commit:** `7ab8ca3`

- [x] Stop (never delete) a persistent container after `workspace.idle_stop_after`
      (default 4h) with no request and no running or queued job
- [x] A running job always holds the container up; `never` disables
- [x] Audit the stop; release the workspace `ssh-agent` (§19.2)
- [x] `workspace_open` restarts a stopped workspace

`DockerWorkspaceManager.sweep_idle_workspaces` stops every persistent,
running workspace idle for at least `workspace.idle_stop_after_seconds`
(already parsed by S2's config layer) with no running/queued job in
`JobStore`. Called opportunistically from `workspace_open` — like S16's
job-log retention, there is no scheduler elsewhere in the daemon to call
this "on a schedule" instead. "`workspace_open` restarts a stopped
workspace" needed no new code: `ensure_workspace` (S6/S11) already
restarts anything not `running`, whatever stopped it.

Found and fixed a real correctness gap while implementing this:
`last_used_at` (idleness is measured from it) was only ever bumped by
lifecycle transitions (start/stop/restart/reconcile) — never by actual
usage. A client running only `exec`/`exec_start` calls for hours, with
no lifecycle event in between, would have looked idle and had its
container stopped mid-session. Added `touch_last_used` (a plain
`UPDATE`, not a `DockerWorkspaceManager` method — S9's filesystem tools
never construct a manager and needed to call it too) and wired it into
`exec`, `exec_start`, and `_resolve_repository_root` (the one join query
all six filesystem tools already call, so one touch point covers `list_files`
/ `read_file` / `write_file` / `patch_file` / `delete_file` / `search_files`).
`exec_status`/`exec_output`/`exec_cancel` (job_id, not workspace_id) and
`workspace_info` (explicitly side-effect-free) are deliberately not
touch points — polling a job or reading state isn't "using" the
workspace's resources the way running a command or touching its files is.

`src/dev_workspace_mcp/audit.py`: `write_audit_event`, a deliberately
minimal slice of §24 — enough to audit the one event this task needs
(an append-only JSONL line at `audit.path`, `metadata` passed through
the existing `redact_secrets` per §24.1), not the full audit surface.
§67/§68's `audit_event` SQLite table (a queryable index over the same
records) is not written here; building the dual-write and a query
surface over it, plus covering §24's full event list and denial
auditing, is S29's job (already a dedicated later task) — attempting
that as a side effect of idle stop would be real scope creep beyond
what this task's own checklist asks for.

`ssh-agent` release (§19.2) is a documented no-op: per-workspace
`ssh-agent` processes do not exist yet (S23). `sweep_idle_workspaces`
has nothing to release until then; `stop()` itself is unaffected.

`tests/workspace/test_idle_stop.py` (11 tests, real Docker): stops past
the threshold, leaves a recently-used workspace alone, `never` disables
it, a running job blocks the stop and releases it once the job finishes,
ephemeral workspaces are never swept, the audit event's shape (and that
disabling audit writes nothing), `workspace_open` restarting an
idle-stopped workspace end to end, and — the correctness fix above — a
real `exec`/`read_file` call each bumping `last_used_at`.
`tests/workspace/test_touch_last_used.py` and `tests/test_audit.py`
cover the two new small pieces in isolation (no Docker needed).

### S18 — Recovery and reconciliation ✅
**Spec:** §44, §69 · **Satisfies:** FR-028 · **Verified by:** AT-017 · **Commit:** `4f58aef`

- [x] Daemon restart sequence per §44.1, all eight steps in order
- [x] Container crash → `stopped`/`failed`, repository intact
- [x] Docker/SQLite divergence per §69: unknown managed container recovered or
      quarantined; missing container marked absent; duplicates fail closed
- [x] AT-017: kill the container with changes present; changes survive restart

§69's divergence handling (missing → `absent`, unrecorded → adopted,
duplicate → fail closed) and step 8 (repository state preserved — it was
always a bind mount, never the container's writable layer) already
existed, from S6/S8. What S18 actually added: `DockerWorkspaceManager
.reconcile_all()`, sweeping every persisted workspace through the
existing per-workspace `reconcile()` eagerly at startup (§44.1 steps
1-3) rather than waiting for each one's next operation — S6's lazy
version stays in place alongside it as a safety net. Wired into
`cli.py`'s `serve()` as §73's step 5, skipped when Docker is
unreachable (Step 6, SSH-agent reconciliation, §44.1 steps 4-5, still
lands with S23; step 7 is config-load-time, per S2). A workspace that
fails to reconcile (§69's duplicate-container case) is skipped, not
fatal to the sweep — the same fail-closed contract `reconcile()` itself
already had, just not allowed to abort every *other* workspace's chance
to reconcile too.

Also added the "crash" half of "container crash → `stopped`/`failed`"
(§44.2), previously just "→ `stopped`" always: `_state_from_docker_container`
now returns `failed` for `dead`, an OOM kill, or a Docker-level runtime
error — unambiguous crash signals — and `stopped` otherwise. Deliberately
does **not** treat a plain nonzero exit code as a crash signal on its
own: an intentional `stop()` sends SIGTERM, and a process with no
handler for it (`sleep`, and plenty of real long-running processes)
legitimately exits 128+15=143 — caught by a test that initially failed
against a first version of this function that *did* use bare exit code,
which would have misclassified every ordinary `stop()` as a crash.

`tests/workspace/test_recovery.py`: `_state_from_docker_container`
against fake container objects for the classification logic itself (an
OOM kill isn't worth reproducing for real — slow and flaky across
kernels); `reconcile_all` against real Docker for the sweep itself
(multiple workspaces, one stopped out of band; a duplicate-container
workspace skipped without aborting the others; the empty-DB no-op); and
AT-017 end to end through the real MCP tool layer — write a file via
`exec`, `container.kill()` (a real SIGKILL, not the `exec`/stop tools),
reconcile with a fresh `DockerWorkspaceManager` over the same on-disk
state DB (simulating a daemon restart), confirm the file survived and
`workspace_open` recovers the workspace to `running` with the file still
there.

**Phase 2 (S14-S18) complete.** Acceptance: long builds survive
individual MCP request timeouts — met by S14/S15/S16 together
(`exec_start`/`exec_status`/`exec_output`/`exec_cancel`, backed by
`JobStore` and on-disk log capture, all independent of any single
request's lifetime).

---

## Phase 3 — Structured Git

Acceptance: a client can implement, validate, and commit a change safely.

### S19 — Git execution plumbing ✅
**Spec:** §20.1, §18.7, §44.4 · **Verified by:** AT-025 · **Commit:** `142b88d`

- [x] Structured Git tools execute **in-container** via the execution service, as
      the container user
- [x] `git_status` / `git_diff` / `git_log` MAY run host-side for speed, as the
      daemon uid
- [x] Host-side reads always invoked with `-c core.hooksPath=<empty-dir>
      -c core.fsmonitor=` and `GIT_OPTIONAL_LOCKS=0`
- [x] Hooks run normally for in-container operations — they are a feature (§18.7)
- [x] Interrupted-operation detection: merge, rebase, cherry-pick, bisect (§44.4)
- [x] AT-025: a `core.hooksPath` hook inside the working tree does not execute
      during a host-side `git_status`

New `src/dev_workspace_mcp/git/` package. `git/service.py`'s
`run_git_host_side` is the one host-side entry point `git_status`/
`git_diff`/`git_log` (S20/S21) will call — real `-c core.hooksPath=<a
fresh empty dir>`, `-c core.fsmonitor=`, `GIT_OPTIONAL_LOCKS=0`, and the
*real* environment plus that one override (not a replacement `env={...}`,
which drops `PATH`). There is no separate "in-container git runner": per
§20.1's own rationale (one execution identity, one credential), a
mutating structured tool (`git_commit`/`git_pull`/`git_push`, S22/S26/S27)
will call `ExecutionService`/`AsyncJobRunner` directly with a `["git",
...]` argv, exactly as `exec("git ...")` already does — hooks run
normally there because nothing suppresses them, which is the point
(§18.7: hooks are a feature, not a threat, under the trusted-container
model).

`detect_interrupted_operation` (§44.4) reads Git's own marker files
(`MERGE_HEAD`, `rebase-merge`/`rebase-apply`, `CHERRY_PICK_HEAD`,
`BISECT_LOG`) under the resolved `.git` directory — following one level
of gitfile `gitdir:` indirection for a linked worktree/submodule (§18.2),
verified against a real `git worktree add`. Diagnostic only: unlike
`workspace/mounts.py`'s `resolve_git_dir`, it does not authorize the
resolved path against anything.

Also refactored `git_state.py` (S11) to call `run_git_host_side` instead
of its own ad hoc `subprocess.run` — which passed a *replacement*
`env={"GIT_OPTIONAL_LOCKS": "0"}`, silently dropping `PATH`. It happened
to keep working only because `git` sits at a location POSIX's default
fallback search path covers on every host tested here; a regression test
(`test_run_git_host_side_finds_git_via_path`) guards the fixed version.

AT-025's literal phrasing ("a `core.hooksPath` hook... does not
execute") doesn't map onto a real, standard Git hook: no stock hook
fires on `status`/`diff`/`log`. The actual host-side-read hook vector is
`core.fsmonitor`, which those commands genuinely do consult — that's why
§18.7 calls it out by name alongside `core.hooksPath`. The test proves
both: a real script configured via `core.fsmonitor` does *not* run
through `run_git_host_side`, and does run through a plain, unmediated
`git status` on the same repository — so the test demonstrates real
suppression, not just "the script never had a reason to fire."
`core.hooksPath`'s own override is verified more directly, by confirming
our value wins over a repo-configured one in `git config`'s own output.

`tests/git/test_service.py` (15 tests, no Docker): the two overrides
above (both proven, not assumed), exit-code/stdout/stderr plumbing, the
`PATH`-drop regression guard, and `detect_interrupted_operation` for
every §44.4 state plus the clean-repo, non-repo, and linked-worktree
cases.

### S20 — `git_status` ✅
**Spec:** §17.2, §22.3 · **Satisfies:** FR-018 · **Verified by:** AT-020 · **Commit:** `20a3e23`

- [x] Structured output per §17.2: branch, head_sha, upstream, ahead/behind,
      detached, clean, staged/unstaged/untracked
- [x] Report conflict and interrupted state rather than repairing it
- [x] AT-020: a merge conflict is reported, not auto-repaired

`src/dev_workspace_mcp/git/status.py`'s `read_git_status` parses a
single `git status --porcelain=v2 --branch --untracked-files=all -z`
call rather than several separate `git` invocations — branch, head,
upstream, ahead/behind, and every path's staged/unstaged/untracked/
conflicted state all come from one consistent snapshot. `-z` (NUL
records, no C-style path quoting) is what makes it reliably parseable.
Beyond §17.2's illustrative fields, the output also carries `conflicted`
(a separate list, not folded into staged/unstaged) and
`interrupted_operation` (S19's `detect_interrupted_operation`) — real,
additive fields AT-020 needs that the spec's own JSON example, marked
"SHOULD include", never claimed to be exhaustive.

Getting the porcelain v2 field layout right took real verification, not
assumption: a first version used the same field-count split for both `1`
(ordinary changed) and `2` (rename/copy) records, which is wrong — a `2`
record has one extra field (the rename/copy score, e.g. `R100`) before
the path. Caught by `test_status_on_a_renamed_staged_file`, which failed
with `staged == ['R100 a-renamed.txt']` against a real `git mv` before
the fix. Every record type (`1`, `2`, `u` unmerged, `?` untracked, plus
the `# branch.*` headers) was verified against real `git status -z`
output before being trusted, not written from memory of the format spec.

`tools/git.py`'s `register()` takes only `db` — no `docker_client`, no
container config — since `git_status` is host-side only (§20.1) and
needs nothing else; `resolve_repository_root` (newly extracted from
`tools/filesystem.py`'s private copy into `workspace/manager.py`, shared
by both now) resolves `workspace_id` → repository root without touching
Docker. Deliberately does not call `touch_last_used` (§8.1's idle-stop is
measured against *container* usage; a host-side read never touches the
container at all, so counting it would be measuring the wrong thing).

`tests/git/test_status.py` (8 tests): clean repo, staged/unstaged/
untracked, a path both staged and unstaged, a real renamed file, real
ahead/behind against a real remote, detached HEAD, an unborn branch, and
AT-020 itself — a real merge conflict, confirming `conflicted` and
`interrupted_operation` report it without repairing anything.
`tests/workspace/test_git_status_tool.py` (4 tests, real Docker): the
tool end to end through `workspace_open`, reflecting a write made
through `write_file`, `WORKSPACE_NOT_FOUND` on an unknown workspace, and
confirming the tool works even when Docker itself is unreachable —
§20.1's whole point.

`schemas/tools.json` re-exported (`git_status` is a new tool);
`CONTRACT_VERSION` bumped `0.2.1` → `0.3.0` per §33.2 (minor).

### S21 — `git_diff` and `git_log` ✅
**Spec:** §17.3, §46 · **Satisfies:** FR-019 · **Commit:** `bc2c76d`

- [x] Working-tree, staged, and commit-to-commit diffs; path filter; stat-only
- [x] Bounded output with explicit truncation and follow-up retrieval
- [x] Bounded log

`src/dev_workspace_mcp/git/diff.py`'s `read_git_diff` selects its mode
from which parameters are given, not three code paths: no `base`/`staged`
→ working-tree (`git diff`); `staged=True` → `--cached`; `base` given
(`head` defaults to `HEAD`) → commit-to-commit. `staged` and `base`/`head`
together is rejected as ambiguous. Bounded by `max_bytes` (default 1 MiB
— no config block exists whose sole purpose would be one field, so this
stays a module constant, overridable per call) with an `offset` for
follow-up retrieval — the same byte-range shape `exec_output` already
uses, verified by a test that reassembles a full diff from two bounded
calls and confirms it matches an unbounded one exactly.

`src/dev_workspace_mcp/git/log.py`'s `read_git_log` uses `git log
--pretty=format:` with ASCII field/record separators (`\x1f`/`\x1e`)
rather than a human-oriented delimiter — a commit subject can contain
almost anything a human-readable separator (`|`, `,`, tabs) might
collide with, and confirmed by a test with literal `|`/`,`/`"` in a real
commit subject. An unborn branch's `git log` failure
("does not have any commits yet") is treated as an empty log, not an
error. Bounded by `max_entries` (default 50), with `paths` and `ref`
filters.

Both are host-side only (§20.1), added to the existing `tools/git.py`
alongside `git_status` (S20) rather than a new module — same
`register(server, *, db)` shape, same `resolve_repository_root`, no
Docker dependency.

`tests/git/test_diff.py` (10 tests) and `tests/git/test_log.py` (7
tests): every mode, the two rejected-combination cases, path filtering,
stat-only, the bounded-output/offset reassembly proof, an unborn branch,
`ref` selecting a specific branch, and the separator-collision proof.
`tests/workspace/test_git_diff_log_tools.py` (3 tests, real Docker): both
tools end to end through `workspace_open`, including a write made
through `write_file` showing up in the working-tree diff and *not* the
staged one.

`schemas/tools.json` re-exported (two new tools); `CONTRACT_VERSION`
bumped `0.3.0` → `0.4.0` per §33.2 (minor).

### S22 — `git_commit` ✅
**Spec:** §17.4, §20.6, §45, §65, §18.9 · **Satisfies:** FR-020, FR-024 · **Verified by:** AT-008, AT-019 · **Commit:** `43abc8c`

- [x] Explicit `paths`; stages exactly what it is told (no separate `git_add`)
- [x] `expected_branch` / `expected_head_sha` guards; mismatch → `GIT_HEAD_CHANGED`
- [x] Strict mode: refuse when a path outside `paths` is staged
- [x] `amend` parameter, governed by `git_policy.amend_published`
- [x] `no_verify` parameter
- [x] `default_branch_commit` policy
- [x] Preconditions per §20.6; failures leave state unmodified
- [x] AT-008: guarded commit against a stale HEAD fails
- [x] AT-019: clean-tree-required operation fails with unrelated changes present

The first structured *mutating* Git tool — runs in-container (§20.1),
through `ExecutionService`, exactly like `exec`. Two commands, not one:
`git add -- <paths>` then `git commit --only -m <message> -- <paths>`.
"No separate `git_add`" (§17.4) means the *client* never makes a separate
tool call to stage anything, not that the implementation avoids `git add`
as a subprocess — verified it can't: `git commit --only <path>` alone
raises "pathspec ... did not match any file(s) known to git" for a
genuinely new, untracked file (a real failure hit while testing, not
theorized). `--only` is still essential: it's what makes "stages exactly
what it is told" true in the presence of anything *else* already staged
— without it, a plain `git commit -m ...` would commit that too, which is
exactly what strict mode (a separate, earlier check) and non-strict mode
(silently leave it staged, uncommitted) both depend on.

A second real bug, same root cause pattern as the first: `git/commit.py`'s
argv builders initially omitted the leading `"git"` — `run_git_host_side`
(S19) prepends it itself, but the execution service (S12/S15) that runs
`git_commit`'s in-container commands does not; the omission surfaced as
`exec: "add": executable file not found in $PATH` against a real
container, not a hypothesis.

§20.6's preconditions run host-side, before the container is asked to do
anything, and fail closed: interrupted-operation detection (§44.4,
reused from S19) → `GIT_CONFLICT`; `expected_branch`/`expected_head_sha`
(§45) → `GIT_HEAD_CHANGED`; strict mode → `GIT_DIRTY`. Two policy checks
from §18.9, both evaluated the same way:

- `default_branch_commit`: the default branch is read from
  `refs/remotes/origin/HEAD` — a *local* ref a normal `git clone` sets up
  without any network round trip, verified against a real clone. `None`
  when never set (e.g. a repository the daemon didn't clone itself) —
  enforcement is skipped rather than guessed at with "main"/"master".
- `amend_published`: `git merge-base --is-ancestor HEAD @{upstream}`,
  checked against the *pre-amend* HEAD, verified against a real push.
  No upstream at all is treated as "not published" deliberately, not
  "unknown, block to be safe" — with no upstream branch there is
  categorically no "upstream branch" to have been published to (a
  distinct, verified error from "not an ancestor").

`src/dev_workspace_mcp/git/commit.py`: `build_add_argv`, `build_commit_argv`,
`detect_default_branch`, `is_head_published` — the host-side-checkable
pieces, pure enough to unit-test without touching a container.
`tools/git.py`'s `git_commit_tool` wires them to `ExecutionService`,
sharing `register()` with the read-only Git tools (S20/S21) but now also
taking `docker_client`/`container_config`/`execution_config`/
`git_policy_config`/`audit_config`. Emits a `write_audit_event` per commit
(§20.7), the same minimal audit infrastructure S17 built — the fuller
§24 event list and denial-fidelity work stays S29's job.

`tests/git/test_commit.py` (9 tests, no Docker): the argv builders
(including the missing-`"git"` regression), `detect_default_branch`
against a real clone and a repo without one, `is_head_published` for a
published, an unpushed, and a no-upstream commit.
`tests/workspace/test_git_commit_tool.py` (12 tests, real Docker): a
basic commit, staging exactly the given paths (a second untracked file
stays untracked), the empty-`paths` rejection, both `expected_*` guards
failing closed with nothing committed, strict mode rejecting (and
non-strict mode ignoring) an extraneous staged file, `default_branch_commit:
deny` against a real pushed default branch, `amend` allowed when
unpublished and denied when published (both against a real remote),
`no_verify` bypassing a real failing `pre-commit` hook, and a real merge
conflict (the same scenario AT-020 uses for `git_status`) triggering
`git_commit`'s own interrupted-operation precondition.

`schemas/tools.json` re-exported (one new tool); `CONTRACT_VERSION`
bumped `0.4.0` → `0.5.0` per §33.2 (minor).

---

## Phase 4 — GitHub Synchronization

Acceptance: a fast-forward commit reaches an authorized repository from both the
structured tool and `exec("git push")`, with no credential extractable.

### S23 — SSH key provisioning (agent mode) ✅
**Spec:** §19.1, §19.2, §19.4 · **Satisfies:** FR-025, FR-037 · **Verified by:** AT-013, AT-036

- [x] Per-workspace `ssh-agent`; load the workspace key; bind-mount the socket at
      `/run/mcp/ssh/agent.sock`; set `SSH_AUTH_SOCK`
- [x] Key material never enters the container filesystem, env, or argv
- [x] Remove the key from the agent whenever the workspace stops (including idle
      stop); terminate the agent on delete
- [x] `file` mode as the documented weaker alternative, reported via `ssh_key_mode`
      (already true since S11 — `workspace_info`'s `ssh_key_mode=ssh_config.mode`
      predates this task)
- [x] Agent reconciliation on daemon restart (§44.1 step 4) — without it a running
      container's key is silently unusable
- [x] AT-013: no host credentials present; no key material discoverable under agent
      mode, including during an in-flight push
- [x] AT-036: key unloaded after stop; no agent after delete

**Commit:** `272fc89`

D3 was decided manual (see Open Decisions above): the operator generates
the deploy-key pair and registers its public half on GitHub by hand,
then configures `repositories[].ssh_key`; this task builds only the
per-workspace `ssh-agent` that loads and serves an already-provisioned
key. No `administration: write` credential is needed or used anywhere
here.

New `src/dev_workspace_mcp/workspace/ssh_agent.py` — `SSHAgentManager`,
backed by a new `ssh_agent` table (`db/schema.py` migration, deliberately
**not** foreign-keyed to `workspace`: `workspace_open` has to start the
real agent process, and persist a record of it so a crash between the
two doesn't leak an untracked process, *before* `ensure_workspace`
creates the `workspace` row a brand-new persistent workspace's own
mounts depend on — a real ordering constraint a first version of this
migration got wrong, caught by `IntegrityError: FOREIGN KEY constraint
failed` the first time the Docker-level tests actually ran). Each
`ssh-agent -D -a <socket>` runs in the foreground rather than the
default fork-and-detach mode specifically so its `Popen.pid` is the
*real*, directly-signalable agent process — verified empirically (a real
`ssh-add`/`ssh-add -l` round-trip against the bound socket, immediately
after spawn) before trusting it. `ensure()` is idempotent: a live agent
already tracked for the workspace is reused rather than restarted
(`ssh-add` on an already-loaded key succeeds cleanly rather than
erroring — also verified empirically — which is what lets `ensure()`
unconditionally re-`ssh-add` on every call rather than first checking
whether the key is already loaded, correctly covering both "already
running with the key" and "already running, key unloaded by a previous
`stop`" in one code path).

**A real, non-obvious bug this task found and fixed**: `_terminate_pid`
originally sent `SIGTERM` and nothing else. `terminate()`'s own test
(kill, then assert the pid is dead) failed intermittently — `os.kill(pid,
0)` kept reporting the process alive well after the signal.
`/proc/<pid>/stat` showed state `Z`: a zombie, real and unreaped, because
nothing had ever called `waitpid` on it. Fixed by having `_terminate_pid`
call `os.waitpid(pid, 0)` after the signal, tolerating `ChildProcessError`
for the one case where that call is expected to fail — a pid recovered
from the `ssh_agent` table after a daemon *restart* is not this process's
own child (its actual parent died with the previous daemon), so
`waitpid` can't reap it; the signal still reaches and terminates it
either way, reaping is just someone else's job in that case. Verified
against a real zombie, reproduced deliberately, before trusting the fix.

`workspace_open` (`tools/workspace.py`) provisions the agent for
**persistent** workspaces only: `persistent_workspace_id(repository.id)`
is a pure, deterministic function of the repository id — the same one
`ensure_workspace` computes internally — so the agent (and its socket
path, needed for the container's mount list) can be readied *before*
`ensure_workspace` creates the container. `mode: "ephemeral"` workspace
ids include a random suffix only `ensure_workspace` generates internally,
after mounts must already exist, so ephemeral-mode agent provisioning is
a **known, deliberately scoped-out gap**: an ephemeral workspace under
`ssh.mode: agent` gets no agent socket at all right now. This is judged
an acceptable, narrow gap — §8.1's own examples for ephemeral workspaces
(clean-room validation, reproducing CI, verifying installation
instructions) lean read-only/validation-oriented, where SSH push
capability matters less than for the persistent dev-workspace flow this
MVP centers on — but it is a real, documented limitation, not an
oversight.

`DockerWorkspaceManager.stop`/`.delete` (`workspace/manager.py`) gained
an optional `ssh_agent_manager` — `stop` (including idle-stop's own call
path) calls `unload_key` (§8.1/§19.2: the key comes out, the agent
process itself keeps running — cheaper than a full teardown/restart
cycle every idle cycle, and correctly handled by `ensure()`'s
unconditional re-`ssh-add` on the next open); `delete` calls `terminate`.
`cli.py`'s startup sequence gained step 6 (§44.1 steps 4-5), running
*after* step 5's container reconciliation (it needs that step's
up-to-date `running` state) and skipped under the same "Docker
unreachable" condition step 5 already is: `SSHAgentManager.reconcile`
unconditionally tears down every previously-tracked agent first — §44.1
step 4's own premise is that the previous agent died with the daemon, so
nothing is assumed reusable — then starts a fresh one only for workspaces
`running_workspace_key_paths` (new) confirms are still `running`, which
also covers step 5 (an agent for a workspace that is no longer running
is torn down and never restarted).

Three test files: `tests/workspace/test_ssh_agent.py` (15 tests, real
`ssh-agent`/`ssh-add`/`ssh-keygen` processes, no Docker — `ensure`,
`unload_key`, `terminate`, `reconcile`'s both halves, key-path
resolution for both `scope: repository` and `scope: shared`, the
zombie-reaping fix); `tests/workspace/test_ssh_agent_provisioning.py` (4
tests, real Docker + the real S7 image, real `ssh-keygen` — the socket
mount and `SSH_AUTH_SOCK` actually work end-to-end through `exec`,
`file` mode correctly gets neither, AT-013 (a real private key's own
distinctive payload fragment is not found anywhere in the container
filesystem via `grep`/`find`, bounded with `--exclude-dir`/`-xdev` after
an unrestricted `/` walk timed out), and AT-036 end-to-end through
`stop`/`delete`); `tests/test_cli.py` gained one test proving step 6's
*orphan-termination* half through the real `serve` command's actual
wiring, not the pieces in isolation — the fresh-start half is not
separately re-proven at the CLI level, since it is already covered
thoroughly by `test_ssh_agent.py`'s own `reconcile` tests and setting up
a real, still-`running`-after-reconciliation Docker container purely to
re-exercise already-tested composition was judged not worth the added
test complexity.

AT-013's "including during an in-flight push" is satisfied structurally
rather than by a timing-dependent test: the key file is never mounted,
copied, or otherwise written into the container at any point — only a
Unix socket is, and the agent process (not the key) serves signature
requests over it — so there is no time window, mid-push or otherwise,
during which the key could appear; the at-rest filesystem/name search
already performed covers every point in time, not just one.

A real leak was also found and fixed in the test suite itself (not
product code): the Docker-container teardown fixture force-removes
containers but was never going to touch a host-side `ssh-agent`
process, so the provisioning tests' first run left five real orphaned
agents running. Fixed with a dedicated autouse cleanup fixture reading
the same state db the test used.

`schemas/tools.json` needed no regeneration — no new tools or output
fields (confirmed by re-running `export-schemas` and diffing — no
drift).

### S24 — SSH client configuration and host-key pinning ✅
**Spec:** §19.3 · **Satisfies:** FR-033 · **Verified by:** AT-034

- [x] Container-local `$HOME/.ssh/config` — `IdentitiesOnly`, `StrictHostKeyChecking yes`,
      pinned `UserKnownHostsFile`
- [x] Pinned GitHub host keys; no trust-on-first-use; never honour a `known_hosts`
      or SSH config from inside the repository
- [x] Optional `url.<base>.insteadOf` so an HTTPS `origin` still uses the key
- [x] AT-034: substituted host key → mismatch failure, no prompt

**Commit:** `e127fc1`

`docker/default/entrypoint.sh` now writes `$HOME_DIR/.ssh/config` (mode
`700`/`600`, owned by the checkout identity) with `Host github.com`,
`User git`, `IdentitiesOnly yes`, `StrictHostKeyChecking yes`, and
`UserKnownHostsFile /etc/mcp/ssh/known_hosts` — `IdentityFile ~/.ssh/id_mcp`
is added only under `ssh.mode: file` (agent mode has nothing static to
point at). A new `MCP_SSH_MODE` env var (from `ContainerConfig`'s
`ssh_config.mode`, threaded through `build_workspace_run_kwargs`) selects
that branch. The root-only block also gains
`git config --system url."git@github.com:".insteadOf "https://github.com/"`
so an `origin` authored as an HTTPS URL still pushes/pulls over the pinned
SSH identity (verified by asking git itself: `git ls-remote --get-url
origin` after `git remote add origin https://github.com/...`). The pinned
host-key file itself (`ssh.known_hosts`) is mounted read-only at a fixed
path outside the working tree
(`workspace/container_config.SSH_KNOWN_HOSTS_CONTAINER_PATH` =
`/etc/mcp/ssh/known_hosts`, via the new `workspace/mounts.py:
build_known_hosts_mount`) — never inside `/workspace`, so nothing a
container process reads from the checked-out repository (its own
`.ssh/config` or a `known_hosts` committed to the repo) can substitute for
it.

Three real bugs found and fixed while building this:

1. **Design flaw — known_hosts auto-creation was reachable only through
   `load_config`.** Docker turns a bind mount of a *missing* host file into
   an empty *directory* at the target path instead of failing loudly, so
   `ssh.known_hosts` has to exist on disk before the first mount. The
   creation logic (`ensure_known_hosts_file_exists`) lived only inside
   `load_config()`, but `create_server()` — the thing that actually depends
   on the file existing — can be (and throughout the test suite, is) called
   with a `ServerConfig` built directly via `ServerConfig.model_validate`,
   bypassing `load_config()` entirely. This surfaced as 53 failing tests
   across `test_async_execution_tools.py`, `test_execution_tools.py`,
   `test_git_commit_tool.py`, `test_git_diff_log_tools.py`,
   `test_git_status_tool.py`, `test_idle_stop.py`, `test_recovery.py`, and
   `test_workspace_tools.py`, all with the same
   `"bind source path does not exist"` root cause the moment
   `workspace_open`'s new known_hosts mount was added. Fixed by making
   `ensure_known_hosts_file_exists` public and calling it from
   `create_server()` itself (alongside `open_db`, which already creates its
   own parent directories lazily for the same reason) — `create_server` is
   now self-sufficient regardless of how its `config` argument was built.
2. A `test_https_origin_routed_over_ssh_via_insteadof` helper created only
   an empty `.git` *directory* (`(repo_path / ".git").mkdir()`) instead of a
   real repository, so `git remote add`/`git ls-remote` had nothing valid
   to operate on. Fixed by running a real `git init -q -b main` first.
3. `test_at_034_substituted_host_key_fails_closed_no_prompt` originally
   chained `ssh-keyscan` onto the same `exec` call as the multi-thousand-line
   `apt-get install openssh-server` output. `stdout_tail` is response-bounded
   (§15.5), and the test's own parsing took the *whole* captured blob and
   split on the first space — which, once the real keyscan line was pushed
   out of the tail, silently grabbed a fragment of an apt-get log line
   instead, producing a malformed known_hosts entry and a misleading
   "No ED25519 host key is known" failure rather than the intended
   "REMOTE HOST IDENTIFICATION HAS CHANGED" mismatch. Fixed by giving
   `ssh-keyscan` its own separate `exec` call, so its `stdout_tail` is
   cleanly just the keyscan line.

AT-034 itself is verified generically rather than against the real GitHub
host key: a real `openssh-server`/`sshd` is installed and started on a
nonstandard port inside the workspace container, `ssh-keyscan` captures its
actual host-key line, and a freshly generated, deliberately wrong key is
substituted into a second `known_hosts` file. With `BatchMode=yes`
throughout (proving no interactive "trust this key?" prompt is possible on
a non-interactive `exec`, and that a real client fails outright on a
mismatch rather than hanging): the *correct* pinned key reaches the auth
stage (`Permission denied`, no host-verification warning at all) — a sanity
check that the harness and the real keyscan line both actually work —
while the *substituted* key fails specifically with
`REMOTE HOST IDENTIFICATION HAS CHANGED` and never reaches the auth stage
(`Permission denied` absent). `tests/workspace/test_ssh_client_config.py`
(8 tests, all against a real Docker daemon and the real S7 default image)
covers the written config's exact contents, its permissions and ownership,
the known_hosts mount's read-only enforcement and pinned content, that the
mount never appears anywhere under `/workspace`, the `insteadOf` rewrite,
AT-034 itself, and `file`-mode's `IdentityFile` line. No new tools or
output fields were added, so `schemas/tools.json` needed no regeneration
(confirmed by re-running `export-schemas` and diffing — no drift).

### S25 — Remote identity validation ✅
**Spec:** §20.2, §7.3 · **Satisfies:** FR-023 · **Verified by:** AT-009, AT-026, AT-027, AT-028, AT-030

- [x] Resolve remote → URL; scheme allowlist; reject `ext::`, `file://`, and any
      local-command transport, with `-c protocol.ext.allow=never`
- [x] Normalize to `owner/repo` (§7.3) and compare against configured identity and
      `github.repositories`
- [x] Honour `expected_remote_repository` regardless of server config
- [x] Reject client-supplied `--upload-pack` / `--receive-pack` / `--exec`
- [x] AT-009 unauthorized remote · AT-026 `ext::` rejected · AT-027 key scope stops
      both paths · AT-028 SSH-form `origin` works · AT-030 submodule URL not fetched

**Commit:** `2c48fe4`

New `src/dev_workspace_mcp/git/remote.py` — `validate_remote_identity`,
the §20.2 mechanism `git_pull`/`git_push` (S26/S27) will call as their
first precondition, the same way `git_commit` already runs its own
host-side precondition reads (status, interrupted-operation detection)
before anything mutates state (§20.6). It has no MCP tool of its own.

`resolve_remote_url` runs `git remote get-url <name>` host-side via the
existing `run_git_host_side` (S19), with `-c protocol.ext.allow=never`
added defensively even though `get-url` never itself invokes a transport.
`classify_url_scheme` recognizes `https://`, `ssh://`, the SCP-like
`git@github.com:owner/repo.git` form (which Git treats as SSH despite
having no scheme prefix at all), and calls out `ext::` and `file://`
explicitly per spec — both are rejected unconditionally, never merely
gated by `allowed_url_schemes`, since either is a local-code/local-file
hazard and not just an unauthorized-remote one. `normalize_to_owner_repo`
implements §7.3's three documented forms with `None` for anything else
(including a non-GitHub host), which callers treat as a mismatch, not as
"unrecognized, so allow." `validate_remote_identity` composes all of
this: resolve → reject disallowed/dangerous scheme → normalize → compare
against the repository's configured `github` identity and
`github.repositories` → apply a caller-supplied
`expected_remote_repository` override on top, per §20.2's "honoured
regardless of server-side configuration." Rejecting client-supplied
`--upload-pack`/`--receive-pack`/`--exec` is satisfied by construction:
this module (and the future `git_pull`/`git_push` signatures it feeds)
take a remote *name*, never a raw Git flag, so there is no parameter for
one to travel through.

`tests/git/test_remote.py` (28 tests, real `git init`/`git remote add`,
no Docker needed since validation never touches a container) exercises
every documented URL form, `ext://`/`file://`/unknown-scheme rejection,
an `allowed_url_schemes` narrowed to `["ssh"]` actually stopping an
HTTPS remote, the `github.repositories` allowlist, and
`expected_remote_repository` both overriding and being overridden by.

Coverage against the five listed ATs is real but partial by necessity,
since `git_pull`/`git_push` (S26/S27) and SSH key provisioning (S23,
blocked by D3) don't exist yet:

- **AT-009** (unauthorized remote) and **AT-027**'s validation half (key
  scope): identical at this layer — a remote resolving to an owner/repo
  outside the configured identity/allowlist raises
  `GitRemoteNotAllowedError` before anything is invoked against it,
  which is what makes "a structured push SHALL fail" true in the first
  place. AT-027's second path (`exec("git push")` failing at GitHub
  specifically due to key scope) needs a real, installed deploy key —
  deferred to S23.
- **AT-026** (`ext::` rejected before any authenticated operation): fully
  verified — `classify_url_scheme`/`validate_remote_identity` reject it
  before `resolve_remote_url` even returns to a caller that could act on
  it.
- **AT-028** (SSH-form remote): the validation half — the SCP-like form
  normalizes and passes exactly like the other two — is verified;
  `exec("git push")` and a structured `git_push` actually succeeding
  over it needs S27 and real GitHub connectivity.
- **AT-030** (submodule URL substitution): this AT's real guarantee is
  implemented by S26's already-planned `--no-recurse-submodules` on the
  fetch step (`git_pull` never asks Git to contact a submodule's own
  remote at all, so nothing needs validating there) — not by this
  module. Noted here as a design constraint to carry into S26, not
  something to re-verify at this layer.

### S26 — `git_pull` ✅
**Spec:** §20.5, §18.8 · **Satisfies:** FR-021 · **Verified by:** AT-010

- [x] Fetch then integrate per `pull_strategy`, default `ff_only`
- [x] `--no-recurse-submodules` on the fetch step
- [x] Non-fast-forward under `ff_only` → `GIT_NON_FAST_FORWARD`, never escalate
- [x] AT-010: push against a diverged remote fails without forcing

**Commit:** `4a86a6b`

New `src/dev_workspace_mcp/git/pull.py` (pure argv builders and outcome
classification, mirroring `git/commit.py`'s own split) plus a `git_pull`
tool in `tools/git.py`, wired the same way `git_commit` (S22) is: §20.6
preconditions host-side (interrupted-operation detection,
`expected_branch`/`expected_head_sha` guards, and — new for a
remote-contacting operation — a detached-HEAD check, since there is no
branch to integrate into), then S25's `validate_remote_identity` *before*
anything is fetched, then two in-container commands through the
execution service: `git fetch --no-recurse-submodules <remote>` (§18.8 —
this, not any special handling in this module, is AT-030's actual
mechanism: a structured pull never asks Git to contact a submodule's own
remote at all, regardless of what a tracked `.gitmodules` says), then an
integration step chosen by `git_policy.pull_strategy` (`git merge
--ff-only`, `git merge`, or `git rebase` against `<remote>/<branch>` —
the same same-named-branch convention §20.3's refspec table already
assumes for the opposite direction). A `git merge --ff-only` failure is
classified as `GIT_NON_FAST_FORWARD` only when Git's own stderr contains
its specific "Not possible to fast-forward" wording (verified empirically
against a real diverged repository before trusting it) — anything else
(e.g. uncommitted local changes the merge would overwrite) surfaces as an
ordinary `COMMAND_FAILED` instead, not misclassified as the recoverable
non-fast-forward case. Whether the result was a genuine fast-forward is
verified after the fact via a real `git merge-base --is-ancestor`, not
inferred from `strategy` alone — plain `merge`/`rebase` can *also*
resolve to a fast-forward when there was nothing to actually integrate.

`git_pull` needed two things no earlier structured tool did:
`RepositoryRegistry` (to look up the repository's configured `github`
identity for §20.2 validation) and `GithubConfig` (for the
`github.repositories` allowlist) — both threaded through
`git_tools.register()` and `server.py`'s `_register_tools`, which is why
this task also touches `tests/workspace/test_git_status_tool.py`'s
degraded-mode `git_tools.register(...)` call site.

`tests/git/test_pull.py` (16 tests) proves the real fetch/integrate
*mechanics* — fast-forward success, already-up-to-date no-op,
non-fast-forward failure with Git's exact real wording, `merge`
producing a genuine merge commit only on true divergence, `rebase`
replaying local commits — against real local Git repositories, with no
Docker and no remote-identity validation involved (this tool doesn't
touch a remote at all; validation is S25's concern, tested separately).

`tests/workspace/test_git_pull_tool.py` (7 tests, real Docker) covers the
MCP-tool-level wiring: AT-026 (`ext::` rejected before any fetch), AT-009
(unauthorized remote rejected), `expected_remote_repository` overriding
server config, the §20.6 preconditions (interrupted merge, branch/HEAD
guards), and one test of a real connection to the real `github.com` host.

That last test surfaced a genuine, non-obvious interaction with S24: a
validated `https://github.com/...` origin is rewritten to SSH by S24's
`url."git@github.com:".insteadOf` (§19.3) — same as any other structured
operation — and GitHub requires an authenticated key for *any* SSH
operation, including reading a public repository anonymously. Without
S23 (SSH key provisioning, blocked by D3) there is therefore no way to
complete a real end-to-end fetch yet, which the first version of this
test discovered the hard way (it assumed anonymous HTTPS would bypass
SSH entirely — it does not, by design, once `insteadOf` is in effect).
The test now verifies the honest boundary instead: with GitHub's real,
freshly `ssh-keyscan`-obtained host key pinned into `known_hosts`, the
connection passes host verification (proving S24's mount and this tool's
fetch step are correctly wired together) and fails specifically at the
authentication stage — cleanly, without hanging — which is exactly the
expected shape of "the plumbing works; the credential doesn't exist yet."
A full success-path AT-028/AT-030 exercise against the real GitHub host
remains deferred to S23.

`schemas/tools.json` gained `git_pull`'s schema; `CONTRACT_VERSION`
bumped `0.5.0` → `0.6.0` (§33.2: new tool, minor).

### S27 — `git_push` and refspec construction ✅
**Spec:** §20.3, §20.5, §18.9, §20.6 · **Satisfies:** FR-022, FR-034 · **Verified by:** AT-011, AT-029, AT-031

- [x] Parameters `force`, `force_with_lease`, `delete`, `ref_type`, `no_verify`,
      all defaulting false/branch
- [x] Daemon **constructs** the refspec from validated parameters per §20.3's
      table — no client-supplied refspec
- [x] Refuse on the governing policy key **before** invoking Git
- [x] Reject invalid ref names, destinations outside `refs/heads/`/`refs/tags/`,
      and `force` + `force_with_lease` together (`INVALID_ARGUMENT`)
- [x] Require `expected_local_head_sha` when force is permitted and used
- [x] `--atomic --recurse-submodules=no`
- [x] AT-011: `force: true` → `POLICY_DENIED` before Git runs
- [x] AT-029: refspec construction correct; conflicting flags refused
- [x] AT-031: `git add`/`commit`/`fetch`/`push` all work from `exec`, with correct
      host ownership

**Commit:** `888e729`

New `src/dev_workspace_mcp/git/push.py` (pure refspec/argv construction,
mirroring `git/pull.py`'s split) plus a `git_push` tool in `tools/git.py`.
`build_refspec` implements §20.3's table literally — one line per row;
`force_with_lease` deliberately shares the *default* (unforced) refspec
shape, since its safety comes entirely from a separate CLI flag, not a
`+` prefix. `build_force_with_lease_arg` constructs
`--force-with-lease=<ref>:<sha>` from the locally-known remote-tracking
ref when one exists, falling back to bare `--force-with-lease` (Git's own
reflog-based safety check) when it doesn't — this daemon never invents an
"expected remote state" it doesn't actually have. `validate_branch_name`
delegates to real `git check-ref-format --branch` rather than a
hand-rolled regex — verified empirically to reject `..`, a leading `-`
(which `git push` would otherwise read as a flag), embedded spaces, and
empty input — plus one extra check of its own: a `branch` value that is
already `refs/`-qualified is rejected outright, since the daemon always
supplies the `refs/heads/`/`refs/tags/` prefix itself and a
self-qualified input would otherwise silently double up.

`git_push_tool`'s ordering, all before Git is ever invoked (AT-011):
input sanity (`force`+`force_with_lease` together, `delete` combined with
a meaningless `expected_local_head_sha`) → workspace/precondition checks
(interrupted operation, same as `git_commit`/`git_pull`) → branch-name
validation → §18.9 policy, evaluated in full — `push` (governs the whole
tool), `default_branch_push` (deny/require_approval block any push to
the default branch; `allow_fast_forward`, the actual default, additionally
blocks a *forced* push there specifically, even when `force_push` alone
would otherwise permit it — verified as its own, independent restriction,
not merely `force_push`'s), `force_push`/`force_with_lease`
(independently gate their respective flags), `delete_remote_branch`/
`delete_remote_tag` (gate `delete: true`, keyed by `ref_type`) — then,
only once policy has actually permitted the operation, the §20.3
requirement that `expected_local_head_sha` be supplied for a force push
(and, when supplied at all, checked as a `GIT_HEAD_CHANGED` guard against
the branch's actual local tip) → S25's `validate_remote_identity`,
still before any network touch → the in-container `git push --atomic
--recurse-submodules=no -c protocol.ext.allow=never`. Every
`RemoteWritePolicy` value of `require_approval` is treated as a hard
denial (§42: no interactive-approval mechanism exists in this release).
A plain (non-force, non-delete) push rejected by the remote for being
behind is classified as `GIT_NON_FAST_FORWARD` — verified against Git's
real rejection wording (`(fetch first)`, captured from an actual diverged
push between two clones of a bare repository) — rather than left as an
undifferentiated `COMMAND_FAILED`, matching AT-010's underlying "fails
without automatically forcing" guarantee for push specifically, not only
for `git_pull`'s own integration step (S26).

`tests/git/test_push.py` (25 tests) proves the refspec table, branch-name
validation, force-with-lease argument construction (both with and without
a known remote-tracking ref), the exact `git push` invocation shape, and
non-fast-forward detection — all against real local Git repositories, no
Docker needed, mirroring `git/pull.py`'s own test split.

`tests/workspace/test_git_push_tool.py` (16 tests, real Docker) covers
every policy key `git_push` consults (including the `default_branch_push:
allow_fast_forward`-blocks-force case against a real seeded default
branch, and `delete_remote_branch`'s default hard denial), the input
validations, AT-026/AT-009/`expected_remote_repository` remote-validation
gating (mirroring `test_git_pull_tool.py`), and — like S26's own real-
network test — a real connection attempt to the real `github.com` host:
with GitHub's real, freshly `ssh-keyscan`-obtained host key pinned, the
push passes host verification and fails specifically at the
authentication stage, cleanly, for lack of a key (S23, blocked by D3).

AT-031 is deliberately *not* exercised through the structured tool at all
— §20.2 is explicit that remote validation "does not bind `exec`" — so
it uses a real bare repository created *inside the container's own
filesystem* (`/tmp/remote.git`, outside the `/workspace` bind mount) via
`exec` itself, then a real `git add`/`commit`/`fetch`/`push` cycle, also
via `exec`, fully offline. This surfaced one real constraint worth
recording: `exec`'s own `cwd` parameter is restricted to `/workspace` or
beneath it (§14.2), so inspecting the bare remote's own log afterward
needed `git -C /tmp/remote.git log ...` rather than `exec`'s `cwd` — not
a bug, just a reminder that `cwd` and a plain `-C` argument answer
different questions. The resulting commit's ownership is checked from
the host side (`stat`), matching AT-031's "correct host ownership"
literally.

`schemas/tools.json` gained `git_push`'s schema; `CONTRACT_VERSION`
bumped `0.6.0` → `0.7.0` (§33.2: new tool, minor).

S23 (blocked by D3) and S28 (blocked by D2) remain blocked; S29 (Audit
logging) is the last unblocked Phase 3 task.

### S28 — Remote protection verification ✅
**Spec:** §18.5, §20.4 · **Satisfies:** FR-032 · **Verified by:** AT-035

- [x] Query rulesets and branch protection for the default branch on
      `workspace_open` using the read-only credential
- [x] Report `remote_protection` in `workspace_info` and the `git_push` response
- [x] `require_remote_protection`: `off` / `warn` (default) / `deny`
- [x] Under `deny`, do **not** provision a key when protection is missing →
      `REMOTE_PROTECTION_MISSING`
- [x] No credential → `unverified`, and `deny` degrades to `warn` with a reason
- [x] AT-035: warn succeeds with `missing`; deny fails and provisions no key

**Commit:** `8628814`

D2 was decided "no live GitHub credential for now" (see Open Decisions
above): this task is built and fully tested against the no-credential
(`unverified`) path plus a **real** stand-in for the credentialed path —
see below — rather than skipped.

New `src/dev_workspace_mcp/github_api.py` — `verify_remote_protection`,
a real GitHub REST API client (`urllib.request`, stdlib — one GET
request didn't justify a new HTTP dependency) against `GET
/repos/{owner}/{repo}/branches/{branch}/protection`, the classic branch-
protection endpoint (simpler than the newer Rulesets API for a first
implementation; `GithubConfig.api_base_url` — new, also independently
useful for GitHub Enterprise Server — makes the endpoint host swappable
either way). It degrades to `unverified` — never raises — for every
reason short of a genuine, credentialed "protection confirmed absent"
answer: no credential configured, no resolvable GitHub identity or
default branch, `api_auth: app` (not implemented — needs JWT signing and
an installation-token exchange, real work with no way to verify it
without a live GitHub App, so it degrades the same honest way an absent
credential does, rather than being half-built), or the query itself
failing for any reason. A diagnostic feature SHALL NOT be able to fail
`workspace_open` by itself — only `require_remote_protection: deny`'s
own enforcement can, and only for a *confirmed* `missing`, matching
§18.5's explicit "deny SHALL be treated as warn" rule for the
no-credential case exactly.

`tools/workspace.py`'s `workspace_open` verifies remote protection
**before any container work** — resolving `default_branch` via the
already-existing `detect_default_branch` (git/commit.py, S22) and
calling `verify_remote_protection`, entirely in-memory — so that `deny`
+ a confirmed `missing` result can raise `RemoteProtectionMissingError`
before `ensure_workspace` touches Docker at all: AT-035's "SHALL NOT
have provisioned a key into the container" holds because no container
is ever created, not merely because the ssh-agent step is skipped. The
verified result is persisted only *after* `ensure_workspace` succeeds,
into a new `repository.remote_protection` JSON column (`db/schema.py`
migration) — `ensure_workspace` is what upserts the `repository` row
this UPDATE targets, so persisting any earlier would hit the same
"row doesn't exist yet" ordering problem S23's `ssh_agent` table already
ran into. `workspace_info` reports this **cached** value rather than
re-querying GitHub on every read — §20.4 only requires verification on
`workspace_open` (optionally again before a push) — confirmed by a test
asserting a real fake-server's request count does not increase across
two `workspace_info` calls following one `workspace_open`.

The shared reporting shape (`RemoteProtectionReportOutput`,
`to_report_output`) lives in `github_api.py` itself and is imported by
both `tools/workspace.py` and `tools/git.py`, rather than each tool file
defining its own copy of the same §20.4 fields. `git_push`'s
`GitPushOutput` gained a `remote_protection` field reporting the same
cached value (§20.4 only says push MAY re-verify; there is no live
credential to meaningfully re-verify against yet, so re-querying was not
implemented — reporting the cached value is honest and cheap). This
field is wired and type-checked (confirmed by mypy and the schema
export) but **not exercised here via an actual successful structured
push** — no test in this task's suite, or S26/S27's, achieves a genuine
successful structured `git_push_tool` call, since that needs a real,
GitHub-installed credential that does not exist without S23's key
actually being registered on a real repository. This is the same
constraint S26/S27 already documented, not a new gap.

**A real stand-in, not a mock, for the credentialed path**:
`tests/test_github_api.py` (12 tests) and
`tests/workspace/test_remote_protection.py` (6 tests, real Docker) run
against a real local `http.server.HTTPServer` in a background thread —
a real HTTP round-trip, real JSON parsing, real header/auth checking —
standing in for `api.github.com`, the same substitution
`github_config.api_base_url` exists to support for GitHub Enterprise
Server for an unrelated reason, and the same pattern S24's AT-034 test
used (a real local `sshd` standing in for `github.com`'s). Covers: every
degrade-to-`unverified` reason, a real `404` → `missing`, a real
"protected" response → `verified` with `force_push_blocked`/
`deletion_blocked`/`requires_pull_request` correctly read off the
response body, `git_policy.remote_protection_checks` narrowing which
protections actually count toward `verified` (force push allowed but
only `deletion` is checked → still `verified`), a rejected token →
`unverified` with the HTTP status in the reason, an unreachable server →
`unverified`, AT-035's both halves (`warn` succeeds with `missing`;
`deny` fails with `REMOTE_PROTECTION_MISSING` and — confirmed by listing
containers with the workspace's own label — no container was ever
created), `deny` degrading to `warn` without a credential, and
`workspace_info`'s caching behavior.

`schemas/tools.json` regenerated: `workspace_open`/`workspace_info`'s
`remote_protection` field gained the full §20.4 shape (previously a bare
`status` string) and `git_push` gained the field entirely.
`CONTRACT_VERSION` bumped `0.7.0` → `0.7.1` (§33.2: patch — existing
tools' output fields changed/added, no new tool).

With S28 done, every task in Phase 3 that is not explicitly blocked is
now complete. Only S23's automated-provisioning alternative (not chosen —
D3 was decided manual) and D2's live-credential path (not chosen — no
token configured) remain unbuilt, both by deliberate decision, not by
being blocked.

### S29 — Audit logging ✅ (partial — see below)
**Spec:** §24, §20.7 · **Satisfies:** FR-027 · **Verified by:** AT-021, AT-037

- [x] Append-only structured records for the §24 event list (scoped subset — see below)
- [x] Structured Git records per §20.7 including `policy_decision`
- [ ] `ssh_auth` record per agent signature request, with fingerprint and
      best-effort `requesting_command` — the only visibility into `exec`-path
      remote operations — **blocked, see below**
- [x] Denials audited with the same fidelity as successes
- [x] Secret redaction (§24.1); never key material
- [x] AT-021 secret redacted · AT-037 blocked, see below

**Commit:** `b15106b`

A new `audit_denials(operation, *, audit_config)` decorator in
`tools/git.py` stacks directly below `@translate_workspace_errors` on
`git_commit_tool`/`git_pull_tool`/`git_push_tool` — it catches the raw
`WorkspaceError` (before that other decorator converts it to a
client-facing `ToolError`) and writes one audit record per failure, with
`workspace_id` read straight from the tool's own call kwargs. This
replaced a much larger option that was seriously considered and rejected:
hand-adding a `write_audit_event` call at every one of the ~25 individual
raise sites across the three tools. One decorator line per tool gives
full "denials audited with the same fidelity as successes" coverage
instead, and the same mechanism will cover any future raise site in
these functions with zero additional code. `policy_decision` is set to
`"deny"` specifically when the caught error's code is `POLICY_DENIED`,
and `"n/a"` for every other kind of denial (a remote-validation
rejection, a state guard, an interrupted operation) — distinguishing an
actual `git_policy` key's decision from any other failure, rather than
overclaiming "policy" for all of them. Every *success* path audit call
for the three tools also gained an explicit `"policy_decision": "allow"`
field, matching §20.7's example shape exactly (which already includes
`policy_decision` even on success).

`tools/workspace.py`'s `workspace_open` gained two audit calls:
`workspace_opened` on success (§24's own event name, literally), and
`repository_authorization_failure` when `registry.get(repository_id)`
raises `RepositoryNotAllowedError` — also named directly from §24's
event list.

**Deliberately scoped down, and worth being explicit about:** §24's event
list is a "SHOULD"/"Recommended events" list, not an exhaustive mandate,
and this task wires the subset that is security-relevant, directly named
in §24, and reachable through modules that already had `audit_config` in
scope (`workspace.py`, `git.py`). `file_written`/`file_deleted`
(filesystem tools), `command_started`/`command_canceled` (execution
tools), and `container_created`/`rebuilt`/`deleted` remain unwired: the
first two would need `audit_config` newly threaded through
`filesystem_tools.register()`/`execution_tools.register()` and
`server.py`'s wiring (neither receives it today), and the container
events don't correspond to any distinguishable signal `ensure_workspace`
currently returns (nor, for rebuilt/deleted, to any tool that exists yet
— `workspace_rebuild`/`workspace_reset`/`workspace_delete` are §34.3-
deferred). None of this is required by AT-021 or AT-037, the two ATs
this task is actually verified against, so it was left as a clearly-
documented follow-up rather than folded into an already-large task.

**`ssh_auth` (and therefore AT-037) is genuinely blocked, not merely
deferred by choice.** §20.7 is explicit that `ssh_auth` is emitted "under
`ssh.mode: agent`... for every signature request the agent serves" — the
event's entire mechanism is hooking into the per-workspace `ssh-agent`
process S23 builds (SSH key provisioning, blocked by D3). There is no
agent to intercept signature requests from yet, so there is nothing to
build here that would not be pure guesswork about S23's eventual
interface. This is the one bullet in this task that is not "scoped down"
but actually blocked, tracked the same way S25/S26/S27 tracked their own
S23-dependent halves.

`tests/workspace/test_audit_logging.py` (6 tests, real Docker) proves:
`workspace_opened` on success, `repository_authorization_failure` on an
unknown `repository_id`, a `git_push` policy denial audited with
`policy_decision: "deny"`, a `git_pull` remote-validation rejection
audited with `policy_decision: "n/a"` (proving the two are genuinely
distinguished, not both bucketed as "policy"), a `git_commit` success
audited with `policy_decision: "allow"`, and an interrupted-operation
precondition failure audited as a plain failure — proving `audit_denials`
catches every `WorkspaceError`, not only `PolicyDeniedError`. AT-021's
redaction mechanism is not re-tested per call site here: every audit call
added in this task routes through the same `write_audit_event`, already
proven exhaustively by `tests/test_audit.py` (S17); there is no natural,
realistic path for a secret to appear in this task's own audit metadata
(branch names, shas, remote identities, policy decisions, error codes).

No new tools or output fields, so `schemas/tools.json` needed no
regeneration (confirmed by re-running `export-schemas` and diffing — no
drift).

This completes every currently-unblocked Phase 3 task. S23 (blocked by
D3) and S28 (blocked by D2) — and `ssh_auth`/AT-037 within this task —
remain blocked pending those credential decisions.

---

## Phase 5 — Environment Configuration

### S30 — Caches, project config, images ✅ (partial — see below)
**Spec:** §9, §10, §11, §16.2, §30 · **Verified by:** AT-004

- [x] Named cache volumes under `$HOME`, owned by the container user
- [x] `.mcp-workspace.yml` — versioned, unknown mandatory fields are errors;
      `setup` steps run as the container user and need `sudo` for OS packages
- [x] Image precedence per §9 (partial — see below); record base image, digest,
      config version
- [ ] Dev Container subset per §30 — **not implemented, see below**
- [x] AT-004: install a real package, reconnect, tool still present (substituted
      package — see below)

**Commit:** `6bb2c80`

**Scope, stated up front**: §9's image precedence has four levels
(explicit MCP config → devcontainer.json → project Dockerfile →
configured default). This task implements the first and the last —
`.mcp-workspace.yml`'s `image` field, falling back to
`docker.default_image` — and deliberately does not implement
`.devcontainer/devcontainer.json` parsing (§30) or per-project
Dockerfile detection (§9's precedence item 3). §30 itself already
allows this ("MAY initially support a subset"); a second config format
on top of `.mcp-workspace.yml` was judged substantial enough to defer
as its own follow-up rather than fold into an already-large task —
tracked here as a deliberate cut, not silent approximation of a
security-sensitive setting (§30's own "SHALL not silently approximate"
is about behavior, and there is none to approximate: no devcontainer.json
support means devcontainer.json is simply not read at all yet).

New `src/dev_workspace_mcp/project_config.py` — `load_project_config`,
a real YAML parser + pydantic model (`extra: "forbid"`, matching every
other config model in this codebase) for `.mcp-workspace.yml`/`.yaml`,
covering §10's full documented shape: `version`, `image`, `workspace`
(parsed and validated, not yet wired to anything — the same
"accepted, not acted on" status `docker.workspace_path`, a pre-existing
config field, already has; both need `tools/workspace.py`'s hardcoded
`DEFAULT_WORKSPACE_PATH` replaced with a real override, separate work
this task does not do), `setup`, `environment`, `caches`, `github`, and
`validation` (parsed for forward-compatibility and unknown-field
rejection; no tool runs these commands yet — there is no `validate`
tool in §34.1). A malformed file fails `workspace_open` closed
(`ContainerCreateFailedError` — reusing the closed 24-exception
taxonomy `errors.py`'s own docstring insists on, rather than inventing
a 25th code the spec's own §31 list doesn't name), matching "Unknown
mandatory fields SHOULD cause a validation error."

New `src/dev_workspace_mcp/workspace/caches.py` — named Docker volumes
for `caches[]` entries, repository-scoped (`mcp-cache-<repository_id>-
<cache_name>`): every workspace opened for a repository shares its
caches, but two repositories never share one. §11's own `workspace` /
`repository` / `shared` scope levels are not implemented as a
configurable knob — repository-scoped is the only level this task
builds, a deliberate simplification favoring the safer end of "the
default SHOULD favor isolation," not the full range §11 describes.
`caches[].path` (`$HOME/.cargo`, `~/.cargo`, or bare `.cargo` — all
three documented/common forms) is validated as genuinely home-relative,
rejecting an absolute path elsewhere or `..` traversal, since it names a
Docker mount *target* inside the container. Docker creates a fresh named
volume root-owned regardless of what mounts onto it, so `entrypoint.sh`
gained `MCP_CACHE_PATHS` (colon-separated, `$HOME`-relative) and chowns
each one to the container uid — the same non-recursive pattern already
used for `$HOME` itself, for the same reason (repairing ownership of
pre-existing content from a *previous* uid is a known, accepted, and
already-precedented simplification, not new to this task).

`.mcp-workspace.yml`'s `setup` steps run through the same execution
service `exec`/the structured Git tools already use (§20.1's "not a
second execution path" rationale, again) — `["bash", "-c", step]`, so
`sudo` works exactly as it does from `exec` — but **only when
`workspace_open` genuinely just created the container**, never on a
reopen of an already-running or stopped-then-restarted persistent
workspace. Determining "was this a real creation" turned out to need a
real fix: the first version compared the *returned* record's
`created_at`/`last_used_at` for equality, reasoning that only `_create`
sets both to the same timestamp — true at the moment of insertion, but
`ensure_workspace`'s own success path (`_run_container`) unconditionally
advances `last_used_at` again once the container actually starts, *even
for a brand-new workspace* — so the equality check the returned record
offered was never true by the time `workspace_open` could observe it,
and this task's own tests caught it immediately (`setup` silently never
ran, and a deliberately failing step never raised). Fixed the same way
S23 solved an analogous ordering problem for its ssh-agent socket:
`persistent_workspace_id(repository.id)` is a pure, deterministic
function of the repository id, the exact same id `ensure_workspace`
computes internally for `mode: "persistent"` — so a plain `SELECT 1
FROM workspace WHERE id = ?` *before* calling `ensure_workspace`
reliably answers "does this workspace already exist" without needing
`ensure_workspace`'s own return contract changed (which ~30 existing
test call sites unpack positionally). `mode: "ephemeral"` is always
treated as a creation, matching `ensure_workspace`'s own "there is no
'the' ephemeral workspace to reuse."

Image provenance (§9.2): `workspace.image_digest`/`config_version`
columns (`db/schema.py` migration; `image` and `created_at` already
existed), populated via a real `docker_client.images.get(image).id`
call right after `ensure_workspace` succeeds, reported in both
`workspace_open` and `workspace_info`'s output (`WorkspaceInfoOutput`
gained `image`, `image_digest`, `config_version`, `created_at`).
Also corrected two stale `server_info` capability flags found while
touching the adjacent dict: `git.commit.expected_head_sha` and
`git.push.remote_protection` were both still `False` despite S22/S28
having implemented them — simple, low-risk factual corrections to
already-completed work, not new scope.

`tests/test_project_config.py` (10 tests) covers `.mcp-workspace.yml`
parsing in isolation — the full documented example, unknown-field/
missing-required-field/invalid-YAML/non-mapping rejection, both file
extensions, precedence between them. `tests/workspace/test_caches.py`
(14 tests) covers path normalization for every documented form,
rejection of unsafe paths, repository-scoped volume naming, and mount
shape — no Docker needed for either file. `tests/workspace/
test_project_config_integration.py` (11 tests, real Docker, real S7
image) covers image precedence, environment propagation, setup steps
running once on real creation and never on a real reopen (the bug
above, caught here), a real failing step raising `COMMAND_FAILED`,
cache volumes actually writable and owned by the container uid (checked
via a real mount-point assertion, not merely a directory under `$HOME`),
persistence across a real stop/start cycle, two repositories never
sharing a cache, image provenance end-to-end, and AT-004.

**AT-004 uses a substituted real package**, the same principle S24's
AT-034 used a generic local `sshd` instead of `github.com`'s: the spec
names `cargo-nextest`, which needs a Rust toolchain the S7 default image
correctly does not carry (§9.1: "SHOULD NOT attempt to contain every
language toolchain"); a small, always-available Debian package (`tree`)
exercises the identical mechanism — install after creation, stop,
reopen via a fresh `create_server` call (simulating "restart the MCP
session"), confirm the tool survives — without needing a slow, flaky
Rust bootstrap in every CI run.

A real leak was found and fixed in the test suite itself (not product
code, and not a repeat of S23's ssh-agent leak, a different resource
this time): named cache volumes have no label of their own — created
implicitly via a container's mount reference, never through an explicit
`client.volumes.create(...)` call this daemon makes — so the shared
`docker_client` conftest fixture, which already force-removes
label-matched containers on teardown, left cache volumes behind after
every run. Fixed by filtering `client.volumes.list` on
`cache_volume_name`'s own stable `mcp-cache-` prefix instead of a label,
confirmed empirically that Docker's `name` volume filter does substring
matching (not exact), and verified a full test run leaves zero volumes,
zero containers, and zero `ssh-agent` processes behind.

`schemas/tools.json` regenerated: `workspace_open`/`workspace_info`
gained `image`, `image_digest`, `config_version`, `created_at`.
`CONTRACT_VERSION` bumped `0.7.1` → `0.7.2` (§33.2: patch — existing
tools' output fields changed/added, no new tool).

---

## Phase 6 — Hardening

### S31 — Resource and disk controls ✅ (partial — see below)
**Spec:** §26, §27 · **Verified by:** AT-005

- [x] `cpus` / `memory` / `pids_limit`; detect and report OOM (partial — see
      below), disk exhaustion (not implemented — see below), pid exhaustion
- [ ] Disk usage reporting and prune operations — **not implemented, see below**
- [x] AT-005: operator-path reset leaves the repository intact, clears
      container-local state

**Commit:** `bc9b0f1`

New `ResourcesConfig` (`resources:` block — `cpus`, `memory`,
`pids_limit`, every field `None`/unset by default, matching Docker's own
unbounded default rather than imposing an opinionated daemon-side
ceiling) wired into `build_workspace_run_kwargs` → `docker.containers.run`
(`nano_cpus`, `mem_limit`, `pids_limit` — the real docker-py kwarg names,
confirmed by reading docker-py's own source before trusting them, not
guessed). Verified against the container's real `HostConfig` (`docker
inspect`) for both the configured and the default-unset case, and
`pids_limit` additionally against genuine kernel enforcement: a real
fork-bomb-style command against a real, low `pids_limit` fails with the
real kernel's own `"Resource temporarily unavailable"` — verified
empirically against a plain `alpine` container first, before trusting
that the same behavior holds through this daemon's own wiring.

**OOM detection is only partial** — the underlying classification logic
(`_state_from_docker_container`'s `OOMKilled` → `WorkspaceState.FAILED`)
already existed from S18, tested there only against fake container
objects, deliberately: S18's own test file already judged a real forced
OOM kill "slow and flaky across kernels/cgroup configs" and not worth
reproducing. This task respects that same judgment rather than
overriding it — it adds real `mem_limit` wiring (so an operator's
`resources.memory` setting genuinely reaches Docker, confirmed via
inspect) but does not add a new real forced-OOM integration test.

**Disk exhaustion detection and disk-usage/prune tooling are not
implemented.** §34.3 (`docs/TODO.md`'s own MVP tool-surface deferral
list) already excludes `workspace_disk_usage`/`cache_prune`/
`workspace_prune` from the MVP's registered tools entirely — there is no
tool to build reporting *for* in this phase. A CLI-only disk-usage
operator command would still have been in scope, but was judged, along
with a distinct disk-exhaustion signal (Docker has no built-in state for
it the way it does for OOM; it would surface as an ordinary nonzero exit
code from whatever write attempt hit `ENOSPC`, already visible through
`exec`'s existing error reporting, just not classified separately),
substantial enough to defer as its own follow-up rather than fold into
an already-large task.

§8.4's operator path (`reset`/`rebuild`/`delete` — "None is exposed as
an MCP tool in the MVP... reached as operator actions — a CLI
subcommand on the host") is now real: three new `dev-workspace-mcp`
subcommands (`reset`, `rebuild`, `delete`), thin wrappers over
`DockerWorkspaceManager`'s own `reset`/`rebuild`/`delete` (already
implemented since S6/S18 — `rebuild` and `reset` are, as of this task,
still identical underlying recreations, a pre-existing characteristic
neither this task nor its predecessors changed). Each resolves
`--repository <id>` to the deterministic `persistent_workspace_id`
(the same trick S23's ssh-agent socket and S30's setup-step "was this
created" check both already rely on) and fails with a clear message,
not a traceback, for an unknown repository or a workspace never opened.

`reset`/`rebuild` reconstruct run_kwargs via the same pure, already-
tested building blocks `workspace_open` itself uses
(`build_workspace_run_kwargs`, `build_workspace_mounts`,
`build_known_hosts_mount`), including `.mcp-workspace.yml`'s `image`
when the repository has one (§9/§10, S30) and the new resource limits —
but **deliberately does not** reattach an `ssh.mode: agent` socket or
named cache-volume mounts. Those depend on state (a live agent process,
a specific cache volume name) tied to `workspace_open`'s own richer
provisioning flow (§19.2/§11, S23/S30); duplicating that machinery in a
standalone CLI command was judged out of scope for this task, and — a
real constraint worth being explicit about — cannot be retrofitted onto
an already-reset container afterward via a plain `workspace_open` call
either, since a container's mount list is fixed at creation and
`workspace_open` only recreates a container for a repository that has
never had one, not one that already exists but merely lacks a mount.

`tests/workspace/test_resources.py` (3 tests, real Docker) covers the
default-unset case, all three limits applied and inspectable, and real
`pids_limit` kernel enforcement. `tests/workspace/test_cli_lifecycle.py`
(6 tests, real Docker) covers AT-005 itself (a real host-side repository
write survives; a real container-local marker file does not), `reset`
honoring `.mcp-workspace.yml`'s `image`, clear failures for an unopened
workspace and an unknown repository, `rebuild`, and `delete` (repository
preserved, the workspace genuinely gone from `workspace_info`
afterward).

No new tools or output fields — `resources:` only affects internal
container-creation kwargs, and `reset`/`rebuild`/`delete` are CLI
subcommands, not MCP tools — so `schemas/tools.json` needed no
regeneration (confirmed by re-running `export-schemas` and diffing — no
drift).

### S32 — Advisory locks — **deferred by spec, not attempted**
**Spec:** §22.1, §22.2, §20.6, §67 · **Satisfies:** FR-029

- [ ] `workspace_lock` / `workspace_unlock` / `workspace_lock_status` with leases
- [ ] Wire into the §20.6 precondition that is a no-op until this ships
- [ ] Report the active lock in `workspace_info`

**This task directly conflicts with `docs/SPEC.md` — the source of truth
this file's own header names — and the conflict resolves in the spec's
favor, not the checklist above's.** §34.3 ("Deferred — later phases")
explicitly lists `workspace_lock`, `workspace_unlock`, and
`workspace_lock_status` among the tools *not* part of the MVP's
registered surface, with its own explicit rule for when that changes:
"Each SHALL be added only when a concrete workflow needs it, and §34.1
SHALL be updated when it is." §20.6 says the same thing again, even more
directly, about the exact precondition this task's second bullet asks to
wire up: "the workspace advisory lock (§22.1), once locks exist — they
are deferred from the MVP (§34.3), and **until then this precondition is
a no-op**" (emphasis in the spec's own intent, not added here). Building
these three tools now would mean adding MVP-surface tools the spec
itself says should wait for a concrete workflow need — one hasn't been
identified — while also being inconsistent with how this same ralph loop
already treated the *other* §34.3-deferred tools (`workspace_disk_usage`
et al., S31) by leaving them unbuilt rather than pretending §34.3 didn't
apply to them.

No code was written for this task. `src/dev_workspace_mcp/db/schema.py`'s
own module docstring already anticipated this ("`WorkspaceLock` is
deferred to S31 with locking itself" — a stale task-number reference
from before a since-corrected renumbering, the same kind of drift the
Open Decisions section's D3 note already had to correct once; the
number is wrong, the deferral itself was and remains correct). This
entry is intentionally left unchecked, distinct from S30/S31's
"partial" tasks (which shipped real, tested code alongside a documented
scope cut) — nothing here was implemented, because the spec says not to.

### S33 — Security test suite ✅
**Spec:** §70, §39 · **Verified by:** all ATs
**Commit:** `a1bdf8f`

- [x] Unit: path authorization, remote normalization, policy decisions, refspec
      construction, state transitions, redaction, config validation
- [x] Integration on disposable repos and a disposable remote: filesystem
      mutation, package install, job lifecycle, recovery, commit, push, conflicts,
      symlink escape, cache persistence
- [x] Security: traversal, symlink escape, malicious remote URL, secret leakage,
      Docker socket, host mounts, policy enforcement
- [x] End-to-end MCP harness per §70 running the §78 workflow
- [x] Every AT-001..AT-037 mapped to an automated test

This task was an audit of the 650+ tests already built across every prior
task, not a greenfield build — the work was finding and closing the real
gaps, not re-proving what was already proven.

**AT coverage audit.** A first `grep -rn "AT-0[0-9][0-9]"` pass
(case-sensitive, hyphen-only) under-reported: several tests cite their AT
in a Python function name using the forced lowercase-underscore
convention (`test_at_011_...`), which that pattern doesn't match. Redone
case-insensitively over both `AT-XXX` and `AT_XXX`, the real gap list
narrowed from five numbers to three: **AT-008**, **AT-010**, **AT-019**.
The self-verifying `tests/test_acceptance_test_coverage.py` added by this
task encodes that same lesson permanently — its citation regex uses a
`(?!\d)` lookahead rather than `\b`, because `\b` never matches between a
digit and the trailing `_` of an identifier like `at_011_...` (both are
word characters, so there's no boundary there); a first draft using `\b`
reintroduced the exact false-negative the manual grep had just been
corrected for, caught immediately by the audit test's own would-be
false-positive failure (`AT-[11, 13]` reported missing when they weren't).

- **AT-008** (Git State Guard) had real mechanics coverage (`git_commit`'s
  `expected_head_sha` guard) but no test for the literal scenario: HEAD
  read, then a commit lands *externally* — straight into the shared bind
  mount, the same way an operator or another process could, entirely
  outside the MCP session — before the guarded call. Added
  `test_at_008_a_commit_landed_externally_after_head_was_read_fails_the_guard`
  to `test_git_commit_tool.py`: reads HEAD via `git_status`, runs a real
  `git commit` directly against the host checkout, then asserts the
  now-stale `expected_head_sha` fails closed with `GIT_HEAD_CHANGED`.
- **AT-019** (Dirty Tree Protection) exposed a genuine, previously-missing
  precondition, not just a test gap: §22.3 requires potentially-destructive
  operations to fail on unrelated uncommitted changes unless authorized,
  but `git_pull_tool` never checked working-tree cleanliness before a
  `merge`/`rebase` strategy integration. `ff_only` is deliberately exempt
  — a real fast-forward only ever moves the branch tip forward and
  preserves non-conflicting uncommitted changes, the same guarantee
  `git merge --ff-only` itself gives; `merge`/`rebase` genuinely rewrite
  the history the working tree sits on, which is what the precondition
  exists for. Added the check to `git_pull_tool` (raises `GitDirtyError`
  for `merge`/`rebase` against a dirty tree, reusing S22's existing
  `GitDirtyError`/`GIT_DIRTY`) plus two new tests in
  `test_git_pull_tool.py`: one proving a dirty tree blocks a `merge`
  pull, one proving `ff_only` is unaffected by the same dirty tree (it
  reaches — and fails at — remote validation instead, proving the dirty
  check itself never fired).
- **AT-010** (Non-Fast-Forward Push) already had real git-mechanics
  coverage (`tests/git/test_pull.py`'s and `tests/git/test_push.py`'s
  diverged-history tests) but no citation and no structured-tool-level
  test. Citations were added to both. Full structured-tool-level AT-010
  coverage (driving non-fast-forward rejection through the actual
  `git_pull`/`git_push` MCP tools against a real `github.com` remote)
  remains **blocked pending S23/D3** — the same class of gap already
  documented for AT-027/AT-028's second halves: `validate_remote_identity`
  (S25) accepts only `github.com` as a structured-tool remote, and S24's
  `insteadOf` rewrite forces even an HTTPS-authored `github.com` origin
  through SSH, which needs a real provisioned key nobody has registered.

**Category audit** (the TODO's own unit/integration/security bullets):
each category was checked for existing coverage by name — path
authorization/traversal/symlink escape
(`filesystem/test_security.py`, `test_repositories.py`,
`workspace/test_caches.py`), remote normalization
(`git/test_remote.py`), policy decisions (`config/test_models.py`,
`git/test_remote.py`, `test_github_api.py`, several `workspace/test_*`
files), refspec construction (`git/test_push.py`), state transitions
(`workspace/test_manager.py`, `test_idle_stop.py`, `test_recovery.py`),
redaction (`test_audit.py`, `test_errors.py`,
`workspace/test_audit_logging.py`), config validation
(`config/test_loader.py`, `config/test_models.py`, `test_project_config.py`),
malicious remote URL (`git/test_service.py`,
`workspace/test_git_pull_tool.py`, `workspace/test_git_push_tool.py`),
Docker socket (`workspace/test_mounts_integration.py`'s AT-012), secret
leakage (`filesystem/test_security.py`, `test_audit.py`). Every category
already had real, substantive coverage from its originating task — this
audit found no genuinely uncovered category, only the three AT gaps
above.

**New: `tests/test_acceptance_test_coverage.py`.** A self-verifying test
parsing every `### AT-NNN` heading out of `docs/SPEC.md` directly (not a
hardcoded list, so it can't drift from the spec) and asserting each
number is cited, case-insensitively, in both `AT-XXX` and `AT_XXX` form,
by at least one file under `tests/`. Also asserts the spec defines
exactly 37 ATs, as a sanity check on the parser itself. This makes a
future spec edit that adds, removes, or renumbers an AT fail loudly here
instead of silently drifting out of sync with the suite — it does not
judge whether a citing test's assertions are adequate, only that one
exists.

**New: `tests/workspace/test_e2e_workflow.py`.** One continuous test
driving every §78 "definition of done" workflow step reachable without a
live GitHub credential, through the real MCP tool surface against a real
Docker daemon: connect → list/select repository → open persistent
workspace → inspect Git state → inspect/search files → modify a file →
install a dev tool via `sudo apt-get` → run a linter-equivalent → run a
real unit test against the edit → start and monitor an async build via
`exec_start`/`exec_status`/`exec_output` → review the `git_diff` → commit
with an `expected_head_sha` guard → reconnect with a fresh
`create_server` (a new MCP session, same state db and repository) and
confirm the commit is visible. §78 steps 5 and 15 (pull/push against the
real `github.com` remote) are the two genuinely unreachable steps for the
same S23/D3 reason as AT-010/AT-027/AT-028; §78 step 17 (no host Docker
socket/paths/credentials reachable) is asserted per-mechanism elsewhere
(AT-012/AT-013/AT-016) rather than re-derived here. Two real response-shape
bugs in my own first draft were caught immediately by running it against
the real tool layer rather than trusting the schema from memory:
`exec_output`'s field is `content`, not `stdout_tail`; `git_diff`'s field
is `content`, not `diff`; `git_commit`'s field is `commit_sha`, not
`head_sha`.

**Verification performed, not assumed:** full suite (`uv run pytest -q`)
— 656 passed, 0 leftover Docker containers/volumes/ssh-agent processes
afterward; `ruff format .` / `ruff check .` / `mypy src` all clean;
`export-schemas` re-run against a fresh config and diffed byte-for-byte
against the checked-in `schemas/tools.json` — no drift (expected: S33
adds no new tools or output fields).

---

## Phase 7 — Deferred

Not scheduled. From §59 and §75: device forwarding, GPU, port forwarding,
multi-user auth, Kubernetes, Docker-in-Docker, worktree isolation (§76),
`ssh.scope: shared`, per-signature agent approval, structured submodule support,
interactive approval for `require_approval`.

---

## Definition of Done

§78's seventeen-step workflow runs end to end against a real authorized
repository, with no host credential and no cross-repository reach from the
workspace key.
