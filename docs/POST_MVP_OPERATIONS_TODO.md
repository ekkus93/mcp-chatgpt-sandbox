# Post-MVP Operations TODO — Resource Visibility and Safe Reclamation

**Spec:** `docs/POST_MVP_OPERATIONS_SPEC.md` (`PMOR-001`..`PMOR-012`, `PMOAT-001`..`PMOAT-012`)

**Status:** open — O1-O5 complete; O6-O12 remain

This TODO deliberately starts read-only and moves the destructive boundary later. Do not implement prune execution before the inventory/ownership model and dry-run plan are independently tested.

## Working rules

For every task:

1. Read the corresponding PMOR/PMOAT sections before implementation.
2. Write the adversarial/regression acceptance test before the fix/feature and prove it fails for the intended missing behavior.
3. Prefer exact positive ownership proof over name/prefix heuristics.
4. No `docker system prune`, wildcard host cleanup, arbitrary persisted deletion path, or warning-and-forget fallback.
5. Every destructive operation must be plan-scoped, explicitly authorized, and revalidated immediately before execution.
6. Preserve primary errors; diagnostic enrichment must not misclassify or hide them.
7. Any cleanup that can fail after the primary operation completes must remain visible/durable/retryable.
8. Run real-Docker acceptance cases under GAP3 Y15's test namespace and keep a foreign sentinel alive throughout destructive tests.
9. New MCP tools/output fields require schema regeneration, tool docs, and a `contract_version` bump.
10. Run Ruff format/lint, mypy, focused suites, schema/docs drift gates, then the full Docker-backed suite before merging each task.
11. Revert-verify each acceptance test against the pre-task behavior.
12. One task per commit unless the TODO is amended first to explain why two changes are mechanically inseparable.

## Recommended order

**Read-only foundation:** O1–O4.

**Plan boundary:** O5–O7.

**Destructive execution:** O8–O9.

**Operations integration and closeout:** O10–O12.

Do not skip directly to O8. A safe prune executor depends on the ownership/inventory and immutable-plan semantics established earlier.

---

## Phase 0 — Read-only foundation

### O1 — Define the operational resource model and ownership proof

**Satisfies:** PMOR-001 · **Verified by:** PMOAT-001

- [x] Inventory current durable state and Docker labels used for workspace containers and cache volumes
- [x] Define resource kinds and lifecycle states (`active`, `retained`, `cleanup_pending`, `orphaned_owned`, `foreign`, `unknown/degraded`)
- [x] Define exact positive ownership evidence per resource kind
- [x] Preserve GAP3 Y15 test namespace as part of test-resource ownership identity
- [x] Preserve GAP3 Y7 trusted job-log path reconstruction; never persist/replay arbitrary deletion paths
- [x] Add a foreign lookalike container and volume fixture
- [x] Prove name/prefix resemblance alone never establishes ownership
- [x] Document which resources are intentionally inventory-only (Docker images initially)

**O1 completion evidence:**

