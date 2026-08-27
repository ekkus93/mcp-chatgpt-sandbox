# Development Workspace MCP Server — Hardening TODO

**Source of truth:** `docs/HARDENING_SPEC.md` (HR/HAT references point into it),
which in turn defers to `docs/SPEC.md` for everything it does not address.
**Status:** not started
**Origin:** security and correctness review of 2026-08-27 against `214c095`
**Convention:** tasks are `H<n>`. Each names the hardening requirements it
implements and the acceptance tests that prove it. One task per commit.

A task is done when its subtasks are checked, its acceptance tests pass, the
full suite passes, and `schemas/tools.json` has been re-exported if the task
changed a tool contract (§33.2 bump rules still apply).

---

## Decisions

Made 2026-08-27, before work started.

- [x] **HD1 — Host-side Git reads are retained and hardened in place.**
      Rather than moving `git_status`/`git_diff`/`git_log` into the container.
      Preserves §20.1's speed exception and §73's degraded start, at the cost of
      an ongoing control surface that must be maintained (`HARDENING_SPEC.md`
      §4.1). The alternative — eliminating host-side execution entirely — is
      recorded as **H36**, deferred, not rejected.
- [x] **HD2 — `docs/SPEC.md` is amended, not merely superseded.** Three of the
      six critical defects exist because the specification itself is wrong.
      `HARDENING_SPEC.md` §9 lists sixteen amendments; **H34** applies them.
- [x] **HD3 — Unsafe defaults are changed outright.** Pre-1.0 private server, so
      churn is cheap and a safe out-of-box state is worth more than
      compatibility. Affects `exec_output`'s response cap, project-config
      `environment:` handling, and `delete_file`'s root behavior.

---

## Ordering

**H1 comes first and is not optional.** The current test teardown removes every
container carrying the daemon's production label and every `mcp-cache-` volume
on the host. Until it is scoped, running the suite on this machine destroys
live workspaces and every dependency cache — including while validating any
other task here.

After H1, phases 1–3 (the six critical defects) should land before anything
else. Phases 4–7 are independent of each other and can be reordered freely.
Phases 8–9 are cleanup and can trail.

---

## Phase 0 — Make it safe to run the tests

### H1 — Scope test teardown to test-created resources
**Satisfies:** HR-026 · **Verified by:** HAT-020

- [ ] Label every container the suite creates with a test-run-specific label
      (session id or PID), and filter teardown on that label instead of the
      production `dev-workspace-mcp.managed` label
- [ ] Name cache volumes created by tests with a test-run-specific prefix, and
      filter volume teardown on it instead of the global `mcp-cache-` prefix
- [ ] Correct the fixture docstring, which currently claims "nothing else on the
      host's Docker daemon is touched"
- [ ] Add a sentinel container and sentinel volume, both carrying production
      identifiers, and assert a full suite run leaves them intact
- [ ] Make concurrent suite runs non-destructive to each other (deterministic
      container names currently collide; give test workspaces unique repository
      ids or a run-scoped suffix)

**Why first:** `tests/workspace/conftest.py:61–70`. This is the one finding that
makes the act of verifying the others dangerous.

---

## Phase 1 — Host escape

### H2 — Revision parameters cannot inject Git options
**Satisfies:** HR-001 · **Verified by:** HAT-001

- [ ] Reject `git_diff`'s `base`/`head` and `git_log`'s `ref` when the value
      begins with `-`, with `INVALID_ARGUMENT`
- [ ] Additionally place revision arguments after `--end-of-options`, or
      validate through `git rev-parse --verify --quiet --end-of-options`
- [ ] Audit every other argv built from client input for the same class
- [ ] Test through the MCP tool layer, not just the internal function — the
      published contract is what an attacker holds

**Defect:** `git/diff.py:74`, `git/log.py:55`. Reproduced end-to-end: a real
`git_log` call with `ref="--output=<path>"` wrote a host file and returned
`{'result': []}`.

### H3 — Host-side reads execute no repository-supplied code
**Satisfies:** HR-002 · **Verified by:** HAT-002

- [ ] Add `--no-ext-diff --no-textconv` to diff-producing reads
- [ ] Enumerate the repository's own config and pass `-c <key>=` for every
      code-executing key (`HARDENING_SPEC.md` §4.1), as a named module-level
      control with a comment stating it must track Git
