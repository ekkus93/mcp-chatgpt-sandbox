# Gap 3 TODO

**Spec:** `docs/GAP3_SPEC.md` (`G3R-001`..`G3R-017`, `G3AT-001`..`G3AT-017`)

**Source:** 2026-08-31 review of current `master`. GAP2 implementation reviewed at `6e2c111e6a1c541c533103e869adcf12cbc36567`; current `master` is `ae52ece25e44c07de4c2b799920706cea97b2f30` and differs only by `.gitignore`.

**Status:** open — 17 tasks.

Three tasks explicitly reopen GAP2 work:

- **Y1 reopens X3** — the Bash sanitizer starts under Python that is itself influenced by Python startup environment state;
- **Y6 reopens X14** — mutable tag resolution still does not prove the image an existing container actually runs;
- **Y7 reopens X15** — failed log deletion is still forgotten after job rows disappear.

The remaining tasks close new findings from the same code review. Do not treat this list as a request for broad refactoring. Each task is scoped to a reproduced defect or an operational/integrity gap stated in `docs/GAP3_SPEC.md`.

## How to work this list

For every task:

1. Read the corresponding `G3R-*` and `G3AT-*` sections first.
2. Write the adversarial/regression test before the fix and confirm it fails for the intended reason.
3. Implement the smallest fix that closes the whole stated mechanism, not one example spelling.
4. Run the new test alone, then the relevant suite, then the full suite.
5. Revert-verify: temporarily restore the pre-fix implementation and confirm the new test fails again for the same reason.
6. Run:

   ```text
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   ```

7. Run schema-drift checks whenever a tool response, diagnostic field, error code, or configuration schema changes. **Y3, Y4, Y6, Y7, Y9, Y12, and Y16 are obvious candidates; still run the drift check for every task.**
8. One task per commit unless two changes are mechanically inseparable and the TODO is amended before merging them.
9. Do not use a retry to hide deterministic failures.
10. Any best-effort cleanup introduced by a task must preserve durable degraded/retry state if the cleanup can fail after the primary operation has completed.
11. Do not claim a security property from reading code alone. Exercise the control with hostile input.
12. Record corrections in the task write-up when an implementation attempt disproves this TODO's own suggested mechanism.

## Recommended order

**Security first:** Y1, Y2, Y3, Y4, Y5.

**Then reopened correctness:** Y6, Y7, Y8.

**Then explicit behavior / observability:** Y9-Y13.

**Then operational/test integrity:** Y14-Y17.

Y1 is the highest priority because it reopens a privilege-boundary task. Y2 follows because a stale persisted PID can target an unrelated host process. Y3-Y5 concern credentials/policy and should land before lower-risk correctness work.

---

## Phase 0 — Security

### Y1 — Reopen X3: start the privileged Python wrapper in isolated mode

**Satisfies:** G3R-001 · **Verified by:** G3AT-001

- [ ] Change the real Docker `ENTRYPOINT` so Python starts with isolated startup semantics (`-I` or demonstrably equivalent)
- [ ] Keep the interpreter path absolute; do not reintroduce `$PATH` resolution
- [ ] Keep Bash-state sanitization in `entrypoint-wrapper.py`
- [ ] Add a hostile `PYTHONPATH` + repository `sitecustomize.py` test through the real image entrypoint
- [ ] Prove `container.sudo: false` still reaches the final command as the configured non-root uid
- [ ] Revert-verify that removing isolated mode makes the hostile startup test fail
- [ ] Amend GAP2 X3's completion write-up with a correction pointing here

**Do not** satisfy this by adding `PYTHONPATH` to the project-config denylist only. That would repeat X1's pattern: today's input spelling would close while the mechanism remains open to a future surface. The interpreter itself must start isolated.

### Y2 — Never signal an ssh-agent PID after restart without identity proof

**Satisfies:** G3R-002 · **Verified by:** G3AT-002

- [ ] Extend persisted agent identity beyond a bare PID
- [ ] Include restart-stable identity sufficient to detect PID reuse (e.g. boot ID + process start time + expected executable/socket)
- [ ] Verify identity before any reconciliation signal
- [ ] If identity cannot be proven, do not signal; remove/quarantine stale tracking state
- [ ] Add a deterministic PID-reuse simulation test proving an unrelated process survives reconciliation
- [ ] Keep same-process normal termination behavior working
- [ ] Document which `/proc`/platform assumptions are Linux-specific

