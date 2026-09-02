# Post-MVP Operations TODO — Resource Visibility and Safe Reclamation

**Spec:** `docs/POST_MVP_OPERATIONS_SPEC.md` (`PMOR-001`..`PMOR-012`, `PMOAT-001`..`PMOAT-012`)

**Status:** open — O1 complete; O2-O12 remain

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

- [ ] Add an internal inventory service independent of MCP schema first
- [ ] Enumerate managed containers, cache volumes, job-log accounting/debt, and state-storage observations
- [ ] Add explicit result/work bounds
- [ ] Report truncation/incomplete state rather than silently dropping excess resources
- [ ] Isolate per-resource inspection failure and preserve sanitized diagnostics
- [ ] Distinguish measured, accounted/estimated, and unavailable byte values
- [ ] Never convert an unavailable measurement into zero
- [ ] Prove inventory performs no Docker/filesystem mutation
- [ ] Add unit/fake-Docker tests before exposing a tool

### O3 — Expose `resource_inventory` through MCP

**Satisfies:** PMOR-001..PMOR-003 · **Verified by:** PMOAT-001..PMOAT-003

- [ ] Define bounded Pydantic input/output models
- [ ] Keep default operation global/read-only with optional narrow repository/workspace filtering
- [ ] Do not expose arbitrary host filesystem paths
- [ ] Include cleanup-debt summaries and explicit degraded/unavailable observations
- [ ] Add tool registration and docs/TOOLS.md row
- [ ] Bump `contract_version`
- [ ] Regenerate `schemas/tools.json`
- [ ] Run contract-version and docs-drift checks
- [ ] Exercise through the real MCP tool surface

### O4 — Add evidence-based disk and PID-pressure diagnostics

**Satisfies:** PMOR-004, PMOR-005 · **Verified by:** PMOAT-004, PMOAT-005

- [ ] Define diagnostic states for state-filesystem pressure, Docker-storage no-space, container ENOSPC, and non-space I/O failure
- [ ] Preserve original structured errors while attaching only evidence-supported pressure context
- [ ] Avoid broad message-substring classification where errno/API evidence exists
- [ ] Define Linux PID/process pressure sources and document procfs/cgroup assumptions
- [ ] Report unsupported/unavailable metrics explicitly rather than zero
- [ ] Ensure unavailable PID metrics do not block ordinary workspace operation
- [ ] Add forced state ENOSPC, Docker no-space, container ENOSPC, and non-space I/O cases
- [ ] Add supported/unavailable PID-pressure cases

---

## Phase 1 — Plan boundary

### O5 — Build deterministic prune candidates from inventory

**Satisfies:** PMOR-006 · **Verified by:** PMOAT-006

- [ ] Define which lifecycle states are eligible for explicit reclamation in this workstream
- [ ] Exclude `active`, `foreign`, and `unknown/degraded` resources
- [ ] Produce per-candidate reason, ownership evidence, lifecycle preconditions, and expected action
- [ ] Include accounted/estimated bytes where known without fabricating measurements
- [ ] Keep Docker image deletion out of initial candidates
- [ ] Prove candidate construction is read-only
- [ ] Prove healthy retained resources are not accidentally eligible unless the spec explicitly permits that kind

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
