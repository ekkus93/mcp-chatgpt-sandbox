# Post-MVP Operations Spec — Resource Visibility and Safe Reclamation

**Status:** complete — O1-O12 implemented and verified
**Date:** 2026-09-02
**Depends on:** completed GAP3 (`docs/GAP3_SPEC.md`, `docs/GAP3_TODO.md`)
**Requirement series:** `PMOR-001`..`PMOR-012`
**Acceptance series:** `PMOAT-001`..`PMOAT-012`

## 1. Purpose

The MVP now has substantially stronger lifecycle, cleanup-debt, audit-health, maintenance, and Docker-test isolation semantics. The next useful feature set is operational visibility and safe resource reclamation: an operator should be able to answer **what this daemon owns, what is consuming resources, what cleanup is pending, and what can be safely reclaimed** without dropping to ad-hoc Docker/host commands.

This workstream deliberately starts with observation and proof. Destructive cleanup is added only after the daemon can construct a trustworthy inventory and prove ownership of every candidate.

The governing rule is:

> **No resource is deleted merely because its name looks familiar. Every destructive candidate must have positive MCP ownership evidence, a current lifecycle reason for deletion, and an explicit operator-authorized plan.**

There is no broad best-effort fallback such as `docker system prune`, wildcard volume deletion, unscoped filesystem traversal, or "delete anything with this prefix".

## 2. Goals

1. Expose a bounded, read-only inventory of MCP-managed runtime resources.
2. Report disk/resource pressure with explicit provenance and unavailable/degraded states.
3. Make existing durable cleanup debt easy to inspect.
4. Add a dry-run-first reclamation workflow whose plan is inspectable before deletion.
5. Make destructive execution race-aware, ownership-checked again at execution time, and idempotent.
6. Preserve partial cleanup failures as visible retry/debt state rather than returning false success.
7. Integrate operational health with the lifecycle maintenance system without silently enabling aggressive automatic pruning.
8. Keep foreign Docker resources and unrelated host files outside the ownership boundary even when their names resemble MCP resources.

## 3. Non-goals

This workstream does **not** include:

- generic Docker host administration;
- `docker system prune` or equivalent broad cleanup;
- automatic deletion of healthy project caches merely because they are old;
- automatic deletion of Docker images not proven to be MCP-owned and unused;
- host package cleanup;
- repository advisory locks;
- Dev Container (`devcontainer.json`) compatibility;
- Kubernetes/remote-Docker orchestration;
- arbitrary host filesystem browsing;
- background self-healing that hides resource failures from the operator.

Future work may add opt-in policy-driven reclamation, but only after the explicit planning/execution path below has accumulated evidence.

## 4. Resource model

The inventory SHALL classify resources by both **kind** and **ownership/lifecycle state**.

Initial resource kinds:

- workspace containers;
- MCP cache volumes;
- job-log directories and their recorded byte counts;
- durable `job_log_cleanup` debt;
- per-workspace ssh-agent tracking/socket state where operationally relevant;
- daemon state storage usage needed to diagnose state-disk pressure.

Docker images may be reported as referenced runtime/configuration identities, but **image deletion is out of scope for the first reclamation implementation**.

Ownership/lifecycle states SHALL distinguish at least:

- `active` — referenced by current durable state and still needed;
- `retained` — intentionally preserved but not currently active;
- `cleanup_pending` — durable cleanup debt already exists;
- `orphaned_owned` — positively proven MCP-owned but no longer referenced by valid durable state;
- `foreign` — not MCP-owned; report only when needed to explain a collision, never offer as a deletion candidate;
- `unknown`/`degraded` — ownership or state cannot be proven; never destructive.

## 5. Ownership proof

### PMOR-001 — Inventory SHALL use positive ownership proof

A resource SHALL NOT become a prune candidate from a name prefix alone.

For Docker containers/volumes, ownership proof SHALL use the daemon's managed labels and, where applicable, correspondence with durable workspace/run/cache state. Test namespaces from GAP3 Y15 remain part of ownership identity and SHALL prevent cross-test adoption or deletion.

For job-log cleanup, paths SHALL be reconstructed from trusted daemon state (`state_dir` plus validated IDs), preserving GAP3 Y7. Persisted arbitrary filesystem paths SHALL NOT be accepted as deletion authority.

If proof is incomplete, classify the item `unknown`/`foreign`; do not guess.

### PMOR-002 — Inventory SHALL be bounded and side-effect free

The read-only inventory operation SHALL:

- perform no deletion, stop, kill, image pull, cache mutation, or retention mutation;
- have explicit result/work bounds;
- expose truncation/incomplete state rather than silently omitting excess resources;
- isolate per-resource inspection failures so one unreadable item does not erase the rest of the report;
- surface those failures as sanitized diagnostics.

The inventory SHALL NOT recursively walk arbitrary repository contents.

### PMOR-003 — Operational size accounting SHALL state its provenance