**Dangerous fallback to remove:** `_terminate_pid(record.pid)` based only on the integer from SQLite. “It is probably still ssh-agent” is not an acceptable host-process identity check.

### Y3 — Make `require_remote_protection: deny` actually fail closed

**Satisfies:** G3R-003 · **Verified by:** G3AT-003

- [ ] `deny` rejects `missing`
- [ ] `deny` also rejects `unverified`
- [ ] `warn` continues to allow both while returning the exact reason
- [ ] Verify no repository key is provisioned before a deny result
- [ ] Verify no workspace container is created before a deny result
- [ ] Add cases for absent token, 404/authorization failure, timeout/network error, and malformed response
- [ ] Update configuration/docs to state the semantics unambiguously
- [ ] If compatibility needs old behavior, give it a distinct setting name instead of weakening `deny`
- [ ] Run schema/config drift checks

This is intentionally a semantic change. A policy called `deny` must not mean “allow whenever verification is inconvenient.”

### Y4 — Check SSH key unload and fail closed on credential cleanup

**Satisfies:** G3R-004 · **Verified by:** G3AT-004

- [ ] Check `ssh-add -D` return code and stderr
- [ ] On unload failure, terminate the per-workspace agent as the preferred fallback
- [ ] Verify agent termination actually succeeded before reporting credentials removed
- [ ] If both paths fail, surface a typed/degraded credential-cleanup condition
- [ ] Ensure `workspace.stop` does not silently claim a clean credential state when a live agent still holds the key
- [ ] Preserve benign handling for an already-dead/missing agent only when no credential can remain usable
- [ ] Add audit/operational logging with secret-safe stderr handling
- [ ] Run schema/error drift checks

### Y5 — Roll back SSH credential provisioning when workspace open fails

**Satisfies:** G3R-005 · **Verified by:** G3AT-005

- [ ] Separate agent/socket creation from repository-key loading if feasible
- [ ] Create/start the container before loading the repository key
- [ ] If a later step requires earlier key load, add rollback around every failure between key load and successful open
- [ ] Docker create failure leaves no unnecessarily loaded key
- [ ] Docker start/setup failure follows the same cleanup rule
- [ ] Cleanup failure becomes durable/visible rather than warning-and-forget
- [ ] Preserve the pre-container remote-protection deny ordering from Y3

---

## Phase 1 — Correctness and durability

### Y6 — Reopen X14: derive image digest from the actual container

**Satisfies:** G3R-006 · **Verified by:** G3AT-006

- [x] Refresh/read immutable image identity from current container metadata
- [x] Stop deriving the runtime digest by resolving `record.image` as a mutable tag
- [x] Preserve a configured/reference image field separately if useful
- [x] Test same-tag retargeting: A -> create container -> retag to B -> existing workspace still reports A digest
- [x] Test explicit rebuild changes runtime digest to B
- [x] Handle missing local tag without losing the ability to report an already-running container's immutable image ID
- [x] Run schema drift check
- [x] Correct comments that currently claim tag lookup remains correct after retagging
- [x] Amend GAP2 X14's completion write-up with a correction pointing here

**Completion evidence.** PR #4 derives `image_digest` from the running
container's immutable Docker inspect `Image` value and deliberately has no
mutable-tag fallback when that identity cannot be inspected. G3AT-006 covers
same-tag retargeting, explicit rebuild, removal of the configured tag after
container creation, and fail-closed behavior when container provenance cannot
be established. CI run `33436682335` passed the complete 1,077-test suite plus
Ruff, mypy, schema export, contract-version, and tool-doc drift gates on the
final code/test head before this documentation-only bookkeeping update.

### Y7 — Reopen X15: make failed job-log cleanup durable and retryable

**Satisfies:** G3R-007 · **Verified by:** G3AT-007

- [ ] Choose and document a durable cleanup-debt model (tombstone table preferred, safe directory reconciliation acceptable)
- [ ] Do not permanently erase the only linkage needed to find a failed log-directory cleanup
- [ ] Workspace delete may still complete after metadata/container removal
- [ ] Failed log deletion remains visible after the request returns
- [ ] Retention/disk accounting includes pending/orphaned directories
- [ ] Maintenance retries cleanup later
- [ ] Successful retry clears cleanup debt
- [ ] Add tests for permission/transient deletion failure
- [ ] Run schema/state migration checks
- [ ] Amend GAP2 X15's completion write-up with a correction pointing here

