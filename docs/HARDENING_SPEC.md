# Development Workspace MCP Server — Hardening Specification

**Status:** Draft
**Version:** 0.1
**Date:** 2026-08-27
**Reviewed against:** `214c095`

## 1. Purpose

This document specifies the corrections required by the security and
correctness review of 2026-08-27. That review found six critical defects that
let an MCP client write arbitrary files on the host, execute code on the host,
and delete the user's repository together with its Git history — each through
an intended tool surface at default configuration.

It is a companion to `docs/SPEC.md`, not a replacement. It states what the
system SHALL do differently, and it records the specific places where
`docs/SPEC.md` is itself wrong and SHALL be amended.

## 2. Relationship to `docs/SPEC.md`

`docs/SPEC.md` remains the authority for what the system does. This document is
the authority for the defects listed here, and where the two disagree on a
point this document addresses, **this document governs until the amendments in
§9 are applied to `docs/SPEC.md`**, after which `docs/SPEC.md` governs again.

Requirements here are numbered `HR-nnn` and acceptance tests `HAT-nnn`, to avoid
collision with `docs/SPEC.md`'s `FR-nnn` and `AT-nnn`.

Three of these defects exist **because `docs/SPEC.md` is wrong**, not because
the implementation departed from it. §18.7 names a mechanism that is
insufficient for the property it states; §20.2 never mentions the setting that
actually determines a push destination; §15.5's own recommended default defeats
its stated purpose. Correcting only the code would leave the next implementer
free to reintroduce all three faithfully. The §9 amendments are therefore
normative parts of this work, not documentation cleanup.

## 3. What the review changed about the threat model

`docs/SPEC.md` §4 scopes the system to a single trusted user operating their own
repositories, and §39.2 accepts malicious repository content as a residual risk.
That framing is retained. What the review established is that **the defects
below are not repository-content risks** — they are reachable by the MCP client
directly, through documented tool parameters, with no hostile repository
required:

- `git_diff(base=…)` and `git_log(ref=…)` take free-form client strings.
- `delete_file(path="")` takes a documented, in-contract value.
- `exec` gives the client arbitrary shell inside the container **by design**
  (§19.7, §20.1), and `.git/` is container-writable by default
  (`git_dir_mode: read-write`). Any repository-content attack that requires
  writing `.git/config` or `.gitattributes` is therefore **also** a
  client-reachable attack, because the client can author those files itself.

That last point is the one with the widest consequences and SHALL be treated as
a standing rule when reasoning about this system:

> **HR-000 (framing).** Any attack that requires only repository content or
> repository-local Git configuration SHALL be assessed as client-reachable, not
> as an accepted repository-content risk, for as long as `exec` and a writable
> `.git/` coexist.

## 4. Host-side execution safety

Per the 2026-08-27 decision, host-side Git reads are **retained** (§20.1's
speed exception, and §73's degraded start, both depend on them) and hardened in
place. The client can still reach them through `git_status`, `git_diff`,
`git_log`, `workspace_open`, and `workspace_info`.

### HR-001 No option injection through Git revision parameters

Client-supplied revision and ref parameters — `git_diff`'s `base` and `head`,
`git_log`'s `ref`, and any parameter added later that reaches a Git argv —
SHALL NOT be able to be interpreted by Git as options.

The daemon SHALL enforce this by **both** of:

1. rejecting any such value whose first character is `-`, with
   `INVALID_ARGUMENT`; and
2. placing revision arguments after an `--end-of-options` separator, or
   validating them through `git rev-parse --verify --quiet --end-of-options
   <value>` before use.

Rejecting on `-` alone is insufficient as a sole control, because it is a
denylist over a syntax Git may extend.

*Defect:* `git/diff.py:74` and `git/log.py:55` splice these values into argv
before any separator. Reproduced: `ref="--output=<path>"` created an arbitrary
host file through a real `git_log` MCP call, returning `{'result': []}` with no
error.

### HR-002 Host-side reads SHALL NOT execute repository-supplied code

This restates §18.7's stated property, which the current mechanism does not
achieve.

Every Git invocation the daemon runs **on the host** SHALL be constructed so
that no repository-supplied command is executed as the daemon uid. The daemon
SHALL apply all four of the following:

1. **Static flags.** Diff-producing reads SHALL pass `--no-ext-diff
   --no-textconv`.
2. **Dynamic driver neutralization.** Before invoking Git, the daemon SHALL
   enumerate the repository's own configuration (`git config --list --local`,
   and the worktree scope where applicable) and pass `-c <key>=` for every key
   matching a code-executing pattern (§4.1 below). This is complete with
   respect to what can actually run, because a driver that is not defined in
   configuration cannot be invoked.