- PMOAT-001 first RED: Actions run `33663830634` failed on the real-Docker owned-cache assertion because daemon-created cache volumes had no positive MCP ownership labels. This established that deterministic `mcp-cache-*` naming was only an address, not ownership proof.
- PMOAT-001 second RED: Actions run `33667428159` failed with `1 failed, 1119 passed` because `workspace_delete` deleted an exact-name cache volume whose MCP labels identified a different repository. This reproduced the narrower repository-binding authorization gap before the final fix.
- Implementation: `operations/resources.py` defines resource kinds, lifecycle states, positive label/state evidence, inventory-only kinds, and ownership classifiers; `workspace/manager.py` explicitly creates/verifies labeled cache volumes, requires full container/cache identity correspondence, preserves Y15 test namespace identity, binds cache deletion to the pre-delete durable `repository_id`, fails closed when repository proof is unavailable, and revalidates the actual volume returned from Docker creation before mounting it.
- GAP3 Y7 remains the deletion-path authority for job logs: paths are reconstructed from trusted `state_dir` plus validated job IDs and cleanup debt, never accepted from arbitrary persisted paths.
- Foreign-lookalike acceptance covers unlabeled and exact deterministic cache-name collisions; container adoption/removal also requires positive workspace/repository ownership proof. Docker images are explicitly inventory-only in the O1 model.
- GREEN: Actions run `33669094390` on exact branch head `d4bc1b948be1b9246353a83029aaa38a0cd6e7cb` passed Docker build, Ruff format/lint, mypy, MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1121 passed`.
- Revert/pre-fix verification is represented by the two intentional RED commits above: each PMOAT-001 regression was demonstrated on code lacking the corresponding ownership guarantee before the implementation that makes it green.

### O2 — Add bounded read-only resource inventory

**Satisfies:** PMOR-002, PMOR-003 · **Verified by:** PMOAT-002, PMOAT-003

- [x] Add an internal inventory service independent of MCP schema first
- [x] Enumerate managed containers, cache volumes, job-log accounting/debt, and state-storage observations
- [x] Add explicit result/work bounds
- [x] Report truncation/incomplete state rather than silently dropping excess resources
- [x] Isolate per-resource inspection failure and preserve sanitized diagnostics
- [x] Distinguish measured, accounted/estimated, and unavailable byte values
- [x] Never convert an unavailable measurement into zero
- [x] Prove inventory performs no Docker/filesystem mutation
- [x] Add unit/fake-Docker tests before exposing a tool

**O2 completion evidence:**

- PMOAT-002/003 RED: Actions run `33675126641` on pre-feature head `c9b5afd33f7b06020b59c99f9f2f0572144515d5` passed Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift, then failed exactly the five new O2 acceptance cases because `dev_workspace_mcp.operations.inventory` did not exist; the remainder of the suite was green (`5 failed, 1121 passed`).
- Implementation: `operations/inventory.py` adds the internal `ResourceInventoryService` without changing the MCP schema. It inventories positively classified workspace containers/cache volumes plus durable job-log rows, cleanup debt, SSH-agent tracking, and state-storage observations. The service consumes the already-open SQLite connection rather than opening or creating state during collection.
- Bounds are explicit and observable: `InventoryLimits` independently caps returned resources, per-resource inspections, and diagnostics; results expose result/inspection limits plus `truncated` and `incomplete` instead of silently dropping work. Container discovery uses a real Docker server-side `limit`; Docker's volume-list API has no equivalent server-side pagination/limit, so O2 performs one ownership-filtered list call and bounds all subsequent volume inspection/result work while marking oversize results incomplete.
- Failure isolation/provenance: per-resource Docker inspection failures become sanitized diagnostics and degraded observations without aborting healthy resources. Byte observations distinguish `measured`, `accounted`, `estimated`, and `unavailable`; unavailable or invalid negative measurements carry `value=None` rather than being converted to a reassuring zero.
- Ownership correlation: durable workspace/repository contradictions are reported as `foreign` ownership, while a positively owned container whose durable container ID is stale remains an owned orphan. This preserves the O1 distinction future prune planning depends on.
- Mutation proof: fake-Docker acceptance rejects container stop/kill/remove, volume remove, and prune calls. A dedicated PMOAT-002 host-filesystem test patches common `Path`/`os` mutation APIs to fail immediately during `collect()`, verifies the state-file sentinel bytes remain unchanged, and still permits read-only size/stat observation.
- Core GREEN: Actions run `33676474645` on exact implementation head `f2684ae7889b5a856a846f74fe86dc26e9d0ea44` passed Docker build, Ruff format/lint, mypy, MCP contract, contract-version, docs drift, and `1129 passed` with 92% overall coverage.
- Final O2 GREEN including explicit filesystem non-mutation proof: Actions run `33678931433` on exact head `b89915e140fd47119d9e76c9e12e3ac246b55f46` passed every CI gate and the complete Docker-backed suite: `1130 passed` in 630.69s, 92% overall coverage.
- Revert/pre-feature verification is the clean `33675126641` RED: reverting the O2 implementation removes the inventory module and causes the PMOAT-002/003 acceptance surface to fail before any tool exposure, while all unrelated tests remain green.

### O3 — Expose `resource_inventory` through MCP

**Satisfies:** PMOR-001..PMOR-003 · **Verified by:** PMOAT-001..PMOAT-003

- [x] Define bounded Pydantic input/output models
- [x] Keep default operation global/read-only with optional narrow repository/workspace filtering
- [x] Do not expose arbitrary host filesystem paths
- [x] Include cleanup-debt summaries and explicit degraded/unavailable observations
- [x] Add tool registration and docs/TOOLS.md row
- [x] Bump `contract_version`
- [x] Regenerate `schemas/tools.json`
- [x] Run contract-version and docs-drift checks
- [x] Exercise through the real MCP tool surface

**O3 completion evidence:**

- PMOAT-001..003 pre-feature RED: Actions run `33691099187` on the test-only pre-O3 branch cleared Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift, then failed exactly the four new MCP-surface acceptance cases because `resource_inventory` was not registered (`4 failed, 1130 passed`). This is the clean revert/pre-feature proof for the public tool boundary.
- Implementation: `tools/operations.py` exposes bounded Pydantic input/output models and a mutation-free `resource_inventory` MCP tool over O2's already-proven `ResourceInventoryService`; `server.py` registers it without making Docker reachability a server-start prerequisite. Optional scope accepts only opaque repository/workspace IDs, while the default remains global/read-only.
- Public-boundary safety: output uses an allowlisted metadata shape rather than serializing internal `details`; invalid/path-shaped Docker-controlled repository/workspace IDs are suppressed, managed-label evidence values are rendered as `<opaque-id>`, and scoped calls omit diagnostics attached only to out-of-scope resources. Source-wide/budget failures still keep the scoped result incomplete rather than being hidden.
- Cleanup-debt/provenance safety: the public summary preserves `unavailable` byte observations as `value=None`/`provenance=unavailable` and reports partial accounting instead of coercing an unavailable value to zero. Docker/statfs probe failures remain explicit degraded/unavailable observations rather than fabricated healthy values.
- Contract/docs: `resource_inventory` is documented in `docs/TOOLS.md`; `contract_version` is `0.8.0`; canonical `schemas/tools.json` contains the 22-tool contract; contract-version and docs-drift gates pass.
- Initial implementation GREEN: Actions run `33691982931` passed every CI gate and the complete Docker-backed suite before the final public-boundary hardening review.
- Hardening RED: Actions run `33693182599` cleared every non-test gate, then failed exactly the three adversarial boundary cases for out-of-scope diagnostic leakage, unavailable cleanup-debt bytes becoming `0/accounted`, and path-shaped Docker identities reaching output validation (`3 failed, 1134 passed`).
- Final GREEN: Actions run `33693690086` on exact implementation head `f4a9a4468db633bfee5ea94ef302e4833118bd6e` passed Docker build, Ruff format/lint, mypy, MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1137 passed` in 623.55s with 92% overall coverage.
- Revert verification is represented by the clean pre-feature RED `33691099187` plus the narrower hardening RED `33693182599`; both failed only the acceptance behavior subsequently supplied by O3 while unrelated gates/tests remained green.

