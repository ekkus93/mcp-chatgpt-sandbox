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
      degrades to `warn` (§18.5). Blocks S26 from being fully testable.
- [ ] **D3 — Deploy-key provisioning is manual or automated.** §19.1 requires a
      per-repository deploy key with write access. The MVP may require the operator
      to create and register it by hand; automating it needs an API credential with
      `administration: write`, which D2's token deliberately lacks. Blocks S21.

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
      (steps 1-2 and 7 implemented; steps 3-6 land with S3/S6/S23)

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

### S9 — Filesystem service (host-side)
**Spec:** §6.3, §12, §12.1, §48, §49 · **Satisfies:** FR-011, FR-031 · **Verified by:** AT-001, AT-002, AT-033

- [ ] Runs **host-side in the daemon** (§6.3) — no container required for file ops
- [ ] Path validation per §12: normalize, resolve `.`/`..`, canonicalize, confirm
      containment, reject symlink escapes
- [ ] Git-metadata exclusion per §12.1, all three rules: `.git` at root, a `.git`
      component at any depth, and the gitdir target of a gitfile →
      `PATH_GIT_METADATA`
- [ ] Exclude `.git` from `list_files` / `search_files` output by default
- [ ] AT-001: `read_file("../../etc/passwd")` → `PATH_OUTSIDE_REPOSITORY`
- [ ] AT-002: escaping symlink refused by the tools; dangling in the container
- [ ] AT-033: `write_file`/`patch_file`/`delete_file` into `.git` → `PATH_GIT_METADATA`

### S10 — Filesystem tools
**Spec:** §13, §12.2, §46, §47, §48 · **Satisfies:** FR-007, FR-008, FR-009, FR-010 · **Verified by:** AT-003, AT-018

- [ ] `list_files` — relative path, type, size, mtime, symlink status, hash
- [ ] `read_file` — byte/line range, max response size, truncation flag, `sha256`
      in the response (§13.6, so it can be passed back as `expected_sha256`)
- [ ] `write_file` — optimistic concurrency via `expected_sha256`
- [ ] `patch_file` — unified diff at minimum; report applied/changed/old+new
      hashes/rejected hunks
- [ ] `delete_file` — recursive deletion explicit
- [ ] `search_files` — glob, text, regex; ripgrep-backed where available; honour
      `.gitignore` by default with override (§49)
- [ ] Size limits per §12.2; binary handling per §47
- [ ] AT-003: a write appears immediately in the host checkout
- [ ] AT-018: stale `expected_sha256` → `FILE_CONFLICT`

### S11 — `workspace_open` and `workspace_info`
**Spec:** §8.3, §25, §34.1, §62 · **Satisfies:** FR-003, FR-030, FR-035 · **Verified by:** AT-032

- [ ] `workspace_open` per §8.3, creating and starting as needed
- [ ] `workspace_info` returning the same payload (§34.1)
- [ ] Report `git_dir_mode`, `ssh_key_mode`, `ssh_key_scope`, `remote_protection`
      (§25 — SHALL, not SHOULD)
- [ ] `server_info` with `version`, `contract_version`, `capabilities` (§33.3)
- [ ] AT-032: reported modes match actual configuration

### S12 — Synchronous `exec`
**Spec:** §14, §16 · **Satisfies:** FR-012, FR-013

- [ ] argv and shell forms; argv preferred
- [ ] `cwd` defaults to `/workspace`, constrained to allowed container paths
- [ ] Env overrides; sensitive values redacted from logs
- [ ] Strict maximum duration; on overrun promote to a job and return a job handle
      with `promoted_to_job: true` (§14.4) — the two-shape response must be
      explicit in `outputSchema`
- [ ] Bounded output per §15.5

### S13 — Tool contract export
**Spec:** §33.1, §33.2, §71

- [ ] `export-schemas` emitting the full `tools/list` with `inputSchema` and
      `outputSchema` per tool to `schemas/tools.json`
- [ ] Commit the file; CI fails when the export differs from the committed copy
- [ ] `contract_version` bump rules per §33.2 enforced in review

---

## Phase 2 — Persistent Execution

Acceptance: long builds survive individual MCP request timeouts.

### S14 — Job store and lifecycle
**Spec:** §15.1, §15.2, §15.4, §23, §67 · **Satisfies:** FR-014

- [ ] Job states per §15.1; records per §15.2 using `stdout_total_bytes` /
      `stderr_total_bytes` / `output_truncated` / `logs_pruned`