- [ ] Run host-side reads with `GIT_CONFIG_GLOBAL=/dev/null` and
      `GIT_CONFIG_SYSTEM=/dev/null`
- [ ] Strip `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`,
      `GIT_SEQUENCE_EDITOR`, `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_ASKPASS`,
      `GIT_PROXY_COMMAND`, `GIT_TEMPLATE_DIR` from the inherited environment
- [ ] Cover `git_status`, `git_diff`, `git_log` **and** `workspace_info` —
      `read_basic_git_state` reaches the same code path
- [ ] Assert each read still returns correct results; a read that fails is not a
      pass

**Defect:** `git/service.py:57–75`. Verified against git 2.43.0 during the
review, and these results constrain the implementation:

- `-c diff.external=` is **not** a fix — it blocks execution but aborts the read
  with `fatal: external diff died`. Use `--no-ext-diff`.
- `--no-ext-diff` does **not** cover `textconv`; `--no-textconv` is also needed.
- `core.attributesFile=/dev/null` does **not** help — the in-tree
  `.gitattributes` is what binds the driver.
- `git status` executes `filter.*.clean`, so this is not diff-only.
- Driver names are attacker-chosen, so static overrides cannot work — enumerate
  what is configured, which is complete because an undefined driver cannot run.

---

## Phase 2 — Data destruction

### H4 — The repository root is not a deletable target
**Satisfies:** HR-003 · **Verified by:** HAT-003

- [ ] Refuse a `delete_file` path resolving to the repository root, regardless
      of `recursive`
- [ ] Refuse recursive descent into any directory containing a `.git` entry
      (nested repo, submodule, vendored checkout)
- [ ] Cover `""`, `"."`, `"./"`, an absolute path to the root, and a traversal
      that lands on it

**Defect:** `filesystem/service.py:201–215`. Reproduced: working tree and `.git`
both removed, tool returned `{'deleted': True}`. Nothing locally recoverable.

### H5 — A truncated read never becomes a write
**Satisfies:** HR-004, HR-005 · **Verified by:** HAT-004

- [ ] `patch_file` fails with `INVALID_ARGUMENT` when its read was truncated
- [ ] `patch_file` passes the sha256 it read through as the write's
      `expected_sha256`, closing its own read/write window
- [ ] Enforce `filesystem.max_patch_bytes` on the incoming diff
- [ ] Audit for other read-bounded/write-whole pairs

**Defect:** `tools/filesystem.py:187–219`. Reproduced: 1950 of 2000 bytes
destroyed, reported as `{'applied': True}` with a fresh valid sha256.

---

## Phase 3 — Configuration boundary

### H6 — Project configuration cannot set daemon-controlled variables
**Satisfies:** HR-006 · **Verified by:** HAT-005

- [ ] Reject `SSH_AUTH_SOCK` and any `MCP_*` key in `.mcp-workspace.yml`'s
      `environment:`, naming the rejected key in the error
- [ ] Independently, merge daemon-controlled variables **last** in
      `build_workspace_run_kwargs` so ordering alone cannot override them
- [ ] Keep both controls — neither is sufficient alone
- [ ] Assert PID 1 does not run as root on a daemon without userns remapping

**Defect:** `container_config.py:100–112`, `tools/workspace.py:351`. The §5.3
refusal at line 89 inspects only `container_config.user`. Reproduced: root PID 1
and a setuid-root host binary via the bind mount.

### H7 — The §19.1 key invariant covers every key path
**Satisfies:** HR-007 · **Verified by:** HAT-006

- [ ] Extend `_check_ssh_key_not_under_host_ssh` to `ssh.shared_key`
- [ ] Restructure so a new key-bearing config field that is not checked fails a
      test rather than passing silently
- [ ] Cover all three paths in tests

**Defect:** `config/loader.py:74–94`. Reproduced: `shared_key:
"../fakehome/.ssh/id_ed25519"` accepted, resolving to the host user's personal
key. FR-025 says this "SHALL NOT be relaxed by policy or configuration."

### H8 — The entrypoint re-validates cache paths
**Satisfies:** HR-008 · **Verified by:** HAT-007

