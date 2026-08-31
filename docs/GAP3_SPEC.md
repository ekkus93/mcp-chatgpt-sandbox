# Gap 3 Spec

**Source:** code review of current `master` on 2026-08-31. The reviewed GAP2 implementation is at `6e2c111e6a1c541c533103e869adcf12cbc36567`; current `master` is `ae52ece25e44c07de4c2b799920706cea97b2f30`, whose only change from that reviewed parent is `.gitignore`.

**Series:** `G3R-001`..`G3R-017` (requirements), `G3AT-001`..`G3AT-017` (acceptance tests).

`docs/GAP2_TODO.md` currently declares X1-X22 complete. This review found that three of those tasks need to be reopened (`X3`, `X14`, `X15`) and found additional security, correctness, operational, and test-integrity defects outside GAP2. This document defines the requirements needed to close those findings.

The central rule for this pass is:

> A security-sensitive cleanup, verification, or policy operation SHALL NOT convert **unknown**, **failed**, or **not verified** into **success** merely to keep the primary workflow moving. Where best-effort behavior is legitimately required, failure SHALL remain durably observable and retryable.

This does **not** mean every cleanup failure must retroactively fail an operation that has already completed. It means the daemon must not erase the evidence or state needed to recover from that cleanup failure later.

---

## Phase 0 — Security boundaries that still fail open

### G3R-001 — The privileged entrypoint SHALL start Python in isolated mode

**Reopens:** GAP2 X3 / G2R-003.

`docker/default/entrypoint-wrapper.py` removes Bash startup controls only after `/usr/bin/python3` has already initialized. The wrapper itself therefore has an interpreter-startup boundary before `_sanitized_environment()` can execute.

A project-controlled environment can exploit Python startup state such as `PYTHONPATH`. If the repository contains `sitecustomize.py`, normal Python startup may import it before the wrapper reaches its first executable line. Under `container.sudo: false`, this is a privilege-boundary violation because the container is still in its root entrypoint phase.

**Requirement.** The real Docker `ENTRYPOINT` SHALL invoke the wrapper in a Python mode that ignores environment-controlled Python startup state and unsafe import search paths. The preferred implementation is an explicit absolute interpreter invocation equivalent to:

```text
/usr/bin/python3 -I /usr/local/bin/mcp-entrypoint-wrapper.py
```

The implementation SHALL NOT rely on a shebang alone to provide isolation. The wrapper SHALL continue to sanitize Bash startup state before execing the real Bash entrypoint.

The test SHALL exercise the actual image entrypoint path, not merely call `_sanitized_environment()` directly.

### G3R-002 — Restart reconciliation SHALL NOT signal a persisted PID without proving process identity

`SSHAgentManager.reconcile()` persists an ssh-agent PID in SQLite and later sends SIGTERM to that PID after daemon restart. PID reuse makes the persisted integer insufficient proof that the current process is the agent the daemon originally created.

**Requirement.** After a daemon restart, the server SHALL NOT send a signal to a persisted PID unless it can prove that the process identity still matches the tracked ssh-agent instance.

The identity proof SHALL include enough restart-stable information to distinguish PID reuse. Acceptable designs include a combination of host boot ID, process start time, expected executable/command line, and expected socket ownership/path. A same-process pidfd is useful but is not sufficient by itself for restart recovery because it does not survive daemon restart.

If identity cannot be proven, reconciliation SHALL treat the row as stale, remove or quarantine stale socket/database state, and SHALL NOT signal the process.

### G3R-003 — `require_remote_protection: deny` SHALL fail closed on `unverified`

Current policy denies only a confirmed `missing` status. API outage, missing credential, authorization failure, malformed response, timeout, or unimplemented auth can yield `unverified`, and `deny` still permits workspace opening and credential provisioning.

**Requirement.** Policy semantics SHALL be:

- `off`: no enforcement;
- `warn`: `missing` or `unverified` is reported but does not block;
- `deny`: both `missing` and `unverified` block creation/opening of a write-capable workspace.