3. **External configuration exclusion.** Host-side reads SHALL run with
   `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_SYSTEM=/dev/null`, so that
   host-global Git configuration cannot influence a read performed on the
   client's behalf.
4. **Environment scrubbing.** The inherited environment SHALL have
   `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_SEQUENCE_EDITOR`,
   `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_ASKPASS`, `GIT_PROXY_COMMAND`, and
   `GIT_TEMPLATE_DIR` removed.

The existing `-c core.hooksPath=<empty-dir> -c core.fsmonitor=` overrides and
`GIT_OPTIONAL_LOCKS=0` are retained.

#### 4.1 Code-executing configuration keys

The dynamic neutralization in HR-002(2) SHALL cover at minimum the following
key patterns, matched case-insensitively:

```
filter.*.clean          filter.*.smudge         filter.*.process
diff.*.command          diff.*.textconv         diff.external
merge.*.driver          core.pager              core.editor
core.sshCommand         core.hooksPath          core.fsmonitor
core.alternateRefsCommand                       sequence.editor
credential.helper       gpg.*program            uploadpack.packObjectsHook
remote.*.uploadpack     remote.*.receivepack
```

This list SHALL be maintained as a security control, not as a convenience
constant: it SHALL live in one named module-level definition, carry a comment
stating that additions to Git may extend it, and be covered by HAT-002.

#### 4.2 Empirically established constraints

The following were verified against `git 2.43.0` during this review and
constrain any implementation:

- **`-c diff.external=` is not a valid mitigation.** It prevents execution but
  aborts the read with `fatal: external diff died`. The correct control is
  `--no-ext-diff`.
- **`--no-ext-diff` does not cover `textconv`.** A `.gitattributes` binding to a
  `diff.<name>.textconv` driver still executes. `--no-textconv` is required in
  addition.
- **`core.attributesFile=/dev/null` does not help.** The binding that matters is
  the repository's in-tree `.gitattributes`, which that setting does not affect.
- **`git status` executes `filter.*.clean`.** Repository-supplied code execution
  on the host is therefore reachable through `git_status`, and through
  `workspace_open`/`workspace_info` via `read_basic_git_state` — not only
  through `git_diff`. Any fix scoped to the diff path is incomplete.
- **Driver names are attacker-chosen**, so a static list of `-c filter.X.clean=`
  overrides cannot work; enumeration of configured keys is required.

## 5. Destructive operation safety

### HR-003 The repository root is not a deletable target

`delete_file` SHALL refuse a path that resolves to the repository root, with
`INVALID_ARGUMENT`, regardless of `recursive`.

Additionally, a recursive delete SHALL refuse to descend into any directory
containing a `.git` entry (a nested repository, submodule, or vendored
checkout), rather than relying on the path-component check that the root case
bypasses.

*Defect:* `filesystem/service.py:201–215`. `resolve("")` legitimately returns
the root, whose relative parts are empty, so the `.git` guard never fires.
Reproduced: the working tree and `.git` were both removed and the tool returned
`{'deleted': True}`. Because `.git` goes with it, nothing is locally
recoverable.

### HR-004 A truncated read SHALL NOT become a write

