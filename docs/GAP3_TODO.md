# Gap 3 TODO — Completion Record

**Spec:** `docs/GAP3_SPEC.md` (`G3R-001`..`G3R-017`, `G3AT-001`..`G3AT-017`)

**Source:** 2026-08-31 review of GAP2/current implementation.

**Status:** complete — reconciled 2026-09-02.

This file was originally the detailed Y1-Y17 implementation queue. The original task/subtask wording remains available in Git history. After Y17, most of those checkboxes were administratively stale even though the corresponding code, adversarial tests, and CI evidence had already landed. This reconciliation converts the file into the authoritative completion record rather than preserving a misleading mixture of completed implementation and unchecked historical boxes.

Three GAP2 tasks were reopened and are now explicitly corrected:

- **Y1 reopens X3** — the Bash sanitizer was correct, but its privileged Python launcher itself needed isolated startup. See `docs/GAP2_X3_CORRECTION.md`.
- **Y6 reopens X14** — mutable tag lookup is configuration lookup, not proof of the image backing an existing container. See `docs/GAP2_X14_CORRECTION.md`.
- **Y7 reopens X15** — best-effort post-delete job-log cleanup needed durable debt/retry/accounting. See `docs/GAP2_X15_CORRECTION.md`.

The final integrated GAP3 implementation/test tree, including the Y16 source-spec disposition closeout described below, passed CI run `33608760848` on exact implementation head `c8523d650dd94ec43b701178df7c7c34c197aa8e`: Docker image build, Ruff format/lint, mypy, MCP schema/contract, contract-version, docs/TOOLS drift, and **1,114 tests passed with 94% coverage**. Subsequent commits in the closeout PR add only reconciliation/correction/post-MVP documentation and receive their own final branch-head CI before merge.

---

## Completion matrix

### Phase 0 — Security

- [x] **Y1 — Reopen X3: start the privileged Python wrapper in isolated mode**  
  **G3R-001 / G3AT-001.** RED: `fc91dcc70c7a6c8d853704d580bf144a18aca0ad`. Fix: `f97702f788adf0af3aa37ca847d02e6216d9872d`. The shipped Docker `ENTRYPOINT` uses the absolute interpreter with `-I`; `tests/workspace/test_gap3_entrypoint_isolation.py` drives hostile `PYTHONPATH`/`sitecustomize.py` through the real image and verifies the final command remains non-root when `sudo: false`. Historical X3 wording is corrected in `docs/GAP2_X3_CORRECTION.md`.

- [x] **Y2 — Never signal an ssh-agent PID after restart without identity proof**  
  **G3R-002 / G3AT-002.** RED: `7811fa0b560e40a08194ea0c8c00115e3c72d054`. Fixes: `b49bcea4951a034a8565cf4e269735d5c4c662f7`, then restricted-procfs hardening `6ccd2db5c4c05b489a8910f700883f66539b3e95`. Persisted identity is verified before signaling; failure to prove identity does not signal the PID. `tests/workspace/test_gap3_ssh_agent_identity.py` deterministically substitutes an unrelated live process and proves it survives reconciliation.

- [x] **Y3 — Make `require_remote_protection: deny` fail closed**  
  **G3R-003 / G3AT-003.** RED: `f9ee01185afa4a69fdaa383a325dcececb5a0831`. Fix: `48114c830eac82e9bfb64b27d3e57de78de45c33` (format follow-up `015c308a73efbd06b5db6668c2cc1b518f604d46`). `deny` rejects both `missing` and `unverified` before key/container provisioning; `warn` remains permissive but diagnostic. The semantics are explicit in `docs/CONFIGURATION.md` and covered by the remote-protection acceptance suite.

- [x] **Y4 — Check SSH key unload and fail closed on credential cleanup**  
  **G3R-004 / G3AT-004.** PR #1, validated implementation head `ce05c60f08fbca9704239e4b6005a6d412d71e69`. `ssh-add -D` is checked; verified agent termination is the fallback; an unprovable cleanup becomes typed/degraded `SSH_CREDENTIAL_CLEANUP_FAILED` rather than a false clean result. The current integrated suite exercises the resulting cleanup semantics.

- [x] **Y5 — Roll back SSH credential provisioning when workspace open fails**  
  **G3R-005 / G3AT-005.** PR #2, implementation head `d34a234be27cb16a58e54e1de67f6cc73c30e4ca`. Agent/socket provisioning is separated from repository-key loading, key load occurs only after workspace/container establishment, and later open/setup failure rolls credentials back through Y4's checked cleanup path. Cleanup failure is surfaced rather than warning-and-forget; Y3's pre-provision deny ordering is preserved.

### Phase 1 — Correctness and durability

