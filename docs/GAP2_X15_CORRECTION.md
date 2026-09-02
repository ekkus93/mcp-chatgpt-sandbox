# GAP2 X15 cleanup-durability correction

GAP2 X15 preserved the important RR-012 rule that workspace deletion is defined by workspace/container metadata removal rather than by every secondary cleanup action succeeding. Its historical completion claim was nevertheless too strong about what happened to job-log cleanup after the primary delete completed.

Before GAP3 Y7, job rows could be deleted and the subsequent best-effort log-directory removal could fail. Once the rows were gone, the daemon had discarded the only durable linkage needed to find and retry that directory. A warning was emitted, but the cleanup debt itself was forgotten and its bytes could escape retention accounting.

GAP3 Y7 / G3R-007 / G3AT-007 closes that residual without weakening RR-012. Migration 7->8 adds durable `job_log_cleanup` tombstones created in the same SQLite transaction that deletes job metadata. Maintenance reconstructs only the trusted `<state_dir>/jobs/<validated job_id>` path, accounts pending cleanup bytes against retention pressure, retries failed deletion, records sanitized failure state, and removes the tombstone only after absence is proven. Workspace deletion may therefore finish while secondary cleanup remains pending, but that pending work is durable, visible, retryable, and accounted rather than warning-and-forget.

PR #5 implemented the GAP3 correction. GAP3 Y14 subsequently placed the same retry path on the lifecycle-owned maintenance scheduler, so retry no longer depends on later client traffic.

This file corrects X15's historical cleanup claim. The underlying RR-012 deletion semantics remain intentional; GAP3 adds the durable deferred-cleanup state that X15 did not originally preserve.