### O4 — Add evidence-based disk and PID-pressure diagnostics

**Satisfies:** PMOR-004, PMOR-005 · **Verified by:** PMOAT-004, PMOAT-005

- [x] Define diagnostic states for state-filesystem pressure, Docker-storage no-space, container ENOSPC, and non-space I/O failure
- [x] Preserve original structured errors while attaching only evidence-supported pressure context
- [x] Avoid broad message-substring classification where errno/API evidence exists
- [x] Define Linux PID/process pressure sources and document procfs/cgroup assumptions
- [x] Report unsupported/unavailable metrics explicitly rather than zero
- [x] Ensure unavailable PID metrics do not block ordinary workspace operation
- [x] Add forced state ENOSPC, Docker no-space, container ENOSPC, and non-space I/O cases
- [x] Add supported/unavailable PID-pressure cases

**O4 completion evidence:**

- PMOAT-004/005 module-level RED: Actions run `33704861730` cleared Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift, then failed exactly the seven new pressure/PID acceptance cases because `dev_workspace_mcp.operations.pressure` did not exist (`7 failed, 1137 passed`). This is the clean pre-feature/revert proof for the O4 diagnostic primitives.
- Public-boundary RED: Actions run `33705719797` again cleared every non-test gate, then failed only the nine O4 acceptance cases (`9 failed, 1137 passed`): the seven missing pressure primitives plus missing Docker-error enrichment and missing synchronous `exec.pressure`. This proves the behavior is wired through real MCP/Docker surfaces rather than existing only as helper functions.
- Implementation: `operations/pressure.py` defines explicit `state_filesystem`, `docker_storage`, and `container_filesystem` domains. State ENOSPC requires native `errno.ENOSPC`; contradictory non-space errno remains `non_space_io`. Docker-storage ENOSPC requires a Docker `APIError` daemon explanation carrying the canonical OS signal; generic `DockerException` text is ignored. Container ENOSPC requires a failed command plus canonical `No space left on device`; successful commands and vague `disk full` wording are not promoted.
- Primary-error preservation: `errors.to_tool_error()` enriches an existing chained Docker `WorkspaceError` only by adding sanitized `details.pressure`; the original code, message, recoverability, and pre-existing details remain primary. Synchronous `exec` returns additive pressure evidence for failed container commands without changing its exit/result semantics.
- PID source/availability contract: `observe_pid_pressure()` supports only Linux cgroup v2. `/proc/self/cgroup` locates the daemon's unified cgroup and `pids.current`/`pids.max` under `/sys/fs/cgroup` supply usage/limit. There is deliberately no cgroup-v1 or host `pid_max` fallback. Missing, inaccessible, malformed, or unsupported sources return typed `unavailable` with `current=None`/`limit=None`, never zero, and never block ordinary `resource_inventory` operation.
- Contract/docs: `resource_inventory.pid_pressure` and `exec.pressure` are additive output fields; `contract_version` is `0.8.1`; canonical `schemas/tools.json` remains a 22-tool contract; `docs/TOOLS.md`, schema drift, and contract-version gates pass.
- Superseded non-behavioral run: `33731124137` stopped at Ruff format on one function-signature normalization and therefore is not RED/GREEN acceptance evidence. Commit `1079d74150ac05dec71c83c1ce3cc4a9a38194b8` applied only Ruff's requested formatting.
- Final GREEN: Actions run `33731708015` on O4 head `1079d74150ac05dec71c83c1ce3cc4a9a38194b8` passed Docker build, Ruff format/lint, mypy (`66 source files`), MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1146 passed in 625.55s` with 92% overall coverage.
- Revert/pre-feature verification is represented by `33704861730` plus the stronger real-surface RED `33705719797`; both failed only the O4 behavior subsequently supplied by this task while unrelated gates/tests remained green.

---

## Phase 1 — Plan boundary

### O5 — Build deterministic prune candidates from inventory

**Satisfies:** PMOR-006 · **Verified by:** PMOAT-006

- [x] Define which lifecycle states are eligible for explicit reclamation in this workstream
- [x] Exclude `active`, `foreign`, and `unknown/degraded` resources
- [x] Produce per-candidate reason, ownership evidence, lifecycle preconditions, and expected action
- [x] Include accounted/estimated bytes where known without fabricating measurements
- [x] Keep Docker image deletion out of initial candidates
- [x] Prove candidate construction is read-only
- [x] Prove healthy retained resources are not accidentally eligible unless the spec explicitly permits that kind

**O5 completion evidence:**

- PMOAT-006 initial RED: Actions run `33740510473` cleared Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift, then failed exactly the seven new O5 acceptance cases because `dev_workspace_mcp.operations.prune_candidates` did not exist (`7 failed, 1146 passed`). This is the clean pre-feature/revert proof for deterministic candidate construction.
- SSH-identity hardening RED: Actions run `33741713552` again cleared every non-test gate, then failed exactly eight O5 cases (`8 failed, 1146 passed`): the original seven missing-builder cases plus the production inventory handoff dropping the Y2 SSH-agent `boot_id`/`process_start_time` identity. This proves O5 cannot silently degrade SSH-agent ownership to PID-only identity.
- Implementation: `operations/prune_candidates.py` is a pure snapshot-only builder over `ResourceInventoryResult`; it has no Docker, filesystem, process, or state mutation dependency. It emits only explicitly supported positively owned candidates in exact reclaimable lifecycle states, uses deterministic opaque `pc_<sha256-prefix>` IDs, preserves ownership proof and byte value/provenance, records exact future action and material preconditions, sorts deterministically, and fails closed on duplicate candidate identity.
- Eligibility is intentionally conservative: orphaned owned workspace containers are eligible only when observed `exited`/`dead`; orphaned owned cache volumes are eligible; durable `cleanup_pending` job-log debt is eligible for exact retry; orphaned owned SSH agents are eligible only with PID + boot ID + process-start-time. `active`, healthy `retained`, `foreign`, `unknown/degraded`, physically running/ambiguous orphan containers, unsupported kinds, and Docker images are omitted rather than guessed into a reclaim action.
- Internal SSH-agent inventory now carries the Y2 durable process identity needed by later plan/execution revalidation while the existing public `resource_inventory` metadata allowlist remains unchanged, so O5 adds no new host-path/identity disclosure and requires no MCP contract bump.
- Superseded non-behavioral implementation runs `33857321769` and its predecessor stopped at Ruff formatting only and are not acceptance evidence; the final formatting-only fix did not alter O5 semantics.
- Final GREEN: Actions run `33858085926` on exact O5 head `da54317f1f34d0ac75948251c16ad77867fa9413` passed Docker build, Ruff format/lint, mypy (`67 source files`), MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1154 passed in 639.54s` with 92% overall coverage.
- Revert/pre-feature verification is represented by the clean RED `33740510473` plus the stronger inventory-identity RED `33741713552`; both failed only O5 behavior subsequently supplied by this task while unrelated gates/tests remained green.