`patch_file` SHALL fail with `INVALID_ARGUMENT` when the file it read was
truncated by `filesystem.max_read_bytes`, rather than writing the truncated
buffer back.

More generally: any operation that reads a bounded view of a file and then
writes the whole file SHALL verify that the read was complete before writing.

`patch_file` SHALL additionally pass the sha256 it read through as the write's
`expected_sha256`, closing the read/write window in its own optimistic
concurrency guard.

*Defect:* `tools/filesystem.py:187–219`. Reproduced with `max_read_bytes: 50`
against a 2000-byte file: 1950 bytes destroyed, reported as
`{'applied': True}`.

### HR-005 `filesystem.max_patch_bytes` SHALL be enforced

The declared limit SHALL bound the accepted `diff` payload. A declared limit
that is never read is a false assurance.

## 6. Configuration boundary integrity

### HR-006 Project configuration SHALL NOT set daemon-controlled variables

The `environment:` block of `.mcp-workspace.yml` is repository-supplied and
client-authorable. It SHALL be filtered through an allowlist policy that
**rejects** any key that is `SSH_AUTH_SOCK` or matches `MCP_*`, with a clear
error naming the rejected key.

Independently, `build_workspace_run_kwargs` SHALL merge daemon-controlled
variables **last**, so that ordering alone cannot produce an override even if
the filter is bypassed. Both controls SHALL be present; neither is sufficient
alone.

*Defect:* `container_config.py:100–112` splats `extra_env` last, and the §5.3
root refusal at line 89 inspects only `container_config.user`. Reproduced:
`MCP_UID: "0"` produced a container running as root on a daemon with no
user-namespace remapping, and a container write to the bind mount produced a
**setuid-root binary owned by root on the host** — the exact outcome §5.3
exists to prevent.

### HR-007 The §19.1 key invariant SHALL cover every key path

The config-load refusal that rejects a key resolving under the host user's
`.ssh` SHALL be applied to **every** configuration path that can name a key,
currently `ssh.keys_dir`, `repositories[].ssh_key`, and `ssh.shared_key`.

The check SHALL be written so that adding a new key-bearing field without
extending it is a test failure, not a silent gap.

*Defect:* `config/loader.py:74–94` omits `ssh.shared_key`. Reproduced: a config
with `scope: shared` and `shared_key: "../fakehome/.ssh/id_ed25519"` was
accepted, and resolved to the host user's personal key. FR-025 states this
invariant "SHALL NOT be relaxed by policy or configuration."

### HR-008 The entrypoint SHALL re-validate cache paths

`entrypoint.sh` SHALL reject any `MCP_CACHE_PATHS` entry that is absolute,
contains a `..` component, or is empty, before creating or chowning it.

Host-side validation in `caches.py` is necessary but not sufficient, because
`MCP_CACHE_PATHS` is settable directly through project `environment:` when no
caches are declared. Validation SHALL exist on both sides of the boundary.

*Defect:* reproduced — `MCP_CACHE_PATHS='../../etc:../../usr/bin'` left `/etc`
and `/usr/bin` owned by the container user.

### HR-009 Cache volume names SHALL be unambiguous

The volume name derived from a repository id and cache name SHALL be
collision-free across repositories. The daemon SHALL derive it by hashing the
`(repository_id, cache_name)` pair, as `naming.container_name` already does,
rather than by string concatenation.

*Defect:* `caches.py:69–73`. Repository `my-app` + cache `cargo` and repository
`my` + cache `app-cargo` both yield `mcp-cache-my-app-cargo`. Caches hold
executables, so this is a cross-repository write primitive, not only a
disclosure.

### HR-010 Declared configuration SHALL be enforced or removed

Every declared configuration key SHALL either be read by the code or be deleted
from the model. Currently unenforced: `filesystem.max_patch_bytes` (see
HR-005), the entire `security:` block (`allow_docker_socket`,
`allow_privileged_containers`, `allow_additional_host_mounts`), and
`git_policy.validate_remote`.