- [x] **Y6 — Reopen X14: derive image digest from the actual container**  
  **G3R-006 / G3AT-006.** PR #4, head `0cf8b664d4cd5dbbf91e009a38282d54f6bd158c`, CI `33436682335`. Runtime `image_digest` comes from Docker container-inspect `Image`; configured image reference is separate and there is no mutable-tag provenance fallback. Tests cover retag A→B, rebuild, tag removal, and fail-closed provenance. See `docs/GAP2_X14_CORRECTION.md`.

- [x] **Y7 — Reopen X15: make failed job-log cleanup durable and retryable**  
  **G3R-007 / G3AT-007.** PR #5, head `6837676252d0526ac3999611d9bd31bd53586b2c`; RED CI `33442065074`, GREEN CI `33445829666`. Migration 7→8 adds durable `job_log_cleanup` tombstones; pending bytes remain accounted, retry uses trusted state-root/validated IDs, and debt clears only after absence is proven. See `docs/GAP2_X15_CORRECTION.md`.

- [x] **Y8 — Tighten X18 so `list_files` bounds actual traversal work**  
  **G3R-008 / G3AT-008.** PR #6, head `28962405e5bf8aa29bd46c7cf584852a29da8e4e`; RED CI `33449305711`, GREEN CI `33464448697`. Recursive `Path.rglob` was replaced with explicit `os.scandir` traversal that prunes `.git` before descent, never follows directory symlinks, and enforces a candidate/work ceiling distinct from returned-entry count.

### Phase 2 — Explicit behavior and observability

- [x] **Y9 — Reject or explicitly report unimplemented project config**  
  **G3R-009 / G3AT-009.** PR #7, head `e15c94c7ccd0423a2714423f73d0955302e18e92`, GREEN CI `33474823551`. Supported fields are exhaustively classified; operational-looking but unimplemented `workspace`, project `github`, and `validation` fields are rejected, including explicit null, rather than silently accepted as no-ops.

- [x] **Y10 — Stop hiding unexpected global-hook configuration errors**  
  **G3R-010 / G3AT-010.** PR #9, head `900abedfcef1e12c5cfc2eb4f494ce7d6c22bfd3`, GREEN CI `33480301094`. Only Git's normal missing-key status is benign; malformed/unreadable config and Git execution errors remain failures with useful secret-safe diagnostics. `~/` expansion remains supported.

- [x] **Y11 — Decide same-name local/global hook composition explicitly**  
  **G3R-011 / G3AT-011.** PR #11, head `e3421edd7610233b5f1b3cabd9ee66ba7ff96755`, GREEN CI `33507422707` (**1,097 passed**). Repository-local pre-commit checks run first; only on success does the configured global pre-commit run; either failure blocks the commit. Real `git commit` acceptance tests cover order and both failure paths. `docs/GAP2_X7_CORRECTION.md` records the older scope correction.

- [x] **Y12 — Expose audit degradation as server health**  
  **G3R-012 / G3AT-012.** PR #13 (replacement for draft #12), head `f23a3a8607129c14d98fb2ba92051ed9927ac54f`; RED CI `33511981294` (**1 failed, 1,097 passed**), GREEN CI `33518340588` (**1,098 passed**). Audit remains best-effort for completed operations, but health becomes `degraded` with redacted last-failure information and recovers to healthy after a successful write.

- [x] **Y13 — Clean atomic-write temp files after `os.replace` failure**  
  **G3R-013 / G3AT-013.** PR #15 head `79fddb8bca113f869e414139b1d9af0bd57ac48a`, GREEN CI `33535809314` (**1,100 passed**); follow-up exception-precedence hardening PR #17 head `ea3c5605cf32111ce7ccd18a9045092c47a31d80`, GREEN CI `33541281288` (**1,101 passed**). Pre-replacement failures best-effort unlink the validated-parent-relative temp file; cleanup cannot mask the primary write/replace error.

### Phase 3 — Operational and test integrity

- [x] **Y14 — Add a real maintenance scheduler**  
  **G3R-014 / G3AT-014.** PR #19 (replacement for draft #18), head `809559d995c7c788eb2b9c35bdde015748cc309e`, GREEN CI `33554282769` (**1,104 passed**). MCP lifespan owns a periodic scheduler that runs idle sweep plus retention/Y7 cleanup retries without client traffic, isolates failures, exposes health, shuts down cleanly, and offloads blocking Docker/filesystem work from the asyncio event loop.

- [x] **Y15 — Namespace Docker resources per test run**  
  **G3R-015 / G3AT-015.** PR #20, head `59b4fa56d48b662aaa684da1904591a1d2674143`; RED CI `33573716378` (**2 failed, 1,104 passed**) proved container/cache collisions, GREEN CI `33576044517`. The test-only namespace covers physical container names, ownership labels, cache-volume names, and reconciliation while production naming remains unchanged.