**Do not** fix this by making `workspace_delete` fail after the workspace is already gone. Preserve the sound RR-012 principle; add durable deferred cleanup instead.

### Y8 — Tighten X18 so `list_files` bounds actual traversal work

**Satisfies:** G3R-008 · **Verified by:** G3AT-008

- [ ] Replace recursive `Path.rglob` with a traversal that can prune `.git` before descent
- [ ] Keep symlink handling and repository-boundary rules intact
- [ ] Add a candidate/work budget distinct from `max_entries`
- [ ] Define the response/error when the work budget is exhausted before the requested visible-entry count is reached
- [ ] Instrument generator/scandir work, not wall-clock time
- [ ] Add a huge `.git/objects` fixture proving it is pruned rather than traversed
- [ ] Add a many-skipped-candidates fixture proving the work bound is independent of `len(entries)`
- [ ] Preserve existing X9 concurrent-vanish behavior

---

## Phase 2 — Explicit behavior and observability

### Y9 — Reject or explicitly report unimplemented project config

**Satisfies:** G3R-009 · **Verified by:** G3AT-009

- [ ] Inventory every `ProjectWorkspaceConfig` field and whether runtime code consumes it
- [ ] For `workspace`, choose implement vs reject vs explicit ignored-field reporting
- [ ] For `validation`, choose implement vs reject vs explicit ignored-field reporting
- [ ] Prefer rejection for this pass unless implementation is already specified and small
- [ ] No operational-looking field may silently parse and do nothing
- [ ] Update `docs/CONFIGURATION.md` / tool docs
- [ ] Add tests for every currently unimplemented field
- [ ] Run config/schema drift checks

### Y10 — Stop hiding unexpected global-hook configuration errors

**Satisfies:** G3R-010 · **Verified by:** G3AT-010

- [ ] Replace `... 2>/dev/null || true` with exit-status-aware handling
- [ ] Treat Git's normal “key not configured” result as benign absence
- [ ] Treat malformed/unreadable global config or Git execution failure as a hook failure
- [ ] Preserve support for `~/` global hooks paths
- [ ] Add tests for absent setting and genuine error separately
- [ ] Keep stderr useful but avoid leaking secrets from Git config values

### Y11 — Decide same-name local/global hook composition explicitly

**Satisfies:** G3R-011 · **Verified by:** G3AT-011

- [ ] Decide whether local `pre-commit` chains, precedes, follows, or deliberately replaces global `pre-commit`
- [ ] Implement the chosen policy
- [ ] Exercise both hooks in a real `git commit` acceptance test
- [ ] Update `docs/DEVELOPMENT.md`
- [ ] Correct GAP2 X7 wording if it currently implies stronger generic composition than implemented

If the repository deliberately replaces a global `pre-commit`, say that clearly. Do not leave the behavior as an accidental consequence of `core.hooksPath`.

### Y12 — Expose audit degradation as server health

**Satisfies:** G3R-012 · **Verified by:** G3AT-012

- [ ] Add audit health state (`healthy`/`degraded`)
- [ ] Store last failure timestamp and sanitized reason
- [ ] Expose it through `server_info` or an equivalent diagnostic tool
- [ ] Preserve best-effort semantics for already-completed operations
- [ ] Decide whether `audit.required`/strict mode belongs in this task; if added, enforce before side effects
- [ ] Verify recovery: a later successful write restores healthy state or records a clear recovered state
- [ ] Add tests for disk/permission write failure and recovery
- [ ] Run tool-schema drift checks

### Y13 — Clean atomic-write temp files after `os.replace` failure

**Satisfies:** G3R-013 · **Verified by:** G3AT-013

- [ ] Put the final `os.replace` under the same temp-file cleanup guarantee as `_write_all`
- [ ] Inject replacement failure after a complete temp write
- [ ] Original target remains unchanged
- [ ] Temp file is removed when cleanup succeeds
- [ ] If temp cleanup itself fails, preserve the original replace error while logging/diagnosing the orphan temp path
- [ ] Ensure cleanup remains relative to the already-validated parent fd

---

## Phase 3 — Operational and test integrity