- [ ] Reject absolute, `..`-containing, and empty `MCP_CACHE_PATHS` entries in
      `entrypoint.sh` before `mkdir`/`chown`
- [ ] Reject `:` in a cache path host-side (it is the separator)
- [ ] Keep host-side validation too — both sides of the boundary

**Defect:** reproduced — `'../../etc:../../usr/bin'` left `/etc` and `/usr/bin`
owned by the container user.

---

## Phase 4 — Path containment

### H9 — File access is race-free
**Satisfies:** HR-011 · **Verified by:** HAT-009

- [ ] Open files with `O_NOFOLLOW` relative to a directory fd pinned at
      validation time, or re-verify the opened inode with `fstat`
- [ ] Apply to read, write, patch, and delete paths
- [ ] Test with a sustained concurrent flipper, not a single attempt

**Defect:** `filesystem/security.py:69–80` validates by path;
`service.py:132,196` re-open by path. Reproduced: a host private key returned on
iteration 1; an escaping write within 24.

### H10 — An escaping symlink does not break enumeration
**Satisfies:** HR-012 · **Verified by:** HAT-010

- [ ] Catch `PathOutsideRepositoryError` alongside `PathGitMetadataError` in
      `list_files` and `search_files`
- [ ] Report escaping and dangling symlinks as entries, as `_describe`'s
      docstring already promises
- [ ] Test with a `.venv/bin/python`-style link, the common real case

---

## Phase 5 — Integrity of reported guarantees

### H11 — Error messages are redacted
**Satisfies:** HR-013 · **Verified by:** HAT-011

- [ ] Redact `payload["message"]`, not only `details`
- [ ] Fix redaction of list values (the key hint is currently dropped),
      non-string scalars, and `scheme://user:pass@host` userinfo
- [ ] Cover the YAML-echo path — a malformed `.mcp-workspace.yml` currently
      echoes the offending source line verbatim

**Defect:** `errors.py:248–259, 273–280`. Reproduced: a `ghp_` token in
cleartext in `message` while `details.url` was correctly `[REDACTED]`.

### H12 — The validated push destination is the one used
**Satisfies:** HR-014 · **Verified by:** HAT-012

- [ ] Resolve with `git remote get-url --push`, or push to the validated URL
      rather than the remote name
- [ ] Validate both URLs where fetch and push differ
- [ ] Assert the audit record names the destination actually used

**Defect:** `git/remote.py:48–65`, `git/push.py:96–100`. Reproduced: validation
approved `owner/repo` while the commit landed elsewhere and the audit record
named the wrong destination.

### H13 — Denials are audited at §20.7 fidelity
**Satisfies:** HR-015

- [ ] Include the policy key from `PolicyDeniedError.details["policy"]`
- [ ] Include `repository_id`, and `remote`/`branch`/`remote_repository` where
      applicable
- [ ] Add `refspec` and `remote_protection_status` to `git_push` success records
- [ ] Do not let an unwritable audit path replace a denial with a crash

### H14 — Authorization is re-checked against live configuration
**Satisfies:** HR-016 · **Verified by:** HAT-013

- [ ] Validate the resolved repository against the live `RepositoryRegistry` on
      every workspace-scoped call
- [ ] Test that removing a repository from config immediately revokes access
      through its stale `workspace_id`

---

## Phase 6 — Recoverability

### H15 — A workspace recovers from any Docker state
**Satisfies:** HR-017 · **Verified by:** HAT-014

- [ ] Create a container for an existing row whose container is missing
- [ ] Adopt-or-remove a container found by deterministic name with no matching
      row, on the create path and not only in `reconcile()`
- [ ] Cover: removed out of band; failed create; orphaned name collision

**Defect:** `workspace/manager.py:246–261, 509–516`. Currently a transient
failure wedges the workspace permanently, even after Docker recovers.

### H16 — Deleting a workspace that ran jobs succeeds
**Satisfies:** HR-018 · **Verified by:** HAT-015

- [ ] Cascade `job.workspace_id`, or delete dependent rows in order inside one
      transaction
- [ ] Ensure a failure cannot leave the container removed and the row in
      `deleting`

### H17 — Migrations are atomic
**Satisfies:** HR-019 · **Verified by:** HAT-016