§18.9 already states the principle: "A policy key with no tool to enforce it is
not a policy." This requirement extends it to every block.

## 7. Path containment

### HR-011 File access SHALL be race-free

Containment SHALL be enforced on the file descriptor actually used for I/O, not
on a path that is re-resolved afterwards. The daemon SHALL open files with
`O_NOFOLLOW` relative to a directory descriptor pinned at validation time
(`os.open`/`openat` semantics), or SHALL re-verify via `fstat` that the opened
inode matches the validated one.

*Defect:* `filesystem/security.py:69–80` validates with `Path.resolve()`;
`service.py:132,196` then re-open by path. The racing writer is the container
itself via `exec`. Reproduced: `read_file` returned a host private key on the
first iteration; `write_file` landed outside the root within 24.

### HR-012 An escaping symlink SHALL NOT break enumeration

`list_files` and `search_files` SHALL treat a symlink that escapes the
repository, or dangles, as a reportable entry — never as a reason to fail the
whole listing.

*Defect:* `filesystem/service.py:84–90` and `search.py:92–95` catch only
`PathGitMetadataError`, so `PathOutsideRepositoryError` aborts the call. A
`.venv/bin/python -> /usr/bin/python3` link is enough to make
`list_files(recursive=true)` unusable. The `_describe` docstring already
promises the correct behavior; that branch is currently unreachable.

## 8. Integrity of the guarantees the system reports

### HR-013 Error messages SHALL be redacted

Secret redaction SHALL be applied to the `message` field of a tool error, not
only to `details`. Redaction SHALL additionally handle values inside lists,
non-string scalars, and `scheme://user:pass@host` userinfo.

*Defect:* `errors.py:273–280` redacts `details` only, and many raise sites
interpolate raw Git/SSH stderr or a full remote URL into the message.
Reproduced: a `ghp_` token in cleartext in `message` while `details.url`
correctly showed `[REDACTED]`.

### HR-014 The validated push destination SHALL be the destination used

Remote validation SHALL resolve the URL that `git push` will actually use —
`git remote get-url --push <name>`, which accounts for `remote.<name>.pushurl`
and `url.<base>.pushInsteadOf` — or the daemon SHALL push to the validated URL
string rather than to the remote name.

Where fetch and push URLs differ, **both** SHALL be validated.

*Defect:* `git/remote.py:48–65` validates the fetch URL; `git/push.py:96–100`
pushes by remote name. Reproduced: validation approved `owner/repo` while the
commit landed in a different repository, and the success audit record named the
destination the push never reached. An audit log that is affirmatively wrong is
worse than one that is silent.

### HR-015 Denials SHALL be audited with the fidelity §20.7 requires

A denial record SHALL carry the policy key that produced it, the
`repository_id`, and the operation's `remote`, `branch`, and
`remote_repository` where applicable — matching the success record's fidelity,
as §20.7 already requires. Success records for `git_push` SHALL additionally
carry `refspec` and `remote_protection_status`.

*Defect:* `tools/git.py:107–123` records only `{policy_decision, error_code}`
and discards `PolicyDeniedError.details["policy"]`.

### HR-016 Authorization SHALL be re-checked against live configuration

Every workspace-scoped tool call SHALL validate the resolved repository against
the live `RepositoryRegistry`, not against persisted state alone.

*Defect:* `workspace/manager.py:99–123` resolves `workspace_id → canonical_path`
from the database with no registry consultation, so a repository removed from
configuration remains fully accessible through its stale rows. Reproduced
against a registry authorizing nothing.

## 9. Required amendments to `docs/SPEC.md`

These SHALL be applied. Each is a defect in the specification itself.