- [ ] Jobs survive MCP reconnect and outlive the request that created them
- [ ] On daemon restart, reconcile against container processes; unrecoverable → `lost`

### S15 — Async execution tools
**Spec:** §15.3, §63, §64 · **Satisfies:** FR-015, FR-016, FR-017 · **Verified by:** AT-006

- [ ] `exec_start`, `exec_status`, `exec_output` (offset/range/stream/line window),
      `exec_cancel`
- [ ] Cancellation: graceful termination, then force after a grace period
- [ ] AT-006: start a long build, disconnect/reconnect, job still queryable

### S16 — Job log storage
**Spec:** §15.5, §15.6, §46 · **Verified by:** AT-007

- [ ] Capture streams to `<state_dir>/jobs/<job_id>/{stdout,stderr}.log` — never in
      the repository, never in the container
- [ ] `max_output_bytes` as the per-stream per-job on-disk cap
- [ ] Past the cap: stop appending, keep counting, keep a `tail_keep_bytes` ring
      buffer appended at job end, mark the gap `--- <N> bytes omitted ---`
- [ ] Retention by `job_retention_days` and `job_log_max_total_bytes`; running jobs
      never pruned; pruned jobs still queryable with `logs_pruned: true`
- [ ] AT-007: multi-megabyte output stays bounded in responses, retrievable
      incrementally

### S17 — Idle stop
**Spec:** §8.1, §27

- [ ] Stop (never delete) a persistent container after `workspace.idle_stop_after`
      (default 4h) with no request and no running or queued job
- [ ] A running job always holds the container up; `never` disables
- [ ] Audit the stop; release the workspace `ssh-agent` (§19.2)
- [ ] `workspace_open` restarts a stopped workspace

### S18 — Recovery and reconciliation
**Spec:** §44, §69 · **Satisfies:** FR-028 · **Verified by:** AT-017

- [ ] Daemon restart sequence per §44.1, all eight steps in order
- [ ] Container crash → `stopped`/`failed`, repository intact
- [ ] Docker/SQLite divergence per §69: unknown managed container recovered or
      quarantined; missing container marked absent; duplicates fail closed
- [ ] AT-017: kill the container with changes present; changes survive restart

---

## Phase 3 — Structured Git

Acceptance: a client can implement, validate, and commit a change safely.

### S19 — Git execution plumbing
**Spec:** §20.1, §18.7, §44.4 · **Verified by:** AT-025

- [ ] Structured Git tools execute **in-container** via the execution service, as
      the container user
- [ ] `git_status` / `git_diff` / `git_log` MAY run host-side for speed, as the
      daemon uid
- [ ] Host-side reads always invoked with `-c core.hooksPath=<empty-dir>
      -c core.fsmonitor=` and `GIT_OPTIONAL_LOCKS=0`
- [ ] Hooks run normally for in-container operations — they are a feature (§18.7)
- [ ] Interrupted-operation detection: merge, rebase, cherry-pick, bisect (§44.4)
- [ ] AT-025: a `core.hooksPath` hook inside the working tree does not execute
      during a host-side `git_status`

### S20 — `git_status`
**Spec:** §17.2, §22.3 · **Satisfies:** FR-018 · **Verified by:** AT-020

- [ ] Structured output per §17.2: branch, head_sha, upstream, ahead/behind,
      detached, clean, staged/unstaged/untracked
- [ ] Report conflict and interrupted state rather than repairing it
- [ ] AT-020: a merge conflict is reported, not auto-repaired

### S21 — `git_diff` and `git_log`
**Spec:** §17.3, §46 · **Satisfies:** FR-019

- [ ] Working-tree, staged, and commit-to-commit diffs; path filter; stat-only
- [ ] Bounded output with explicit truncation and follow-up retrieval
- [ ] Bounded log

### S22 — `git_commit`
**Spec:** §17.4, §20.6, §45, §65, §18.9 · **Satisfies:** FR-020, FR-024 · **Verified by:** AT-008, AT-019

- [ ] Explicit `paths`; stages exactly what it is told (no separate `git_add`)
- [ ] `expected_branch` / `expected_head_sha` guards; mismatch → `GIT_HEAD_CHANGED`
- [ ] Strict mode: refuse when a path outside `paths` is staged
- [ ] `amend` parameter, governed by `git_policy.amend_published`
- [ ] `no_verify` parameter
- [ ] `default_branch_commit` policy
- [ ] Preconditions per §20.6; failures leave state unmodified
- [ ] AT-008: guarded commit against a stale HEAD fails
- [ ] AT-019: clean-tree-required operation fails with unrelated changes present

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