### O6 — Add immutable/stale-aware prune plans

**Satisfies:** PMOR-006, PMOR-007 · **Verified by:** PMOAT-006, PMOAT-007

- [ ] Define plan identifier/token bound to exact candidates and material preconditions
- [ ] Persist plans only if needed; if persisted, define bounded retention and restart behavior
- [ ] Never silently recompute/expand an old plan at execute time
- [ ] Revalidate each candidate immediately before destruction
- [ ] Mark changed/active/unprovable candidates stale/skipped
- [ ] Permit independently valid candidates to proceed only with explicit mixed-result reporting
- [ ] Add race test: candidate becomes active after planning
- [ ] Add identity-change test: label/resource identity changes after planning
- [ ] Add unknown-ownership test that fails closed

### O7 — Expose `resource_prune_plan` as dry-run only

**Satisfies:** PMOR-006, PMOR-007 · **Verified by:** PMOAT-006, PMOAT-007

- [ ] Add MCP tool that creates/returns a plan and performs no destructive actions
- [ ] Support only narrow documented filters
- [ ] Default omission of filters to the safest documented eligible set, not "everything Docker can delete"
- [ ] Return plan token, candidates, reasons, preconditions, and projected/accounted bytes
- [ ] Add audit event for plan creation without leaking host paths
- [ ] Bump contract version and regenerate schemas/docs
- [ ] Instrument Docker calls in acceptance test and prove zero mutation