| Section | Defect | Amendment |
|---|---|---|
| §18.7 | Names `core.hooksPath` + `core.fsmonitor` as sufficient for "a read cannot execute repository-supplied code under the daemon's identity." It is not — `diff.external`, `textconv`, and `filter.*` all execute, and `git status` is affected, not only `status`/`diff` as written. | Replace the mechanism sentence with HR-002's four-part control, and state that the property governs where any future mechanism falls short. |
| §20.1 | Lists the host-side read exception without carrying §18.7's protection forward in full. | Cite HR-001 and HR-002 explicitly for the three host-side reads, and note that `workspace_open`/`workspace_info` reach them through `read_basic_git_state`. |
| §20.2 | Defines remote validation without mentioning `pushurl` or `pushInsteadOf`, so a conforming implementation validates the wrong URL. | Add HR-014: validation SHALL resolve the push URL for push operations. |
| §20.7 | Requires denial fidelity "including the policy key" but the recommended record shape omits it. | Show the policy key in the example record. |
| §15.5 | The recommended default (`max_output_bytes + tail_keep_bytes`, 10.25 MiB) defeats the section's own stated purpose of preventing multi-megabyte output from consuming MCP context. | Specify a response default on the order of 64 KiB, with the full capture reachable only through explicit ranged retrieval. |
| §12.1 | Protects `.git` by path component, which the repository root itself bypasses. | State that the repository root is never a valid target for `delete_file`, and that recursive deletion SHALL refuse directories containing a nested `.git`. |
| §5.3 | The root refusal is specified against `container.user`, leaving an environment-supplied uid override outside its scope. | State that the refusal applies to the container's *effective* uid however determined, and that repository-supplied configuration SHALL NOT influence it. |
| §19.1 | The refusal is described per-repository, which the implementation mirrored, leaving `ssh.shared_key` unguarded. | State that the refusal applies to every configuration path that can name a key. |
| §11 | "Cache behavior SHALL be configurable as: per workspace; per repository; shared globally" — only repository scope exists. Cache naming is unspecified, permitting collisions. | Either implement all three scopes or downgrade the SHALL to match reality; add HR-009's collision-free naming requirement. Also address "SHALL NOT treat a cache as trusted executable input", currently unaddressed. |
| §24 | Recommends audit events for command start, file write, file delete, and cancellation. None exist, which makes AT-021's scenario unreachable. | Either specify the required events as normative and implement them, or amend AT-021 to a scenario the system can actually produce. §24.1's "configurable secret-redaction layer" SHALL likewise be made configurable or redescribed as fixed. |
| §26 | "Detect and report" OOM — detection exists, reporting has no surface. | Specify the reporting surface (a `workspace_info` field, an audit event, or an error code), or downgrade to detection only. |
| §18.5 | Requires rulesets; only branch protection is queried, and rulesets are the sole mechanism on Free private repositories. | Require the rulesets endpoint, and distinguish "confirmed absent" from "not visible to this token" — a 404 currently asserts the former without evidence. |
| §14.2 | "SHALL remain inside allowed container paths" — implemented as a string prefix test, so `/workspace/../etc` passes. | Require normalization before comparison. |
| §14.4 | Specifies a strict maximum duration with no statement that a client value cannot exceed it. | State that a client-supplied timeout SHALL be clamped to the configured maximum. |
| §44.1 | Orders reconciliation containers → agents → jobs; §73 and the implementation use a different order, and step 6 is not performed. | Reconcile the two orderings and state which governs. |
| §8.4 | `reset` and `rebuild` "are distinct semantics, and the daemon SHALL implement them internally" — they are one code path. | Either specify the distinction operationally or merge them in the spec. |

## 10. Recoverability and liveness

### HR-017 A workspace SHALL be recoverable from any Docker state

`ensure_workspace` SHALL create a container for an existing workspace row whose
container is missing, and SHALL adopt-or-remove a container found by
deterministic name when no matching row exists.

No sequence of Docker-side events SHALL leave a workspace permanently
unopenable without manual database surgery.

*Defect:* `workspace/manager.py:246–261, 509–516`. A transient `containers.run`
failure, or `docker system prune`, wedges the row permanently — including after
Docker fully recovers. §69 adoption exists only in `reconcile()`, which never
runs on the create path, so the deterministic name then collides with a 409.

