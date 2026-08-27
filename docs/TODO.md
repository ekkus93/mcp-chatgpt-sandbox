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
- [ ] **D2 — Read-only GitHub API token.** §19.5. Optional but load-bearing:
      without it `remote_protection` is `unverified` and `require_remote_protection`
      degrades to `warn` (§18.5). Blocks S28 (`**Blocked by:** D2` there) from being
      fully testable.
- [ ] **D3 — Deploy-key provisioning is manual or automated.** §19.1 requires a
      per-repository deploy key with write access. The MVP may require the operator
      to create and register it by hand; automating it needs an API credential with
      `administration: write`, which D2's token deliberately lacks. Blocks S23
      (`**Blocked by:** D3` there). *(Corrected 2026-08-26: this note previously said
      "Blocks S21"/"Blocks S26" — stale against a since-reordered task numbering;
      the per-task `**Blocked by:**` annotations on S23/S28 were already correct.)*

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

### S23 — SSH key provisioning (agent mode)
**Spec:** §19.1, §19.2, §19.4 · **Satisfies:** FR-025, FR-037 · **Verified by:** AT-013, AT-036 · **Blocked by:** D3

- [ ] Per-workspace `ssh-agent`; load the workspace key; bind-mount the socket at
      `/run/mcp/ssh/agent.sock`; set `SSH_AUTH_SOCK`
- [ ] Key material never enters the container filesystem, env, or argv
- [ ] Remove the key from the agent whenever the workspace stops (including idle
      stop); terminate the agent on delete
- [ ] `file` mode as the documented weaker alternative, reported via `ssh_key_mode`
- [ ] Agent reconciliation on daemon restart (§44.1 step 4) — without it a running
      container's key is silently unusable
- [ ] AT-013: no host credentials present; no key material discoverable under agent
      mode, including during an in-flight push
- [ ] AT-036: key unloaded after stop; no agent after delete

### S24 — SSH client configuration and host-key pinning
**Spec:** §19.3 · **Satisfies:** FR-033 · **Verified by:** AT-034

- [ ] Container-local `$HOME/.ssh/config` — `IdentitiesOnly`, `StrictHostKeyChecking yes`,
      pinned `UserKnownHostsFile`
- [ ] Pinned GitHub host keys; no trust-on-first-use; never honour a `known_hosts`
      or SSH config from inside the repository
- [ ] Optional `url.<base>.insteadOf` so an HTTPS `origin` still uses the key
- [ ] AT-034: substituted host key → mismatch failure, no prompt

### S25 — Remote identity validation
**Spec:** §20.2, §7.3 · **Satisfies:** FR-023 · **Verified by:** AT-009, AT-026, AT-027, AT-028, AT-030

- [ ] Resolve remote → URL; scheme allowlist; reject `ext::`, `file://`, and any
      local-command transport, with `-c protocol.ext.allow=never`
- [ ] Normalize to `owner/repo` (§7.3) and compare against configured identity and
      `github.repositories`
- [ ] Honour `expected_remote_repository` regardless of server config
- [ ] Reject client-supplied `--upload-pack` / `--receive-pack` / `--exec`
- [ ] AT-009 unauthorized remote · AT-026 `ext::` rejected · AT-027 key scope stops
      both paths · AT-028 SSH-form `origin` works · AT-030 submodule URL not fetched

### S26 — `git_pull`
**Spec:** §20.5, §18.8 · **Satisfies:** FR-021 · **Verified by:** AT-010

- [ ] Fetch then integrate per `pull_strategy`, default `ff_only`
- [ ] `--no-recurse-submodules` on the fetch step
- [ ] Non-fast-forward under `ff_only` → `GIT_NON_FAST_FORWARD`, never escalate
- [ ] AT-010: push against a diverged remote fails without forcing

### S27 — `git_push` and refspec construction
**Spec:** §20.3, §20.5, §18.9, §20.6 · **Satisfies:** FR-022, FR-034 · **Verified by:** AT-011, AT-029, AT-031

- [ ] Parameters `force`, `force_with_lease`, `delete`, `ref_type`, `no_verify`,
      all defaulting false/branch