---

## Phase 2 — Explicit destructive execution

### O8 — Implement plan-scoped prune execution

**Satisfies:** PMOR-008, PMOR-009 · **Verified by:** PMOAT-008, PMOAT-009

- [ ] Add execution service that accepts only an existing immutable plan
- [ ] Require explicit confirmation; absent/false confirmation is non-destructive
- [ ] Revalidate ownership and lifecycle preconditions per candidate immediately before action
- [ ] Delete only the exact resource identity recorded in the plan
- [ ] Never fall back to label-wide, prefix-wide, or system-wide prune
- [ ] Make already-proven-absent resources idempotent success/no-op
- [ ] Treat unknown identity/deletion proof as failure/degraded, not success
- [ ] Real-Docker test with owned disposable resources plus foreign lookalike container and volume
- [ ] Prove foreign sentinels survive unchanged

### O9 — Make partial reclamation failure durable and retryable

**Satisfies:** PMOR-010 · **Verified by:** PMOAT-010

- [ ] Return per-candidate outcome and aggregate mixed/degraded state
- [ ] Preserve the primary failure for each candidate
- [ ] Reuse GAP3 Y7 durable cleanup debt for job-log secondary cleanup where applicable
- [ ] Add durable debt/state for any new post-primary cleanup class that otherwise becomes warning-and-forget
- [ ] Keep pending bytes in accounting until absence is proven
- [ ] Retry and clear debt safely
- [ ] Prove repeated execute/retry is idempotent
- [ ] Force secondary cleanup failure after primary deletion and verify no false blanket success