### HR-018 Deleting a workspace SHALL succeed for a workspace that ran jobs

The `job.workspace_id` foreign key SHALL cascade, or `delete()` SHALL remove
dependent rows in the correct order within one transaction.

*Defect:* the container is removed first, then `DELETE FROM workspace` raises
`sqlite3.IntegrityError`, leaving the container gone and the row stuck in
`deleting` forever, surfaced as a bare traceback.

### HR-019 Migrations SHALL be atomic

Schema migrations SHALL run inside an explicit transaction. Python's `sqlite3`
issues an implicit `BEGIN` only for DML, so `with connection:` does not wrap
DDL.

*Defect:* reproduced — a partially-applied migration persisted its first
statement while `user_version` stayed at `0`, so the next start re-ran it, hit
"table already exists", and the daemon could never start again.

### HR-020 A failed termination SHALL NOT be reported as a termination

`kill` SHALL propagate a failure to signal (notably `PermissionError`) as a
structured error rather than having it swallowed by a catch-all, and a job whose
termination failed SHALL NOT be recorded as `timed_out` or `canceled`.

The drain task SHALL be bounded so that a stream which never reaches EOF cannot
hold an executor thread indefinitely.

*Defect:* `execution/service.py:185–199` and `async_runner.py:139–151`.
Reproduced: the process was still running after "termination", the job was
marked `timed_out`, and `asyncio.run()` never returned because the drain thread
never released. Enough such jobs deadlock every `asyncio.to_thread` in the
daemon.

### HR-021 A failed start SHALL reach a terminal state

A job whose exec start fails SHALL be marked terminal rather than left in
`starting`, where it counts as active and pins the workspace against idle stop
until the next daemon restart.

### HR-022 Structured errors SHALL survive Docker and decoding failures

`docker.errors.DockerException` and `UnicodeDecodeError` SHALL be translated to
§31 error codes rather than escaping as unhandled exceptions.

Host-side Git output SHALL be decoded with an explicit error strategy
(`errors="surrogateescape"` or equivalent), so that a non-UTF-8 filename cannot
crash `git_status`, `git_diff`, `workspace_open`, or `workspace_info`.
`git_log`'s record parser SHALL tolerate a subject containing its own field
separators.

## 11. Git operation correctness

### HR-023 `git_commit` SHALL refuse an unresolved conflict

The precondition SHALL inspect `status.conflicted`, not only the marker files
`detect_interrupted_operation` reads. §44.4 already lists unresolved conflicts
as a distinct state.

*Defect:* reproduced via `git stash pop` — status showed `UU`, the guard
returned `None`, and the commit succeeded.

### HR-024 `git_commit` SHALL stage exactly what it was told

Client-supplied paths SHALL be passed as literal pathspecs (`:(literal)`
prefixing, or `--pathspec-file-nul`), so that pathspec magic cannot widen the
commit. `files_committed` SHALL report what Git actually committed, not the
request echo.

*Defect:* `git/commit.py:32,45`. `paths=[":/"]` stages the entire repository
while the strict-mode check sees nothing extraneous.

### HR-025 A failed commit SHALL leave the index unchanged

§20.6 requires a failed operation to leave repository state unmodified. `git
add` SHALL be rolled back when the subsequent `git commit` fails.

## 12. Test and CI integrity

The suite passed 656 tests while every defect above was present. These
requirements address why.

### HR-026 Test teardown SHALL NOT destroy host state

Fixtures SHALL remove only resources they created, identified by a test-run
specific label or name prefix — never by the daemon's production label, and
never by a global name filter.

*Defect:* `tests/workspace/conftest.py:61–70` removes **every** container
carrying the production `managed` label and **every** `mcp-cache-` volume on the
host. The fixture docstring claims "nothing else on the host's Docker daemon is
touched"; that is false. Running the suite on a machine serving live workspaces
destroys the user's containers and every dependency cache.