### Y14 — Add a real maintenance scheduler

**Satisfies:** G3R-014 · **Verified by:** G3AT-014

- [ ] Add an internal periodic scheduler/task owned by server lifecycle
- [ ] Run idle workspace sweep on schedule
- [ ] Run job-log retention and Y7 deferred cleanup retries on schedule
- [ ] Isolate failures per maintenance item
- [ ] A failure in one cycle does not kill the scheduler
- [ ] Expose maintenance health / last failure information
- [ ] Shut scheduler down cleanly with the daemon
- [ ] Keep opportunistic triggers only as additional defense-in-depth
- [ ] Test maintenance with no MCP workspace request occurring

### Y15 — Namespace Docker resources per test run

**Satisfies:** G3R-015 · **Verified by:** G3AT-015

- [ ] Add a test-only namespace injectable into workspace/container naming
- [ ] Do not change production deterministic naming
- [ ] Apply the namespace consistently to container labels/names and any test-owned volumes that can collide
- [ ] Run two concurrent integration contexts against identical repository IDs without collision
- [ ] Teardown from one context cannot delete/adopt/stop resources from the other
- [ ] Keep existing foreign-resource protections
- [ ] Remove or revise the “run with no concurrent Docker activity” limitation once tests prove isolation

### Y16 — Close acceptance-test citation/coverage debt

**Satisfies:** G3R-016 · **Verified by:** G3AT-016

- [ ] Inventory every `known_uncited` item in `tests/test_acceptance_test_coverage.py`
- [ ] For each, locate the real test if one exists under requirement-based naming and add the acceptance identifier to that test
- [ ] Where no real test exists, add one or formally mark the spec item deferred/non-automated with a corresponding TODO owner
- [ ] Eliminate `known_uncited` as a permanent blanket for all GATs / most RATs
- [ ] Tighten the audit so a new unexplained missing citation fails
- [ ] Keep the meta-tests proving citation removal is detected
- [ ] Add G3AT series to the coverage audit
- [ ] Run schema/test integrity checks

### Y17 — Correct stale security claims in touched source comments

**Satisfies:** G3R-017 · **Verified by:** G3AT-017

- [ ] In files touched by Y1-Y16, identify historical comments that state a current invariant stronger than the code
- [ ] Move long reproduction/history that is not needed locally into GAP3/GAP2 correction prose
- [ ] Keep concise comments explaining non-obvious security constraints
- [ ] Correct X3 comments so they state Python isolated startup is load-bearing
- [ ] Correct X14 comments so mutable-tag resolution is never described as runtime-image proof
- [ ] Correct cleanup comments so “best effort” never reads as “guaranteed reclaimed”
- [ ] Point testable security claims to their `G3AT-*` or `G3R-*` identifier
- [ ] Do not perform a repository-wide prose rewrite outside files already touched by this pass

---

## Definition of done

- [ ] Y1-Y17 complete
- [ ] G3AT-001..G3AT-017 automated and passing
- [ ] GAP2 X3, X14, X15 contain correction notes pointing to GAP3
- [ ] Remote-protection `deny` is fail-closed on `unverified`
- [ ] No persisted ssh-agent PID is signaled after restart without identity proof
- [ ] Failed SSH key unload cannot be silently reported as clean
- [ ] Failed workspace opening does not unnecessarily retain a repository key
- [ ] Runtime image digest comes from the actual container image
- [ ] Failed post-delete log cleanup remains durable, retryable, and accounted
- [ ] Recursive listing prunes excluded subtrees and has an actual work bound
- [ ] Unimplemented operational config cannot silently no-op
- [ ] Hook dispatcher cannot hide unexpected Git config failure
- [ ] Same-name local/global hook behavior is explicit and tested
- [ ] Audit failures appear in server health
- [ ] Atomic write replacement failures leave no temp file when cleanup succeeds
- [ ] Maintenance runs without client traffic
- [ ] Concurrent workspace test runs no longer collide
- [ ] Acceptance coverage audit has no unexplained permanent gaps
- [ ] Security comments touched by this pass describe current, test-backed invariants
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy src` passes
- [ ] Targeted suites pass
- [ ] Full suite passes with no Docker-dependent test skipped and no foreign Docker resources modified
- [ ] Schema drift check passes
- [ ] Every task has been revert-verified