- [ ] Daemon **constructs** the refspec from validated parameters per §20.3's
      table — no client-supplied refspec
- [ ] Refuse on the governing policy key **before** invoking Git
- [ ] Reject invalid ref names, destinations outside `refs/heads/`/`refs/tags/`,
      and `force` + `force_with_lease` together (`INVALID_ARGUMENT`)
- [ ] Require `expected_local_head_sha` when force is permitted and used
- [ ] `--atomic --recurse-submodules=no`
- [ ] AT-011: `force: true` → `POLICY_DENIED` before Git runs
- [ ] AT-029: refspec construction correct; conflicting flags refused
- [ ] AT-031: `git add`/`commit`/`fetch`/`push` all work from `exec`, with correct
      host ownership

### S28 — Remote protection verification
**Spec:** §18.5, §20.4 · **Satisfies:** FR-032 · **Verified by:** AT-035 · **Blocked by:** D2

- [ ] Query rulesets and branch protection for the default branch on
      `workspace_open` using the read-only credential
- [ ] Report `remote_protection` in `workspace_info` and the `git_push` response
- [ ] `require_remote_protection`: `off` / `warn` (default) / `deny`
- [ ] Under `deny`, do **not** provision a key when protection is missing →
      `REMOTE_PROTECTION_MISSING`
- [ ] No credential → `unverified`, and `deny` degrades to `warn` with a reason
- [ ] AT-035: warn succeeds with `missing`; deny fails and provisions no key

### S29 — Audit logging
**Spec:** §24, §20.7 · **Satisfies:** FR-027 · **Verified by:** AT-021, AT-037

- [ ] Append-only structured records for the §24 event list
- [ ] Structured Git records per §20.7 including `policy_decision`
- [ ] `ssh_auth` record per agent signature request, with fingerprint and
      best-effort `requesting_command` — the only visibility into `exec`-path
      remote operations
- [ ] Denials audited with the same fidelity as successes
- [ ] Secret redaction (§24.1); never key material
- [ ] AT-021 secret redacted · AT-037 `exec("git fetch")` produces `ssh_auth`

---

## Phase 5 — Environment Configuration

### S30 — Caches, project config, images
**Spec:** §9, §10, §11, §16.2, §30 · **Verified by:** AT-004

- [ ] Named cache volumes under `$HOME`, owned by the container user
- [ ] `.mcp-workspace.yml` — versioned, unknown mandatory fields are errors;
      `setup` steps run as the container user and need `sudo` for OS packages
- [ ] Image precedence per §9; record base image, digest, config version
- [ ] Dev Container subset per §30; unsupported properties reported, never silently
      approximated
- [ ] AT-004: install `cargo-nextest`, reconnect, tool still present

---

## Phase 6 — Hardening

### S31 — Resource and disk controls
**Spec:** §26, §27 · **Verified by:** AT-005

- [ ] `cpus` / `memory` / `pids_limit`; detect and report OOM, disk exhaustion,
      pid exhaustion
- [ ] Disk usage reporting and prune operations; never delete repository files
- [ ] AT-005: operator-path reset leaves the repository intact, clears
      container-local state

### S32 — Advisory locks
**Spec:** §22.1, §22.2, §20.6, §67 · **Satisfies:** FR-029

- [ ] `workspace_lock` / `workspace_unlock` / `workspace_lock_status` with leases
- [ ] Wire into the §20.6 precondition that is a no-op until this ships
- [ ] Report the active lock in `workspace_info`

### S33 — Security test suite
**Spec:** §70, §39 · **Verified by:** all ATs

- [ ] Unit: path authorization, remote normalization, policy decisions, refspec
      construction, state transitions, redaction, config validation
- [ ] Integration on disposable repos and a disposable remote: filesystem
      mutation, package install, job lifecycle, recovery, commit, push, conflicts,
      symlink escape, cache persistence
- [ ] Security: traversal, symlink escape, malicious remote URL, secret leakage,
      Docker socket, host mounts, policy enforcement
- [ ] End-to-end MCP harness per §70 running the §78 workflow
- [ ] Every AT-001..AT-037 mapped to an automated test

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