If backward compatibility requires the current weaker behavior, it SHALL receive a distinct, unambiguous name such as `deny_confirmed_missing`; it SHALL NOT remain hidden behind the word `deny`.

Denial SHALL occur before loading a repository key into an ssh-agent or creating a write-capable container.

### G3R-004 — SSH key unload failure SHALL be checked and fail closed

`SSHAgentManager.unload_key()` runs `ssh-add -D` and ignores its return code. `workspace.stop()` can therefore report success while the per-workspace agent still holds credentials.

**Requirement.** Key unload SHALL verify the result of `ssh-add -D`. A failed unload SHALL NOT be treated as successful credential removal.

Because the agent is per-workspace, the preferred fallback is to terminate that agent entirely when key removal fails. If both unload and agent termination fail, the daemon SHALL return or record a typed credential-cleanup failure and expose degraded credential state.

A benign already-dead/missing agent may remain a no-op only when the daemon has evidence that no credential remains usable.

### G3R-005 — Workspace creation failure SHALL NOT leave an unnecessary loaded repository key behind

`workspace_open` currently ensures/loads an ssh-agent key before Docker workspace creation succeeds. A later Docker failure can leave the key loaded even though the workspace open failed.

**Requirement.** The lifecycle SHALL separate socket availability from key loading where practical:

1. create/reuse the per-workspace agent socket;
2. build mounts and create/start the container;
3. only after the container has succeeded, load the repository key;
4. if any subsequent provisioning step fails, roll back credential state or durably mark cleanup debt.

If the implementation must load the key earlier, it SHALL install an explicit rollback path covering every failure between key load and successful `workspace_open` completion.

---

## Phase 1 — Correctness and durability

### G3R-006 — `image_digest` SHALL come from the actual running container image

**Reopens:** GAP2 X14 / G2R-014.

Resolving `record.image` through `docker.images.get(record.image)` is not sufficient when `record.image` is a mutable tag. A running container created from image A can continue running A after the tag is retargeted to image B, while a fresh tag lookup reports B.

**Requirement.** `workspace_info.image_digest` SHALL identify the immutable image actually used by the current container. The value SHALL be derived from refreshed container metadata / immutable container image ID, not by resolving the mutable configured tag again.

The user-facing `image` field MAY remain the configured/original reference, but `image_digest` SHALL describe the container actually executing commands.

### G3R-007 — Failed job-log deletion SHALL remain durably discoverable and retryable

**Reopens:** GAP2 X15 / G2R-015.

Workspace deletion currently removes job rows first and then best-effort deletes `<state_dir>/jobs/<job_id>/`. If that filesystem deletion fails, the warning is logged after the database linkage has been erased. Retention can never discover the orphan again and disk accounting under-counts it permanently.

**Requirement.** A failed post-delete job-log cleanup SHALL leave durable retry state.

Acceptable designs include:

- a cleanup/tombstone table retained until the directory is successfully removed; or
- retention that scans the jobs directory and identifies row-less directories safely.

The primary workspace delete operation MAY still succeed once the workspace/container metadata is gone. However, cleanup debt SHALL remain observable, included in accounting, and retried until success or explicit operator disposition.

### G3R-008 — `list_files` SHALL bound traversal work, not only returned entries

**Tightens:** GAP2 X18 / G2R-018.

Removing `sorted(rglob())` made traversal lazy, but the current limit is based on `len(entries)`. Excluded entries such as `.git` do not increase that count, and `Path.rglob()` cannot prune a subtree after it has decided to descend into it. A huge excluded `.git/objects` tree can therefore still consume unbounded work before the requested number of visible entries is produced.

**Requirement.** Recursive listing SHALL use a traversal mechanism that can prune excluded subtrees before descent. `.git` SHALL be pruned, not walked and filtered entry-by-entry.

The implementation SHALL also enforce a configurable or derived candidate-work bound so pathological trees cannot force effectively unbounded traversal merely because candidates are skipped.

The returned-entry limit and the work/candidate limit SHALL be distinct concepts in code and tests.

### G3R-009 — Unimplemented project configuration SHALL not be silently accepted as operational

`ProjectWorkspaceConfig` accepts fields such as `workspace` and `validation` while documenting that they are parsed but not acted on.