This SHALL be fixed before the suite is next run on a machine with a live
daemon.

### HR-027 Coverage checks SHALL NOT satisfy themselves

A test that verifies coverage by scanning source files SHALL exclude its own
file from the scan.

*Defect:* `tests/test_acceptance_test_coverage.py:36,59`. The audit globs its
own file, and its failure message contains the literal `AT-010`. Verified: with
every real AT-010 citation removed from the codebase, the audit still passed.
Line 23 does the same for AT-011.

### HR-028 Coverage claims SHALL rest on assertions, not mentions

An acceptance test SHALL be considered covered only by a test that exercises its
scenario. A docstring naming an AT — particularly one explaining why it is *not*
implemented — SHALL NOT satisfy the coverage check.

At minimum, AT-037 (no `ssh_auth` audit event exists at all) and AT-021 (no
`exec` audit record exists, so the scenario cannot occur) SHALL be either
implemented or recorded as explicitly uncovered.

### HR-029 A degraded CI run SHALL fail

CI SHALL fail when Docker-dependent tests skip. A run that proves nothing SHALL
NOT report success.

*Defect:* reproduced — with Docker unreachable the suite exits `0` with the
integration tests skipped. 187 of 566 test functions live in Docker-dependent
files. CI's "Pull test image" step also still pulls `alpine:3.20`, which is no
longer the test image.

### HR-030 Adversarial coverage SHALL exist for every input boundary

The suite's uniform blind spot is that it exercises intended arguments only.
Each control in §4–§8 SHALL carry a test that supplies a hostile value, not
merely a well-formed one.

## 13. Observability

### HR-031 The daemon SHALL emit operational logs

The daemon SHALL use the standard `logging` module, at minimum for: exceptions
currently swallowed by catch-all handlers, container lifecycle transitions,
ssh-agent lifecycle, reconciliation outcomes, and policy denials.

Secrets SHALL pass through the same redaction as HR-013.

*Gap:* `getLogger` appears nowhere in 8,155 lines of source. Combined with
`except Exception: exit_code = None` at `async_runner.py:150`, a failure in the
job drain path is entirely invisible. This is not a `docs/SPEC.md` violation —
§25 and NFR-007 require diagnostic *tools* and structured errors, not a logging
framework — which is itself why it never got built.

## 14. Documentation truth

### HR-032 Shipped surface SHALL match documented surface

`docs/TOOLS.md` SHALL list all 21 shipped tools — it currently lists 15, omits
every structured Git tool, and still calls them "the six future structured Git
tools" — and SHALL carry the real `contract_version` (0.7.2), not a
placeholder.

`docs/CONFIGURATION.md`, `docs/SECURITY.md`, and `docs/THREAT_MODEL.md` SHALL
either be written or SHALL state plainly that `docs/SPEC.md` is the only
reference. They currently promise content "once S2 lands" and "as the
corresponding phases land"; all phases have landed.
`docs/DEVELOPMENT.md` SHALL stop claiming `--config` only checks file existence.

### HR-033 Checkboxes SHALL reflect implementation

The ticked boxes listed in `docs/HARDENING_TODO.md` §Checkbox reconciliation
SHALL be corrected. The review's finding was not that tasks were unbuilt — most
were built substantially — but that a box is ticked and the prose beneath it
then walks the claim back.

## 15. Acceptance tests

Each SHALL be automated. `HAT-` numbering continues `docs/SPEC.md`'s `AT-`
convention and is additive to it.

### HAT-001 Revision parameters cannot inject options
Call `git_diff` with `base="--output=<path>"` and `git_log` with
`ref="--output=<path>"` through the MCP tool layer. Both SHALL fail with
`INVALID_ARGUMENT`, and no file SHALL be created at `<path>`.