- [ ] Wrap migrations in explicit `BEGIN`/`COMMIT` (`with connection:` does not
      wrap DDL)
- [ ] Add a migration test against a populated database at an older
      `user_version`, not only an empty one
- [ ] Configure `busy_timeout` and consider WAL; two processes open the same
      file today with neither

### H18 — A failed termination is not reported as success
**Satisfies:** HR-020, HR-021 · **Verified by:** HAT-017

- [ ] Propagate `PermissionError` from `kill` as a structured error
- [ ] Do not mark a job `timed_out`/`canceled` when termination failed
- [ ] Bound the drain task so a stream that never EOFs cannot hold an executor
      thread forever
- [ ] Kill the process group, not only the direct child
- [ ] Mark a job terminal when its exec start fails, so it stops pinning the
      workspace against idle stop
- [ ] Evict `AsyncJobRunner._live` entries on completion (currently unbounded)

### H19 — Structured errors survive Docker and decoding failures
**Satisfies:** HR-022 · **Verified by:** HAT-019

- [ ] Translate `docker.errors.DockerException` in the exec and workspace paths
- [ ] Decode host-side Git output with an explicit error strategy so a non-UTF-8
      filename cannot crash `git_status`/`git_diff`/`workspace_open`
- [ ] Tolerate a commit subject containing `\x1e` in `git_log`'s parser
- [ ] Correct the `log.py` docstring claiming such bytes are invalid in a subject

---

## Phase 7 — Git and workspace correctness

### H20 — `git_commit` refuses unresolved conflicts
**Satisfies:** HR-023 · **Verified by:** HAT-018

- [ ] Inspect `status.conflicted` alongside `detect_interrupted_operation`
- [ ] Test via a real `git stash pop` conflict, which leaves no marker file

### H21 — `git_commit` stages exactly what it was told
**Satisfies:** HR-024 · **Verified by:** HAT-018

- [ ] Pass client paths as literal pathspecs (`:(literal)` or
      `--pathspec-file-nul`)
- [ ] Report `files_committed` from what Git committed, not the request echo
- [ ] Normalize paths before the strict-mode set-difference check, and account
      for staged renames
- [ ] Test `paths=[":/"]` and `[":(glob)**"]`

### H22 — A failed commit leaves the index unchanged
**Satisfies:** HR-025

- [ ] Roll back `git add` when `git commit` fails (§20.6)
- [ ] Map "nothing to commit" to a specific code rather than `COMMAND_FAILED`

### H23 — Cache volumes cannot collide across repositories
**Satisfies:** HR-009 · **Verified by:** HAT-008

- [ ] Hash-derive the volume name from `(repository_id, cache_name)`
- [ ] Provide a migration or documented path for existing volumes
- [ ] Reclaim volumes on `delete` (currently orphaned, and inherited by a later
      repository reusing the id)

### H24 — Assorted Git correctness
**Satisfies:** HR-022 (partial)

- [ ] `git_diff`: return `next_offset` and split on character boundaries — byte
      offsets currently corrupt any non-ASCII diff and reassembly is impossible
- [ ] `git_diff`: validate `offset`/`max_bytes` (negative values currently
      slice from the end)
- [ ] `detect_default_branch`: use `removeprefix("refs/remotes/origin/")`, not
      `rsplit("/", 1)`, which mangles `release/v1`
- [ ] Accept abbreviated `expected_head_sha`, or document that it must be full
- [ ] Handle `amend_published`'s `require_approval` consistently with
      `default_branch_push`
- [ ] `git_commit`: call `touch_last_used` so an active commit stream resets the
      idle clock

---

## Phase 8 — Test and CI integrity

### H25 — The coverage audit does not satisfy itself
**Satisfies:** HR-027 · **Verified by:** HAT-021

- [ ] Exclude `__file__` from `_all_test_source()`
- [ ] Add a meta-test: removing every real citation for an AT makes the audit
      fail for that AT

**Defect:** `tests/test_acceptance_test_coverage.py:36,59`. Verified — with all
real AT-010 citations removed, the audit still passed, because its own error
message contains the string. Line 23 does the same for AT-011.

### H26 — Coverage claims rest on assertions
**Satisfies:** HR-028