**Requirement.** A project configuration field that appears operational SHALL be one of:

1. implemented; or
2. rejected with a clear unsupported-feature error; or
3. explicitly surfaced in the tool response as ignored/deferred configuration.

The default SHALL NOT silently accept an operational-looking field and then do nothing with it.

For this pass, rejection is preferred unless implementing the feature is smaller and already specified by `docs/SPEC.md`.

### G3R-010 — Hook dispatch SHALL distinguish “not configured” from Git configuration failure

`.githooks/_global-hook-dispatch` currently uses `git config ... 2>/dev/null || true`, collapsing an absent global `core.hooksPath` and a real Git/configuration failure into the same empty-success path.

**Requirement.** Hook dispatch SHALL distinguish the normal “no value configured” exit status from unexpected failures. Unexpected failures SHALL be visible and SHALL fail the hook rather than silently disabling a potentially security-relevant global hook.

### G3R-011 — Local and global hooks with the same name SHALL have an explicit composition policy

GAP2 X7 forwards global hooks for names the repository does not implement, but this repository implements `pre-commit`. A developer with a global `pre-commit` does not automatically get both.

**Requirement.** The project SHALL explicitly choose and document one policy for same-name hooks:

- chain local then global;
- chain global then local;
- deliberately replace global with local for a named hook.

If replacement is deliberate, the behavior SHALL be documented next to hook activation instructions and covered by a test so the heading “does not disable others” is not broader than the actual behavior.

### G3R-012 — Audit write failures SHALL produce durable degraded-health state

It is valid not to retroactively fail an operation that has already completed because the audit append then failed. Current behavior, however, only prints to stderr.

**Requirement.** Audit write failure SHALL set durable or process-lifetime health state visible through a diagnostic surface such as `server_info`. At minimum expose:

- audit health (`healthy` / `degraded`);
- last failure timestamp;
- sanitized last failure reason;
- whether auditing is configured as required or best-effort.

A strict configuration MAY additionally require audit health before beginning sensitive operations. If strict mode is implemented, it SHALL check before the side effect rather than attempt to retroactively fail an already-completed Git/Docker operation.

### G3R-013 — Atomic writes SHALL clean temporary files when final replacement fails

The X10 atomic write path cleans temporary files when `_write_all()` fails, but a later `os.replace()` failure can leave `.mcp-write-tmp-*` files behind.

**Requirement.** Every failure path before successful replacement SHALL best-effort remove the temporary file. Cleanup failure SHALL not hide the original write error, but it SHALL be logged/diagnosed with enough context to find the orphan.

The original target SHALL remain unchanged when replacement fails.

---

## Phase 2 — Operational behavior and integrity

### G3R-014 — Idle-stop and job-log retention SHALL run without requiring unrelated user traffic

GAP2 X11 correctly made opportunistic maintenance unable to fail the triggering request, but both idle-stop and job-log retention remain dependent on unrelated future requests because no scheduler exists.

**Requirement.** The daemon SHALL provide an internal maintenance scheduler for at least:

- idle workspace sweeps;
- job-log retention / deferred cleanup retries.

Maintenance failures SHALL be isolated per workspace/job, logged, and reflected in maintenance health. They SHALL NOT terminate the scheduler loop permanently.

The opportunistic call sites MAY remain as additional triggers, but SHALL NOT be the only mechanism.

### G3R-015 — Concurrent test suites SHALL be isolated rather than merely documented as incompatible

GAP2 X22 documents deterministic container-name collisions between concurrent workspace test runs but leaves the collision in place.

**Requirement.** Test resources SHALL be namespaced per test run without weakening the production guarantee of deterministic workspace naming.

The namespace SHALL exist only in test configuration / injected naming context. Production names SHALL remain unchanged.

Two concurrent `tests/workspace/` runs SHALL not adopt, stop, inspect, or delete each other's containers. A retry is not an acceptable solution.

### G3R-016 — Acceptance-test coverage debt SHALL be represented as executable coverage, not permanent citation allowlists