---

## Phase 3 — Operations integration and closeout

### O10 — Integrate operational pressure into health reporting

**Satisfies:** PMOR-004, PMOR-005, PMOR-011 · **Verified by:** PMOAT-004, PMOAT-005, PMOAT-011

- [ ] Decide additive `server_info` shape for operational pressure/inventory degradation
- [ ] Keep unavailable metrics distinguishable from healthy zero usage
- [ ] Preserve existing audit and maintenance health sections
- [ ] Do not make optional pressure probes block server startup
- [ ] Bump contract version and regenerate schemas/docs
- [ ] Test healthy, pressured, degraded-probe, and recovered states

### O11 — Keep lifecycle maintenance conservative

**Satisfies:** PMOR-011 · **Verified by:** PMOAT-011

- [ ] Confirm scheduler continues retrying already-authorized cleanup debt
- [ ] Do not automatically execute newly generated prune plans
- [ ] Do not automatically delete healthy retained caches/workspaces/images
- [ ] Add multiple-cycle test with healthy retained resources plus genuine cleanup debt
- [ ] Prove only the debt is retried
- [ ] Document that automatic age/pressure reclamation is a future separately specified policy

### O12 — Audit reclamation and close the workstream

**Satisfies:** PMOR-012 · **Verified by:** PMOAT-012

- [ ] Audit plan creation and execution with opaque candidate IDs/kinds/reasons/outcomes
- [ ] Record reclaimed bytes where known
- [ ] Sanitize secret-shaped failure data
- [ ] Do not add arbitrary host paths to audit output
- [ ] Add mixed-result redaction acceptance test
- [ ] Audit all PMOR/PMOAT citations and TODO checkboxes
- [ ] Verify no broad-prune fallback exists in source/comments/docs
- [ ] Run full schema/contract/docs drift checks
- [ ] Run complete Docker-backed suite with foreign sentinel present
- [ ] Revert-verify final acceptance coverage
- [ ] Mark this TODO complete only after exact-head CI passes

---

## Definition of done

- [ ] O1-O12 complete
- [ ] PMOR-001..PMOR-012 implemented
- [ ] PMOAT-001..PMOAT-012 automated and passing
- [ ] `resource_inventory` is bounded and mutation-free
- [ ] prune planning is dry-run and immutable/stale-aware
- [ ] execution requires explicit confirmation
- [ ] every destructive candidate has positive ownership proof and is revalidated
- [ ] no broad Docker/name-prefix prune fallback exists
- [ ] foreign container/volume sentinels survive destructive acceptance tests
- [ ] partial failures remain visible/durable/retryable
- [ ] size metrics distinguish measured/accounted/unavailable
- [ ] ENOSPC/PID pressure classification is evidence-based
- [ ] maintenance does not silently become broad automatic pruning
- [ ] reclamation audit is complete and redacted
- [ ] MCP schemas, `contract_version`, and docs/TOOLS are current
- [ ] Ruff format/lint and mypy pass
- [ ] focused suites pass
- [ ] full Docker-backed suite passes with no foreign resource modified
- [ ] each task has revert-verification evidence