Reported byte counts SHALL distinguish measured values from database/accounting hints.

At minimum:

- job-log accounted bytes use the existing durable counters/debt accounting;
- host state-directory filesystem capacity/free-space comes from the filesystem containing the configured state database/state directory;
- Docker-volume sizes, if measured, SHALL be explicitly marked measured/estimated/unavailable and bounded in work;
- unavailable metrics SHALL never be represented as zero.

## 6. Pressure diagnostics

### PMOR-004 — Disk-pressure diagnostics SHALL distinguish failure domains

When enough evidence exists, diagnostics SHALL distinguish at least:

- daemon state filesystem pressure/full condition;
- Docker daemon/storage failure indicating no space;
- workspace/container filesystem ENOSPC;
- quota/permission/other I/O failures that are **not** proven ENOSPC.

Do not reclassify arbitrary `OSError`, Docker errors, or command failures as disk exhaustion based only on a vague message substring. Preserve the original structured failure and add pressure evidence as diagnostic context.

### PMOR-005 — PID/process pressure SHALL be observable without inventing portability

On Linux, expose bounded read-only process/PID pressure when available (for example current process count/cgroup pids usage and relevant limit). The implementation SHALL document the exact kernel/cgroup sources it trusts.

If a metric is unavailable because the platform, procfs, cgroup layout, or permissions do not support it, report `unavailable` with a sanitized reason. Do not silently emit zero and do not make PID-pressure observation a prerequisite for normal workspace operation.

## 7. Reclamation planning

### PMOR-006 — Reclamation SHALL be plan-first and dry-run by default

The destructive workflow SHALL separate **candidate planning** from **execution**.

A plan SHALL enumerate each candidate with:

- stable candidate identifier;
- resource kind;
- MCP ownership evidence;
- reason it is reclaimable;
- relevant lifecycle/state preconditions;
- estimated/accounted bytes where known;
- actions that execution would attempt.

The initial tool/API SHALL default to dry-run. Merely requesting a plan SHALL have no destructive side effects.

### PMOR-007 — A prune plan SHALL be immutable and stale-aware

Execution SHALL require a plan token/identifier bound to the exact candidate set and material preconditions used to build it. It SHALL NOT silently recompute a broader candidate set when executing an old plan.

Immediately before each destructive action, re-check ownership and the lifecycle precondition. If a container/cache became active, a label changed, a durable reference reappeared, or identity can no longer be proven, skip/fail that candidate rather than deleting it.

A stale plan may be partially executable only when each surviving candidate is independently revalidated and the response makes skipped/stale items explicit.

### PMOR-008 — Destructive execution SHALL require explicit authorization

A caller SHALL explicitly request execution of a previously returned plan. The interface SHALL make dry-run and execute modes unambiguous; omission of an execution/confirmation field SHALL never mean "yes".

There SHALL be no broad fallback deletion when a candidate-specific operation fails.

## 8. Reclamation execution and durability

### PMOR-009 — Deletion SHALL be candidate-scoped and foreign-safe

Execution SHALL operate only on resources enumerated in the authorized plan and revalidated under PMOR-007.

Forbidden implementation shortcuts include:

- `docker system prune`;
- deleting all volumes/containers with an `mcp-*` name prefix;
- host-wide deletion by label without cross-checking the plan candidate;
- deleting a filesystem directory from an untrusted persisted path.

A foreign sentinel whose name resembles an MCP resource SHALL survive both planning and execution.

### PMOR-010 — Partial failures SHALL remain visible and retryable

Execution SHALL return per-candidate outcome rather than collapsing a mixed result into `success`.

If a primary MCP resource has already been removed but secondary filesystem cleanup fails, preserve or create durable cleanup debt where the resource model supports it. Do not resurrect deleted workspace metadata solely to make cleanup look atomic, and do not lose the linkage needed for retry.

Repeated execution SHALL be idempotent: an already-proven-absent target is a successful no-op; unknown ownership or an unproven deletion is not.

## 9. Maintenance and health integration

### PMOR-011 — Automatic maintenance SHALL remain conservative

The lifecycle scheduler may continue retrying cleanup debt that is already authorized by existing lifecycle semantics (for example GAP3 Y7 job-log debt).

This workstream SHALL NOT automatically prune healthy retained caches, stopped-but-retained workspaces, or images merely because the inventory can see them. Any future automatic reclamation policy requires a separate explicit configuration contract, conservative defaults, and its own acceptance criteria.

`server_info` or an equivalent diagnostic surface SHOULD summarize operational pressure/degraded inventory state without requiring the operator to run destructive tooling.

## 10. Audit and contract behavior

### PMOR-012 — Reclamation decisions SHALL be auditable without leaking host details

Plan creation and execution SHALL emit audit events sufficient to reconstruct:

- who/which MCP request created/executed a plan;
- plan identifier;
- candidate kinds/opaque IDs;
- candidate decisions and outcomes;
- bytes reclaimed where known;
- sanitized failure classifications.

Audit output SHALL NOT newly expose arbitrary host filesystem paths, secrets, Docker credentials, SSH key material, or raw environment values.

Any new MCP tool or response field requires schema regeneration and a `contract_version` bump under the existing contract rules.

---

## 11. Proposed MCP surface

Names are provisional until implementation, but the intended separation is:

### `resource_inventory`

Read-only. Returns bounded ownership/lifecycle inventory, cleanup debt, resource-pressure observations, and explicit degraded/unavailable metrics.

### `resource_prune_plan`

Read-only/dry-run. Accepts narrow filters such as eligible resource kinds and optional repository/workspace scope. Returns an immutable plan token plus candidate details. No deletion.

### `resource_prune_execute`

Destructive. Requires a plan token and explicit confirmation. Revalidates every candidate and returns per-candidate results.

Keeping plan and execute separate is intentional. A single `prune(dry_run=false)` call makes it too easy for a default, client bug, or stale model assumption to cross the destructive boundary.

---

## 12. Acceptance tests

Each SHALL be automated. Real-Docker cases SHALL use GAP3 Y15 resource namespaces and SHALL leave a deliberately foreign sentinel untouched.

### PMOAT-001 — Foreign lookalikes never become owned

Create MCP-owned and foreign containers/volumes with deliberately similar names. Inventory classifies only positively labeled/linked resources as owned, and the foreign resources never appear as prune candidates.

### PMOAT-002 — Inventory is side-effect free and bounded

Instrument Docker/filesystem operations. A large resource set reaches the documented work/result bound, reports truncation/incomplete state, and performs zero stop/delete/remove actions.

### PMOAT-003 — Size/pressure metrics never fabricate zero

Force unavailable filesystem/Docker size probes. Output distinguishes accounted/measured/unavailable values and never converts probe failure to `0 bytes` or `0%` pressure.

### PMOAT-004 — ENOSPC domains remain distinct

Inject state-filesystem ENOSPC, Docker-storage no-space failure, container-command ENOSPC, and a non-space I/O failure. Each preserves its primary structured error and receives only the pressure classification supported by evidence.

### PMOAT-005 — PID pressure is explicit or unavailable

Exercise the supported Linux metric source and a forced unavailable source. Both produce typed diagnostic state; unavailable does not become zero and does not break ordinary workspace operations.

### PMOAT-006 — Planning is non-destructive

Create eligible owned orphan/cleanup resources, request a plan, and prove all resources still exist afterward. The plan contains reason, ownership evidence, preconditions, and a stable plan token.

### PMOAT-007 — Stale plans fail closed per candidate

Create a plan, then make one candidate active or alter its identity before execution. Execution refuses that candidate while independently revalidating other candidates; it never silently expands/recomputes the plan.

### PMOAT-008 — Execution requires explicit confirmation

Omit/false-set confirmation and prove no resource changes. Execute the same plan with explicit authorization and prove only the validated candidates are touched.

### PMOAT-009 — Foreign sentinel survives real prune execution

Against a real Docker daemon, execute a plan containing MCP-owned disposable resources while a foreign lookalike container and volume exist. Owned candidates are reclaimed; both foreign sentinels remain byte/resource-identical and running/present as applicable.

### PMOAT-010 — Partial cleanup failure is durable and retryable

Force one candidate's secondary cleanup to fail after primary deletion. The response is mixed/degraded rather than blanket success, durable cleanup debt remains discoverable, a later retry clears it, and accounting reflects the pending bytes until proven gone.

### PMOAT-011 — Maintenance does not become an implicit broad prune

Run multiple scheduler cycles with healthy retained caches/workspaces plus genuine existing cleanup debt. Only already-authorized cleanup debt is retried; healthy retained resources remain untouched.

### PMOAT-012 — Reclamation audit is complete and redacted

Plan and execute a mixed-result reclamation containing a secret-shaped failure string and host-path-shaped detail. Audit records plan/candidate decisions and outcomes while redacting secrets and not exposing arbitrary host paths.

---

## 13. Definition of done

This workstream is complete only when:

- PMOR-001..PMOR-012 are implemented;
- PMOAT-001..PMOAT-012 are automated and passing;
- inventory is bounded and read-only;
- destructive cleanup requires an immutable plan plus explicit authorization;
- every destructive candidate is positively owned and revalidated at execution;
- no broad prune/name-prefix fallback exists;
- foreign Docker sentinels survive full acceptance execution;
- partial cleanup failure is visible/durable/retryable;
- pressure diagnostics distinguish evidence from unavailable/unknown state;
- maintenance remains conservative by default;
- audit/schema/contract documentation is current;
- Ruff, mypy, contract/schema/docs drift gates pass;
- the complete Docker-backed test suite passes without modifying foreign resources.