GAP2 X19 expanded the citation audit but records all GATs and most RATs in `known_uncited`. The audit therefore detects drift but does not prove every acceptance requirement has a named executable test.

**Requirement.** Every acceptance-test identifier in active specs SHALL either:

- be cited by at least one actual automated test that exercises the requirement; or
- be explicitly marked in the spec as deferred/non-automated with a reason and owner/task.

`known_uncited` SHALL not be used as a permanent substitute for acceptance coverage. This pass SHALL reduce the existing allowlists by adding real citations/tests or by formally correcting the corresponding spec language where the test already exists under a requirement identifier.

### G3R-017 — Security invariants in source comments SHALL describe current behavior, not historical claims

The review found comments that confidently described stronger properties than the code actually provided, notably the X3 Python wrapper and X14 image-digest logic. Long historical narratives in source can make stale claims look like current invariants.

**Requirement.** While touching files for G3R-001..G3R-016:

- move historical reproduction narratives that are not needed to understand the current invariant into GAP/remediation documentation;
- keep source comments focused on what the current code enforces and why;
- do not remove comments that explain non-obvious security constraints or deliberate fallback behavior;
- where a comment makes a testable security claim, add or point to an adversarial test that proves it.

This is a scoped cleanup of touched areas, not a repository-wide comment rewrite.

---

## Acceptance tests

Each acceptance test below SHALL be automated unless the test explicitly says it is a static integrity test.

### G3AT-001 — Python startup injection cannot run before privilege drop

Build/run the shipped default image with `container.sudo: false`, hostile `PYTHONPATH`, and a repository `sitecustomize.py` that records effective UID or creates a sentinel. The sentinel SHALL not be created by privileged Python startup. The final workspace command SHALL run under the configured non-root uid.

Revert-verification SHALL demonstrate the test fails when isolated Python startup is removed.

### G3AT-002 — Stale ssh-agent PID cannot kill an unrelated process

Persist an ssh-agent record, terminate the agent, cause or simulate PID reuse with an unrelated process, then run reconciliation. The unrelated process SHALL remain alive. Reconciliation SHALL discard/quarantine the stale agent record without signaling it.

### G3AT-003 — Remote-protection `deny` blocks all unverified states

Under `require_remote_protection: deny`, separately exercise:

- no credential;
- 404/authorization failure;
- timeout/network failure;
- malformed successful response.

Each SHALL deny before container creation and before repository-key loading. `warn` SHALL still allow these cases while reporting the reason.

### G3AT-004 — Failed key unload cannot be reported as successful removal

Force `ssh-add -D` to fail. The implementation SHALL either terminate the per-workspace agent successfully or surface a typed/degraded credential-cleanup result. A live agent with the key still loaded SHALL never be silently treated as cleaned.

### G3AT-005 — Failed workspace creation leaves no unnecessarily loaded key

Make agent/socket creation succeed and Docker container creation fail. After `workspace_open` returns the Docker failure, the repository key SHALL not remain loaded unless a durable cleanup-debt record explicitly reports the failure.

### G3AT-006 — Retagging does not change the digest reported for an existing container

Create image A as `test:latest`, create/open a workspace, retag `test:latest` to image B without rebuilding the workspace, then call `workspace_info`. `image_digest` SHALL still equal A's immutable image ID. After an explicit rebuild, it SHALL equal B.

### G3AT-007 — Failed job-log deletion is retried and accounted

Create a workspace/job log, make deletion fail, delete the workspace, and verify:

- workspace deletion semantics remain correct;
- cleanup debt remains discoverable;
- disk accounting includes the orphan/pending directory;
- after removing the failure and running maintenance, the directory is deleted and cleanup debt clears.

### G3AT-008 — Recursive listing prunes excluded `.git` work

Create a repository with a very large `.git`-shaped subtree and a small visible tree. Instrument candidate enumeration. `list_files(..., recursive=True, max_entries=1)` SHALL not traverse the excluded subtree and SHALL remain below the configured work bound.

Also test a tree with many skipped/vanishing candidates to prove the work bound is independent of `len(entries)`.

### G3AT-009 — Unsupported project-config fields cannot silently no-op