- [x] **Y16 — Close acceptance-test citation/coverage debt**  
  **G3R-016 / G3AT-016.** PR #22 (replacement for draft #21), head `286e6cda10cfc5fd05decaf033a9283bb81a9855`; RED CI `33585445211`, GREEN CI `33589165788` (**1,111 passed, 94% coverage**). Blanket `known_uncited` handling was removed; all acceptance series including G3AT are audited; requirement-named coverage maps to exact real pytest nodes; mapped-node deletion reopens debt; genuine behavioral gaps received tests.

  Final source-spec residual: commit `f78328cfd9b558703d6db8ee2b488878960da60c` removed the remaining private `_DISPOSITIONS` dictionary. CI `33605872106` was the intended RED (**1 failed, 1,112 passed**) because HAT-002 had no formal source-spec disposition. `docs/GAP3_ACCEPTANCE_COVERAGE_SPEC.md` now carries the four exceptional records (HAT-002, RAT-001, RAT-002, GAT-001) as normative, reasoned, owned specification state. The first addendum-only attempt remained correctly red in CI `33607194448` because the audit had not yet consumed the addendum. Commit `c8523d650dd94ec43b701178df7c7c34c197aa8e` added exact `PREFIX-NNN` parsing, duplicate/nonexistent/malformed/stale checks, and a cross-series split regression. CI `33608760848` passed every gate with **1,114 passed, 94% coverage**. There is no private acceptance-disposition allowlist left in the audit.

- [x] **Y17 — Correct stale security claims in touched source comments**  
  **G3R-017 / G3AT-017.** PR #24 (replacement for draft #23), head `0b6f1627d5ba1068e259d707aa42cf10e4303f68`; RED CI `33592001972` (**2 failed, 1,110 passed**), GREEN CI `33592923207` (**1,112 passed, 94% coverage**). X3/Y1 comments identify Python isolation as load-bearing; X14/Y6 comments use container identity, not tag lookup, as runtime provenance; Y7/Y14 comments describe durable cleanup debt and the real scheduler rather than obsolete future claims.

---

## GAP2 reopened-task corrections

- [x] X3 correction: `docs/GAP2_X3_CORRECTION.md` points the stronger interpreter-startup invariant to GAP3 Y1 / G3R-001 / G3AT-001.
- [x] X14 correction: `docs/GAP2_X14_CORRECTION.md` points immutable runtime-image provenance to GAP3 Y6 / G3R-006 / G3AT-006.
- [x] X15 correction: `docs/GAP2_X15_CORRECTION.md` points durable post-delete job-log cleanup to GAP3 Y7 / G3R-007 / G3AT-007 and notes Y14's lifecycle retry.

---

## Definition of done

- [x] Y1-Y17 complete
- [x] G3AT-001..G3AT-017 automated and passing
- [x] GAP2 X3, X14, X15 contain correction records pointing to GAP3
- [x] Remote-protection `deny` is fail-closed on `unverified`
- [x] No persisted ssh-agent PID is signaled after restart without identity proof
- [x] Failed SSH key unload cannot be silently reported as clean
- [x] Failed workspace opening does not unnecessarily retain a repository key
- [x] Runtime image digest comes from the actual container image
- [x] Failed post-delete log cleanup remains durable, retryable, and accounted
- [x] Recursive listing prunes excluded subtrees and has an actual work bound
- [x] Unimplemented operational config cannot silently no-op
- [x] Hook dispatcher cannot hide unexpected Git config failure
- [x] Same-name local/global hook behavior is explicit and tested
- [x] Audit failures appear in server health
- [x] Atomic write replacement failures leave no temp file when cleanup succeeds; secondary cleanup errors do not mask the primary error
- [x] Maintenance runs without client traffic
- [x] Concurrent Docker-backed test contexts are namespaced and cannot adopt/delete each other's managed resources
- [x] Acceptance coverage audit has no unexplained permanent gaps and no private disposition allowlist
- [x] Security comments touched by this pass describe current, test-backed invariants
- [x] Ruff format check passes
- [x] Ruff lint passes
- [x] mypy passes
- [x] Targeted task acceptance suites pass
- [x] Full Docker-backed suite passes with no GAP3 Docker acceptance skipped; current integrated closeout run `33608760848` is **1,114 passed**
- [x] MCP schema export, contract-version, and docs/TOOLS drift gates pass
- [x] Task histories retain adversarial RED/revert evidence demonstrating the acceptance controls fail against their pre-fix mechanisms rather than passing vacuously

GAP3 is therefore closed. The next proposed implementation queue is `docs/POST_MVP_OPERATIONS_TODO.md`, governed by `docs/POST_MVP_OPERATIONS_SPEC.md`.