### HAT-002 Host-side reads execute no repository-supplied code
For each of `diff.external`, `diff.<name>.textconv` bound via `.gitattributes`,
and `filter.<name>.clean` bound via `.gitattributes`: configure the driver to
touch a marker file, then call `git_status`, `git_diff`, `git_log`, and
`workspace_info`. No marker SHALL be created, and each read SHALL still return
correct results — a read that fails is not a pass.

### HAT-003 The repository root cannot be deleted
`delete_file(path="", recursive=true)` SHALL fail, and the repository and its
`.git` SHALL remain intact. Variants: `"."`, `"./"`, an absolute path to the
root, and a path traversing to it.

### HAT-004 A truncated read cannot destroy a file
With `max_read_bytes` set below a file's size, `patch_file` SHALL fail and the
file SHALL be byte-identical afterwards.

### HAT-005 Project configuration cannot escalate identity
A `.mcp-workspace.yml` setting `MCP_UID: "0"` SHALL be rejected, and the
resulting container's PID 1 SHALL NOT run as root on a daemon without
user-namespace remapping.

### HAT-006 The human's key cannot be provisioned
A config naming `ssh.shared_key` that resolves under the host user's `.ssh`
SHALL be refused at load time. Repeat for `repositories[].ssh_key` and
`ssh.keys_dir`.

### HAT-007 Cache paths cannot escape `$HOME`
`MCP_CACHE_PATHS='../../etc'` supplied through project `environment:` SHALL NOT
result in `/etc` changing ownership.

### HAT-008 Cache volumes do not collide
Repository `my-app` + cache `cargo` and repository `my` + cache `app-cargo`
SHALL resolve to different volume names.

### HAT-009 Path containment survives a symlink race
With a concurrent writer flipping a directory component to an escaping symlink,
no `read_file` SHALL return content from outside the repository and no
`write_file` SHALL land outside it, across a sustained run.

### HAT-010 An escaping symlink does not break listing
A repository containing an escaping symlink and a dangling symlink SHALL still
list and search successfully, reporting both as entries.

### HAT-011 Secrets do not appear in error messages
Provoke a failure whose underlying stderr contains a `ghp_`-style token and a
URL with userinfo. Neither SHALL appear in `message` or `details`.

### HAT-012 The validated push destination is the real one
With `remote.origin.pushurl` pointing elsewhere, `git_push` SHALL either fail
validation or push to the validated destination — and the audit record SHALL
name the destination actually used.

### HAT-013 De-authorized repositories become inaccessible
After removing a repository from configuration, every workspace-scoped tool
against its stale `workspace_id` SHALL fail authorization.

### HAT-014 A workspace recovers from any Docker state
For each of: container removed out of band; a failed create; and an orphaned
container occupying the deterministic name — a subsequent `workspace_open`
SHALL succeed.

### HAT-015 A workspace that ran a job can be deleted
`delete` SHALL succeed and leave no rows behind.

### HAT-016 A partially-applied migration does not brick the daemon
A migration failing midway SHALL leave `user_version` and schema consistent, and
the daemon SHALL start.

### HAT-017 A failed kill is not reported as success
With a process the daemon cannot signal, `exec_cancel` SHALL return a structured
error, the job SHALL NOT be marked `canceled`, and the daemon SHALL still shut
down cleanly.

### HAT-018 Commits refuse conflicts and honor exact paths
`git_commit` SHALL fail against an unresolved-conflict tree, and
`paths=[":/"]`SHALL NOT commit files outside the named paths.

### HAT-019 Hostile filenames do not crash the daemon
A repository containing a non-UTF-8 filename SHALL be listable, statusable, and
diffable, and a commit subject containing `\x1e` SHALL not break `git_log`.

### HAT-020 The test suite does not touch foreign Docker state
With a sentinel container carrying the production label and a sentinel
`mcp-cache-` volume present, a full suite run SHALL leave both intact.

### HAT-021 The coverage audit does not satisfy itself
With every real citation for a given AT removed, the coverage audit SHALL fail
for that AT.

### HAT-022 A degraded CI run fails
With Docker unreachable, the suite SHALL exit non-zero.