Supply each accepted-but-unimplemented operational field. The server SHALL reject it or explicitly report it as ignored according to G3R-009. A test SHALL fail if the field is silently accepted with no diagnostic.

### G3AT-010 — Hook dispatcher propagates unexpected Git config failure

Simulate `git config --global --get core.hooksPath` returning the normal “not configured” status and verify the hook continues. Separately simulate a genuine Git/config error and verify the hook fails loudly rather than taking the same path.

### G3AT-011 — Same-name local/global hook behavior matches documented policy

Install a global `pre-commit` sentinel and this repository's local `pre-commit`. A real commit SHALL demonstrate the chosen composition/replacement policy exactly. Documentation and test expectations SHALL agree.

### G3AT-012 — Audit failure becomes visible health degradation

Make `audit.path` unwritable after startup, perform an operation that attempts an audit write, and verify the primary operation follows the configured best-effort/strict policy while `server_info` (or equivalent) reports audit degradation with a sanitized reason.

### G3AT-013 — Failed `os.replace` leaves original content intact and no temporary file

Inject an `os.replace` failure after the temp file was fully written. The original target SHALL remain byte-for-byte unchanged and no `.mcp-write-tmp-*` file SHALL remain when cleanup itself succeeds.

### G3AT-014 — Maintenance runs without client traffic

Configure short test intervals, create an idle workspace and an expired/pending job log, then make no MCP workspace request. The scheduler SHALL stop/prune/retry them within the bounded test interval. One failing maintenance item SHALL not stop later cycles or other items.

### G3AT-015 — Two concurrent workspace test runs do not collide

Run two independently namespaced integration-test contexts concurrently against the same Docker daemon and repository IDs. Their container names/labels SHALL not collide and teardown from one SHALL not alter the other's resources. Production naming tests SHALL still prove deterministic names without a test namespace.

### G3AT-016 — Acceptance coverage audit has no unexplained permanent gaps

The acceptance audit SHALL fail if an active acceptance identifier lacks a real test citation and is not formally marked deferred in its source spec. Existing `known_uncited` allowances SHALL be eliminated or reduced to explicitly tracked deferrals with a corresponding TODO item.

### G3AT-017 — Touched security comments remain test-backed and current

Static integrity test or review check: comments changed for this pass SHALL not claim that mutable tag lookup proves running image identity, that a normal Python shebang neutralizes Python startup state, or that a best-effort cleanup succeeded when only a warning occurred. Security claims introduced by this pass SHALL point to the relevant G3AT/G3R identifier.

---

## Required failure semantics

The following outcomes are normative:

| Situation | Required behavior |
| --- | --- |
| Python interpreter startup cannot be isolated | fail image/workspace provisioning; never silently continue privileged |
| persisted ssh-agent PID identity cannot be proven | do not signal; discard/quarantine stale state |
| remote protection is unverified under `deny` | deny before key/container provisioning |
| ssh key unload fails | terminate agent or surface degraded/typed failure; never claim cleaned |
| post-delete log cleanup fails | workspace delete may succeed, but cleanup debt remains durable/retryable/accounted |
| audit append fails in best-effort mode | primary result may stand, but audit health becomes degraded and visible |
| maintenance item fails | isolate/log/health-report it; scheduler continues |
| unsupported operational config appears | reject or explicitly report; never silently ignore |

---

## Definition of done

This pass is complete only when:

1. G3R-001..G3R-017 are implemented or explicitly corrected in this spec with evidence;
2. G3AT-001..G3AT-017 pass;
3. GAP2 X3, X14, and X15 are amended with correction notes pointing to this pass;
4. no failure path named above converts unknown/failed cleanup or verification into silent success;
5. `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy src` pass;
6. the relevant targeted suites pass;
7. the full suite passes with no Docker-dependent test skipped and no foreign Docker state modified;
8. the acceptance-coverage audit passes under the tightened G3R-016 semantics;
9. schema drift is checked for every tool-output/error/diagnostic change;
10. every task is revert-verified so its new adversarial test fails for the intended reason against the pre-fix implementation.