- [ ] Implement AT-037's `ssh_auth` audit event, or record AT-037 as explicitly
      uncovered — `grep -rn ssh_auth src/` currently returns nothing
- [ ] Make AT-021's scenario reachable by adding §24's `exec` audit event, or
      amend the AT
- [ ] Re-audit every AT whose only citation is a docstring
- [ ] Correct S33's category audit, which cites `filesystem/test_security.py`
      for "secret leakage" on the strength of a file *named* `secret.txt`

### H27 — A degraded CI run fails
**Satisfies:** HR-029 · **Verified by:** HAT-022

- [ ] Fail CI when Docker-dependent tests skip (`--strict-markers` plus an
      explicit skip guard, or a minimum-collected assertion)
- [ ] Replace the stale `alpine:3.20` pull step, which is no longer the test
      image
- [ ] Make the contract check able to fail on an unbumped `contract_version` —
      it currently compares the constant against itself

### H28 — Adversarial coverage for every input boundary
**Satisfies:** HR-030

- [ ] For each control in Phases 1–5, add a hostile-input test alongside the
      well-formed one
- [ ] Fix the tests the review found asserting nothing meaningful: AT-006's
      "disconnect" (`del started` on an in-process server), `test_write_is_
      flushed_immediately_for_concurrent_readers` (single-threaded), and
      `test_reconcile`'s hand-set `pid` (a shape production never produces)
- [ ] Add restart-survival coverage for jobs, which currently has none

---

## Phase 9 — Defaults, dead configuration, observability, documentation

### H29 — Bound output by default
**Satisfies:** HD3, `HARDENING_SPEC.md` §9 (§15.5 amendment)

- [ ] Default the `exec_output` response cap to ~64 KiB, with the full capture
      reachable only through explicit ranged retrieval
- [ ] Validate `byte_start`/`byte_end`/`line_start`/`line_end` (negatives
      currently slice from the end)
- [ ] Reconcile byte offsets against `stream_total_bytes`, which currently index
      different things and silently mislead a paging client
- [ ] Stream rather than slurping in `read_log_bytes`
- [ ] Bound `read_file` by stat before reading, not after

**Defect:** the current default is `max_output_bytes + tail_keep_bytes` =
10.25 MiB, defeating §15.5's stated purpose. AT-007 passes only because that
test reconfigures the cap to 4096.

### H30 — Enforce or delete declared configuration
**Satisfies:** HR-010

- [ ] Enforce or remove the `security:` block (`allow_docker_socket`,
      `allow_privileged_containers`, `allow_additional_host_mounts`)
- [ ] Enforce or remove `git_policy.validate_remote`
- [ ] Clamp client `timeout_seconds` to `execution.sync_timeout_seconds`
- [ ] Normalize `cwd` before the containment test (`/workspace/../etc` currently
      passes a string-prefix check)
- [ ] Add cross-field validation: `api_auth: token` with no token path,
      `scope: shared` with no `shared_key`, `user: root` with `sudo: true`
- [ ] Extend the §53 secrets-outside-repository check to `audit.path` and
      `state_db_path`
- [ ] Implement §73 step 7 — repository roots are never checked for existence
- [ ] Catch non-`ConfigError` exceptions at load (`workspace: 5` currently
      raises a raw `TypeError`)

### H31 — Operational logging
**Satisfies:** HR-031

- [ ] Introduce `logging` with a configurable level
- [ ] Log swallowed exceptions, container and ssh-agent lifecycle,
      reconciliation outcomes, and policy denials
- [ ] Route secrets through HR-013's redaction
- [ ] Replace `except Exception: exit_code = None` with a logged, typed handler

### H32 — Close the remaining spec gaps
**Satisfies:** `HARDENING_SPEC.md` §9

- [ ] Report OOM through a real surface — detection exists, reporting does not
- [ ] Query GitHub rulesets, not only branch protection (§18.5, FR-032), and
      distinguish "confirmed absent" from "not visible to this token"
- [ ] Ship pinned GitHub host keys, or document operator provisioning — the box
      is ticked, the file is created empty, and a fresh install cannot push
- [ ] Make `GIT_AUTH_FAILED` reachable (it is the likeliest real push failure
      under deploy-key auth and currently surfaces as `COMMAND_FAILED`); same
      for `GIT_REFSPEC_NOT_ALLOWED`, `NETWORK_FAILED`, `INTERNAL`
- [ ] Warn when `.devcontainer/devcontainer.json` is present and ignored (§30
      requires reporting, not support)
- [ ] Validate `.mcp-workspace.yml`'s `version:` — `999` is currently accepted
      and recorded
- [ ] Re-run setup steps after `reset`, or document that reset discards project
      configuration; a failed step currently leaves a permanently
      half-provisioned workspace that the next open silently accepts
- [ ] Give `reset` and `rebuild` distinct behavior or merge them in the spec
- [ ] Restore project `environment:` and cache mounts on `reset`/`rebuild`

### H33 — Documentation matches the shipped system
**Satisfies:** HR-032

- [ ] `docs/TOOLS.md`: list all 21 tools (six structured Git tools are missing),
      drop "the six future structured Git tools", fill in `contract_version`
      0.7.2
- [ ] `docs/CONFIGURATION.md`, `SECURITY.md`, `THREAT_MODEL.md`: write them or
      state plainly that `docs/SPEC.md` is the only reference
- [ ] `docs/DEVELOPMENT.md`: `--config` no longer merely checks existence
- [ ] Add a docs check to CI so the tool table cannot drift again

### H34 — Apply the `docs/SPEC.md` amendments
**Satisfies:** HD2, `HARDENING_SPEC.md` §9

- [ ] Apply all sixteen amendments in `HARDENING_SPEC.md` §9
- [ ] Bump `docs/SPEC.md`'s version and date
- [ ] Note in `HARDENING_SPEC.md` §2 that precedence has returned to
      `docs/SPEC.md` for each amended point

### H35 — Reconcile the checkboxes
**Satisfies:** HR-033

Untick or qualify these in `docs/TODO.md`, each with a pointer to the H-task
that closes it. The review's finding was not that tasks were unbuilt — most were
built substantially — but that a box is ticked and the prose beneath then walks
the claim back.

| Task | Correction |
| --- | --- |
| S2 | §19.1 invariant unguarded for `shared_key` (H7); §73 step 7 unimplemented (H30); invalid YAML escapes as `TypeError` (H30) |
| S3 | Migrations not atomic (H17); no concurrency configuration (H17) |
| S6 | §69 reconciliation reaches `absent` but never recovers (H15) |
| S9 / S10 | `max_patch_bytes` unenforced and `patch_file` truncation destroys data (H5); `delete_file("")` wipes the repository (H4); §12.1 rule 3 fails open when only `repositories[]` is configured |
| S12 | `cwd` check bypassable, timeout unclamped, no Docker error handling (H19, H30) |
| S15 | "no race between running and having a pid" is false ~95% of the time; `update_output_counts` is dead code |
| S21 | Argument injection (H2); bounded-diff offsets break on non-ASCII (H24) |
| S22 | Commits through unresolved conflicts (H20); pathspec magic defeats strict mode (H21); failed commit leaves paths staged (H22) |
| S24 | "Pinned GitHub host keys" ships zero keys (H32) |
| S28 | Rulesets never queried (H32) |
| S29 | Redaction covers `details` only (H11); denial fidelity below §20.7 (H13) |
| S31 | OOM reporting absent; pid exhaustion has no daemon-side detection (H32) |
| S33 | AT-037 has no test, AT-021's scenario is unreachable, coverage audit self-satisfies (H25, H26) |

S32's deferral was verified as accurate against §22.1, §22.2, §20.6 and §34.3 —
no correction needed.

---

## Deferred

### H36 — Move Git reads into the container
**Supersedes:** HD1

Not scheduled. Eliminates the host-side execution class structurally rather than
by maintaining §4.1's control surface. Costs: contradicts §20.1's stated
exception, removes Docker-free `workspace_info`/`git_status` and part of §73's
degraded start, and adds container-exec latency to every read. Revisit if §4.1
proves burdensome to maintain, or if a knob is discovered that it cannot cover.

---

## Definition of done

All six critical defects are closed and covered by HAT-001..HAT-008; the full
suite passes with no Docker-dependent test skipped; a suite run leaves foreign
Docker state untouched; `docs/SPEC.md` carries the §9 amendments; and no ticked
checkbox in either TODO overstates what the code does.
