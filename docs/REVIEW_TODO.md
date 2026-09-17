# Review TODO

**Spec:** `docs/REVIEW_SPEC.md` (VR-001..VR-014, VAT-001..VAT-006)
**Source:** code review of `docs/REMEDIATION_TODO.md` R1–R27 plus the H36/H37
work that landed after it, 2026-08-28, against `8301c72`
**Status:** complete (V1-V8 done)

Fourteen items: six correctness defects in shipped code, four overstated
claims, four stale references. Every one was reproduced against the running
code before being written down — each task below carries the reproduction so
the implementer can re-run it rather than trust this file.

**Not in this list:** the review's headline finding (`ExecutionService.capture`
truncating silently, crashing `git_log`/`git_status` and making `git_diff`
claim completeness) was already fixed as **H36a** (`5e00986`,
`docs/HARDENING_TODO.md`). V1 below is the same defect class reached through a
different truncation mechanism, and should follow H36a's shape.

## How to work this list

The discipline `docs/HARDENING_TODO.md` established and
`docs/REMEDIATION_TODO.md` extended, unchanged:

1. Implement the task.
2. **Write the adversarial test first, and watch it fail.** Every defect here
   shipped with a passing suite; four of them shipped *with* a test that was
   supposed to cover the area and chose an input that happened to work.
3. Run the new test alone, then the relevant suite, then the full suite.
4. **Revert-verify:** back up the changed files, `git show HEAD:<file> >
   <file>`, confirm the new tests fail *for the right reason*, restore.
5. Lint, format, typecheck. Check for Docker leftovers.
6. Schema-drift check if any tool output changed; bump `CONTRACT_VERSION` per
   §33.2. **No task here is expected to need one** — if one does, that is a
   signal the fix changed a contract and should be re-examined.
7. One task per commit. No `Co-Authored-By` trailers.
8. **Re-run every claim before writing it in the task's write-up** (RR-033).
   This document exists partly because that rule was not followed in four
   places in the document it reviews.

**Ordering.** Phase 0 first, in descending *consequence* — not reachability;
the earlier wording said reachability and was wrong. The six defects are
independent of one another, so the order is a convenience, not a dependency.
V1 leads because it is the only one that can both crash a call untranslated and
put R4's index-restore guarantee at risk. V2 is the most easily *reached* — no
hostile input at all, see its own trigger condition below — but its worst case
is a wrong boolean and a client that loops. Phase 1 and Phase 2 are
documentation-only, independent of each other and of Phase 0, and can be done
in any order — or batched, since none of them touches code.

---

## Phase 0 — Correctness defects in shipped code

### V1 — A line-cropped capture never reaches a strict parser ✅
**Satisfies:** VR-001 · **Verified by:** VAT-001
**Commit:** `4b6395c`

- [x] `git_commit`'s pre-`add` index snapshot no longer parses a
      line-cropped `stdout_tail`
- [x] The unguarded `parse_index_entries` call cannot raise an untranslated
      exception
- [x] Correct R4's "no newlines in `-z` output" rationale in
      `docs/REMEDIATION_TODO.md`
- [x] Test with >`response_tail_lines` newline-bearing paths

**Defect (reproduced).** `consume` crops `stdout_tail` to the last
`response_tail_lines` (200) **lines**; `stdout_truncated` reflects only the
*byte* cap, so it is `False` while the data is already cropped:

```
bytes: 16140  under 10MiB cap -> stdout_truncated = False
tail is a prefix of full?  False        tail dropped bytes: 3248
!! parse_index_entries: ValueError not enough values to unpack (expected 3, got 1)
```

Git does not escape newlines in `-z` output — R4's stated safety argument is
factually wrong. Verified against real git:

```
0000060   0  \t   a  \n   b   .   t   x   t  \0      <-- literal newline inside the path
```

**Shape of the fix.** H36a's precedent applies: the signal belongs at the
read, not inferred downstream. Either give this call site a capture path that
does not line-crop (`capture`, which H36a already made truthful, is the
obvious candidate — it is head-truncating and now reports it), or detect the
crop and fail closed. Prefer the former: it removes the failure mode rather
than reporting it, and `capture` is already the right shape for "parse this
whole output" as opposed to "show a human the tail".

Note the call site sits **outside** the `contextlib.suppress(Exception)` that
covers the rollback re-parse — so it is unguarded independently of which fix
is chosen.

**Do not** simply widen the `suppress`: R4's whole guarantee is that a failed
commit restores the caller's staged work or honestly reports
`index_left_dirty`, and swallowing the parse error silently would trade a
crash for the data-loss R4 exists to prevent.

**Implemented as described.** Added `capture_index` (`tools/git.py`), a
sibling to the existing `run_git` closure that reads through
`ExecutionService.capture` instead of `consume` — used at both call sites
(`build_capture_index_argv`'s pre-`add` and post-`add` reads). `capture`
was already the right shape (H36a made it truthful, never line-crops); no
new capture path was needed. Corrected the false claim in
`git/commit.py`'s `build_capture_index_argv` docstring and in
`docs/REMEDIATION_TODO.md`'s R4 write-up (a "Correction" paragraph, per
this project's convention — the checkbox was accurate when written, R1's
own tick stays).

**New test, watched fail first.** `tests/workspace/test_git_commit_tool.py`:
`test_at_vat001_a_line_cropped_index_snapshot_does_not_crash_the_commit` —
220 newline-bearing paths staged under one directory, a normal (not
forced-to-fail) `git_commit` call. Pre-fix: `UnexpectedToolError` wrapping
`ValueError: not enough values to unpack (expected 3, got 1)` at
`git/commit.py:123`, exactly the reproduction above, and exactly the "SDK
crash handling applies to it" `translate_workspace_errors` warns of for a
non-`WorkspaceError`. Post-fix: passes, `files_committed` lists all 220
paths.

**Revert-verified.** Backed up the two changed `src/` files, restored
`HEAD`'s versions, re-ran the new test alone: failed with the identical
`UnexpectedToolError`/`ValueError` trace. Restored the fix; test passes
again.

**Suites.** `tests/workspace/test_git_commit_tool.py tests/git/`: 211
passed. Full unscoped `tests/`: 981 passed, 0 failed, 532.66s. No Docker
leftovers (`docker ps -a --filter name=mcp-chatgpt-sandbox-` empty after the run).
Lint/format clean on the three changed `src`/test files; `mypy src`: no
issues in 53 files (the test file's pre-existing, out-of-CI-gate
`InputRequiredResult`/`structured_content` `union-attr` noise — 21 errors
before this change, 23 after, all the same pattern the file already had —
is not part of the `mypy src` gate CI actually runs). No schema/contract
change; no `CONTRACT_VERSION` bump needed, as expected.

**Correction (G7, `docs/GAP_SPEC.md` GR-008):** two references above were
accurate when written, no longer are — five file-splitting commits
landed after this task, and both moved without this write-up being
updated. `capture_index` lives in `tools/git_commit.py` now, not
`tools/git.py` (`f9ead84`'s split of `tools/git.py` into a thin hub plus
per-tool modules). `test_at_vat001_a_line_cropped_index_snapshot_does_
not_crash_the_commit` lives in `tests/workspace/
test_git_commit_tool_failure_modes.py` now, not `tests/workspace/
test_git_commit_tool.py` (`7ce029a`'s split of that file). Neither move
changed what either did — recorded here as a location correction, not a
retraction of anything V1 claimed.

### V2 — `truncated` reflects bytes withheld, not bytes requested ✅
**Satisfies:** VR-002 · **Verified by:** VAT-002
**Commit:** `d698f8c`

- [x] `truncated` compares `min(effective_end, total_size) - start` against
      `limit`
- [x] Test a `byte_range` end past EOF with a `limit` smaller than the
      requested span

**Defect (reproduced)** — `max_read_bytes=500`, 10-byte file:

```
byte_range=(0,1000) -> content='0123456789' truncated=True    <- complete, but flagged
byte_range=(5,1000) -> content='56789'      truncated=True    <- complete, but flagged
```

**Exact trigger, verified against the default config** (10 MiB
`max_read_bytes`). The defect needs *both*
`end - start > min(max_bytes, max_read_bytes)` **and**
`total_size - start <=` that same limit — the client asking for a span wider
than its own cap, on a file with less than a cap's worth left. Ordinary
pagination does **not** trip it; a chunked reader's last page, including one
whose `end` runs past EOF, was checked directly and reports correctly:

```
byte_range=(950,1050) max_bytes=100 -> got   50B, truncated=False   (correct)
byte_range=(0,20000000)             -> got 1000B, truncated=True    <- WRONG
```

The reachable idiom is "read the rest of the file" written as a large explicit
`end` instead of `end=None` (which resolves to `total_size` and is correct).
A client paging on the flag then never terminates — confirmed on a 1000-byte
file:

```
iter 0: start=   0 got=1000B truncated=True
iter 1: start=1000 got=   0B truncated=True    <- past EOF, still flagged
iter 2: start=1000 got=   0B truncated=True    ... forever
```

Write VAT-002 against this condition, not against the smaller `max_read_bytes`
the original reproduction used — a test that only shrinks the config limit
proves less than one that uses the default and an oversized explicit `end`.

**Check while here:** whether `docs/SPEC.md` §12.2's description of
`truncated` for a `byte_range` read needs the same correction, or already
says what the fixed code will do.

**`docs/SPEC.md` checked, no change needed.** §12.2 and §13.2 only say a
read's output "SHOULD identify truncation" — neither states `truncated`'s
precise semantics (bytes withheld vs. bytes requested), so there was
nothing there to contradict the old behavior or need updating for the new.

**Implemented as described.** `read_file`'s `byte_range` branch
(`filesystem/service.py`) now computes
`available_len = max(min(effective_end, total_size) - start, 0)` and
`truncated = available_len > limit` — exactly the formula VR-002
specifies. `read_len`/`span_bytes` (what's actually read and returned)
were already correct; only the flag was wrong.

**New tests, watched fail first.** `tests/filesystem/test_service.py`:
`test_at_vat002_byte_range_truncated_reflects_bytes_withheld_not_requested`
(a 10-byte file, default 10 MiB `max_read_bytes`, `byte_range=(0,
20_000_000)` — pre-fix `truncated=True` on complete content, matching the
reproduction above; also asserts the documented `end=None` idiom was
never broken) and
`test_at_vat002_byte_range_truncated_still_true_when_bytes_are_genuinely_withheld`
(the flag SHALL still fire when content really was withheld — this
already passed pre-fix, confirming the fix narrows the flag rather than
disabling it).

**Revert-verified.** Backed up `filesystem/service.py`, restored `HEAD`'s
version, re-ran the new tests: the first failed with the identical
`assert True is False` on `truncated`; the second still passed (as
expected — it isn't the regressed path). Restored the fix.

**Suites.** `tests/filesystem/`: 118 passed. `tests/filesystem/
tests/workspace/test_async_execution_tools.py` (the other `byte_range`
caller): 146 passed. Full unscoped `tests/`: 983 passed, 0 failed,
532.64s. No Docker leftovers. Lint/format clean; `mypy src`: no issues in
53 files. No schema/contract change — `truncated`'s type is unchanged,
only which value it reports; no `CONTRACT_VERSION` bump.

**Correction (G7, `docs/GAP_SPEC.md` GR-008):** `tests/filesystem/
test_service.py` above no longer exists — split into four files by
operation (`fb194f9`/`7a3f38a`). Both `test_at_vat002_*` tests now live
in `tests/filesystem/test_service_read.py`.

### V3 — Daemon-controlled variables are re-asserted in every branch ✅
**Satisfies:** VR-003 · **Verified by:** VAT-003
**Commit:** `e829487`

- [x] The root branch sets every daemon-controlled variable the non-root
      branch does, or its comment is corrected to say which branch it covers
- [x] Assess `MCP_USER`, never daemon-set in either branch
- [x] Test with `container.user: root` and a hostile `extra_env`

**Defect (reproduced)** — `container.user: root`:

```
root-mode environment: {'MCP_UID': '0', 'SSH_AUTH_SOCK': '/evil',
                        'MCP_WORKSPACE_PATH': '/workspace', 'MCP_CACHE_PATHS': ''}
```

The docstring claims ordering stops exactly these; in root mode the daemon
never sets them, so there is nothing for ordering to act on. Only
`project_config.py`'s allowlist rejects them — one layer, in the branch where
HR-006's own "neither control is sufficient alone" asked for two. This is
R20's argument, unchanged, applied to the branch R20 didn't look at.

**Decide explicitly, and record the decision:** setting `MCP_UID`/`MCP_GID` in
root mode may be wrong on its own terms (root mode deliberately does *not*
create a user or drop privileges). If so, the correct fix is to set them to a
value that preserves root-mode semantics, or to strip daemon-controlled keys
from `extra_env` in both branches rather than overwrite them. Do not
mechanically copy the non-root branch's values across.

**Decision recorded: strip, don't set-to-a-value.** `MCP_UID`/`MCP_GID` have
no "correct value" in root mode — their *absence* is the signal
`entrypoint.sh`'s `[ -z "${MCP_UID:-}" ]` reads as "run as root" (§5.3;
confirmed against `entrypoint.sh` directly, and against the existing test
`test_root_mode_with_userns_remap_is_allowed`, which already asserted
`"MCP_UID" not in kwargs["environment"]` — a contract this fix preserves,
not introduces). There is no non-empty value that "means root mode" the
way there is for every other daemon-controlled key, so "set them to a
value that preserves root-mode semantics" isn't available for these two —
only stripping is. Applied uniformly rather than case-by-case: a new
`_DAEMON_CONTROLLED_ENV_KEYS` frozenset (`MCP_UID`, `MCP_GID`,
`MCP_USER`, `MCP_SUDO`, `MCP_WORKSPACE_PATH`, `MCP_SSH_MODE`,
`MCP_CACHE_PATHS`, `SSH_AUTH_SOCK`) is filtered out of `extra_env` before
*either* branch merges it; each branch then sets only the keys it
actually wants, same as before. A key neither branch re-adds (all six
non-`MCP_WORKSPACE_PATH`/`MCP_CACHE_PATHS` keys, in root mode) now stays
absent by construction rather than by an override that branch never
performs.

`MCP_USER` included even though the checklist notes no branch ever
daemon-sets it: that was the actual gap, not a reason to leave it alone —
ordering-based protection requires a branch to set *something*, and
nothing ever did, so it was unprotected in host mode too, not only root
mode. Confirmed with a host-mode test, not just a root-mode one.

Corrected the module docstring's comment, which claimed extra_env is
"merged FIRST in both branches below, every daemon-controlled variable
LAST" — true of the non-root branch's own six keys, never true of the
root branch's five it doesn't set, which is the defect this task fixes.

**New tests, watched fail first.**
`tests/workspace/test_container_config.py`:
`test_at_vat003_root_mode_extra_env_cannot_inject_daemon_controlled_keys`
(all six leak pre-fix, exactly the reproduction above plus `MCP_USER`/
`MCP_SSH_MODE`, which weren't in the original repro but follow the same
mechanism) and
`test_at_vat003_host_mode_extra_env_cannot_inject_mcp_user` (host mode's
own, previously-unprotected gap).

**Revert-verified.** Backed up `container_config.py`, restored `HEAD`'s
version, re-ran both new tests: both failed with the leaked values shown
above. Restored the fix; `tests/workspace/test_container_config.py`: 20
passed (18 pre-existing + 2 new).

**Suites.** `tests/workspace/` (excluding the slower `test_git*` files,
which don't touch container-identity construction): 289 passed, no
Docker leftovers. Full unscoped `tests/`: 985 passed, 0 failed, 531.99s,
no Docker leftovers. Lint/format clean; `mypy src`: no issues in 53
files. No schema/contract change — this is container-startup environment
construction, never client-visible through any tool's JSON output; no
`CONTRACT_VERSION` bump.

### V4 — The entrypoint's `.ssh` provisioning does not follow symlinks ✅
**Satisfies:** VR-004 · **Verified by:** VAT-004
**Commit:** `305150f`

- [x] The `.ssh` block refuses a symlinked `$HOME/.ssh`, matching the cache
      loop R19 already hardened
- [x] Or `caches[].path` rejects collisions with daemon-managed home
      subdirectories
- [x] Test with a cache volume mounted at `.ssh` containing a symlinked
      `config`

**Defect (reproduced).** `caches[].path` is project-authored and reaches
`$HOME`:

```
'$HOME/.ssh'  -> '.ssh'        '.ssh' -> '.ssh'
'$HOME/.'     -> '.'           '.'    -> '.'
```

The `.ssh` block runs as **root**, before the identity drop, with `mkdir -p`,
a redirect into `$HOME_DIR/.ssh/config`, `chmod`, and `chown -R` — none of the
`[ -L ]` refusals the cache loop directly above it applies. A persistent cache
volume mounted there survives between runs, so a symlink planted in one run is
followed by the next run's root phase.

**Where the test goes.** A shell-script change needs a real container, not a
host-side unit test. R19's own hardening left the precedent to copy:
`tests/workspace/test_default_image.py`'s
`test_rr030_a_symlink_planted_in_a_cache_volume_is_not_followed` — it plants a
symlink inside a persistent cache volume on one run and asserts the next run
refuses. VAT-004 is that test with `.ssh`/`config` substituted for the cache
path.

**Severity, stated honestly in the write-up:** under the default
`container.sudo: true` the client already has container root through `exec`,
so this grants nothing new. Under `container.sudo: false` — supported, and
listed in §74 — it is a real escalation to container root. Contained within
the container either way; not a host escape. Say this plainly rather than
inflating or dismissing it.

**Implemented as described** (option 1: refuse a symlinked `.ssh`,
matching the cache loop). `docker/default/entrypoint.sh`'s `.ssh` block
now refuses outright if `$HOME_DIR/.ssh` is already a symlink (mirroring
the cache loop's own mount-point check directly above it), and again if
`$HOME_DIR/.ssh/config` is already a symlink, before it is ever opened
for writing — `mkdir -p` alone does not catch a symlinked *file* one
level down, which is the actually-reachable case (`.ssh` itself is
usually a real, cache-mounted directory; `config` inside it is what a
prior unprivileged run can replace). `chmod`/`chown -R` are unchanged;
the fix is entirely the two new refusals ahead of them.

**New test, watched fail first — and revised once.**
`tests/workspace/test_default_image.py`:
`test_at_vat004_a_symlinked_ssh_config_planted_in_a_cache_volume_is_not_followed`,
modeled directly on `test_rr030_*`: a persistent named volume mounted at
`.ssh`, planted with `config -> /etc/hostname` by an unprivileged run,
then a second run with the same `cache_paths=[".ssh"]` expected to
refuse. Two things the RR-030 precedent didn't need, found while writing
this one:

- The plant command needed `ln -sf`, not `ln -s`: entrypoint.sh's own
  root phase already writes a real `config` file into `.ssh` on *every*
  run, including the plant run itself, so a bare `ln -s` failed with
  "File exists" before the interesting part of the test ever ran.
- The plant target needed to be a file (`/etc/hostname`), not a
  directory (`/etc`, `test_rr030_*`'s own choice): a symlink-to-directory
  makes the shell's `>` redirect fail with its own "Is a directory"
  error regardless of this fix, which made an early revert-verification
  pass fail for the wrong reason before this was caught and corrected.

**Revert-verified — twice, for the two reasons above.** First pass (bad
plant command): failed on the *plant* step, before the assertion even
ran — not the intended failure. Fixed the plant command
(`ln -sf`), re-verified: this time the plant step succeeded but the
*reach* step's `pytest.raises(..., match="symlink")` failed with `DID
NOT RAISE` — the reverted entrypoint silently wrote through the symlink
and exited 0, exactly the defect described (silent success, not a
crash). Restored the fix: passes again.

**Suites.** `tests/workspace/test_default_image.py`: 11 passed. Also ran
with `shellcheck` on `entrypoint.sh`: one pre-existing warning (`SC2034`,
unused `GROUP_NAME`, line 66, untouched by this change) — no new
warnings from the added block.
`tests/workspace/test_default_image.py tests/workspace/test_caches.py
tests/workspace/test_container_config.py`: 48 passed. Full unscoped
`tests/`: 986 passed, 0 failed, 525.89s, no Docker leftovers. One stale volume
(`mcp-test-vr004-*`) found before the broader run, from an earlier
manual invocation made before the plant-command fix above (that
invocation's own test failure happened inside `pytest.raises`'
`with`-block, so `docker_client`'s teardown fixture still ran — the
volume's absence from that teardown is unexplained, not reproduced
again across several subsequent clean runs); removed by hand and
confirmed a normal run leaves none.

### V5 — Redaction covers every container type it can encounter ✅
**Satisfies:** VR-005 · **Verified by:** VAT-005
**Commit:** `4ad284c`

- [x] Recurse `tuple`/`set`, or narrow R18's stated scope to match reality
- [x] Redact `bytes` under a secret-flagged key
- [x] Test both

**Defect (reproduced):**

```
{'secret': ('hunter2',)}  ->  {'secret': ('hunter2',)}     <- tuple: leaks
{'token': b'ghp_abc'}     ->  {'token': b'ghp_abc'}        <- bytes: leaks
```

R18's rationale — "only a string literally *can be* a secret" — is false for
`bytes`. Reachability is low (error `details` are JSON-shaped in practice), so
**narrowing R18's claim is an acceptable outcome** if recursion turns out to
cost more than it buys; what is not acceptable is leaving the claim broader
than the behavior.

**Implemented as described (chose recursion, not narrowing).** `_redact`
(`errors.py`) gained two new branches, both before the `str` check:
`tuple` recurses element-wise and rebuilds a `tuple` (type-preserved,
matching R18's own precedent for non-string scalars — not coerced to a
`list`); `set` recurses the same way into a `set` comprehension.
`bytes` is handled alongside the existing scalar fallback: redacted to
the literal string `"[REDACTED]"` (matching what the `str` branch
already returns, not `b"[REDACTED]"` — nothing downstream needs a
`bytes` value out of a redaction) when `key_flags_secret`, left
completely untouched otherwise. `frozenset` deliberately excluded — no
caller in this codebase constructs one for `details`/audit `metadata`
today, and adding a branch with no reproducible caller would be
untested by construction; noted in-code so a future need is a one-line
addition, not a rediscovery.

R18's "only a string literally can be a secret" claim corrected in
`docs/REMEDIATION_TODO.md` (a "Correction" paragraph, per this
project's convention) — narrowing was available as an alternative per
this task's own text, but recursion/redaction turned out to cost only a
few lines, so the fuller fix was taken instead of the narrower claim.

**New tests, watched fail first.** `tests/test_errors.py`:
`test_at_vat005_a_secret_flagged_tuple_recurses`,
`test_at_vat005_a_secret_flagged_set_recurses`,
`test_at_vat005_tuple_recursion_preserves_the_container_type` (asserts
`isinstance(..., tuple)`, not just equality — a `list` would compare
equal-by-value to nothing here since the assertion is against a tuple
literal, but this test exists specifically to catch a fix that
recursed by coercing to `list` instead), `test_at_vat005_a_secret_
flagged_bytes_value_is_redacted`, and
`test_at_vat005_a_non_secret_bytes_value_is_left_untouched` (the
type-preservation half, mirrored from R18 — this one already passed
pre-fix, confirming it as a baseline guard rather than a fresh
reproduction).

**Revert-verified.** Backed up `errors.py`, restored `HEAD`'s version,
re-ran the four new reproduction tests: all four failed with the leaked
values shown above. Restored the fix; `tests/test_errors.py`: 62 passed
(57 pre-existing + 5 new).

**Suites.** `errors.py` is used across most of the test tree, same as
R18 found — ran everything except the Docker-backed `tests/workspace/`
and `tests/git/` (which don't exercise redaction any differently) as a
fast proxy: 429 passed. Full unscoped `tests/`: 991 passed, 0 failed,
528.00s, no Docker leftovers. Lint/format clean; `mypy src`: no issues
in 53 files. No schema/contract change — `redact_secrets` is an
internal defense-in-depth layer, never a tool's own JSON contract; no
`CONTRACT_VERSION` bump.

### V6 — An unexplained pid absence is logged ✅
**Satisfies:** VR-006 · **Verified by:** VAT-006
**Commit:** `f2a1ecf`

- [x] `pid_of`'s `DockerException` branch logs before returning `None`
- [x] Test that the log record is emitted

**Defect.** `pid_of` warns when the retry budget is exhausted but returns
silently on `DockerException`. Both produce `None`; R16 requires an absence be
*surfaced*, and one of the two paths is invisible in the daemon's own logs.

Smallest task here. Worth doing with V1 or on its own; not worth a phase.

**Implemented as described.** `execution/service.py`'s `pid_of` now logs
`_logger.warning("exec %s: exec_inspect failed, no pid available", exec_id)`
in the `except docker.errors.DockerException` branch, immediately before
returning `None` — same level, same logger, as the retry-exhausted path
directly below it, so the two absences read distinctly in the daemon's
own logs (different message text) without one masquerading as the
other.

**New test file — Docker-free, unlike this module's other tests.**
`tests/execution/test_service.py` (new): `ExecutionService` constructed
with a `MagicMock` `docker.DockerClient` instead of a real one — `pid_of`
only ever calls `self._docker.api.exec_inspect`, so no container is
needed to exercise either branch. `test_at_vat006_a_docker_exception_
from_exec_inspect_is_logged` (the reproduction), a baseline test pinning
the pre-existing retry-exhaustion log (unchanged by this fix, kept as a
guard against the two drifting apart again), and a plain
pid-is-returned happy-path test. `docs/REMEDIATION_TODO.md`'s R16
write-up corrected — a "Correction" paragraph, since R16's own text was
accurate as far as it went, just incomplete about this one branch.

**Revert-verified.** Backed up `service.py`, restored `HEAD`'s version,
re-ran the reproduction test: failed with no matching log record.
Restored the fix; `tests/execution/test_service.py`: 3 passed.

**Suites.** `tests/execution/ tests/jobs/`: 61 passed. Full unscoped
`tests/`: 994 passed, 0 failed, 525.89s, no Docker leftovers. Lint/format
clean; `mypy src`: no issues in 53 files. No schema/contract change — an
internal daemon log line, never client-visible; no `CONTRACT_VERSION`
bump.

---

## Phase 1 — Overstated claims

All four are `docs/REMEDIATION_TODO.md` describing itself inaccurately. No
code changes. Reasonable to land as **one commit** — they are the same class
of correction and splitting them adds ceremony without adding review value.

### V7 — Correct the four overstatements ✅
**Satisfies:** VR-007, VR-008, VR-009, VR-010
**Commit:** `b20cf52`

- [x] **R21 checkbox 2** — untick or reword. `tests/workspace/conftest.py`'s
      `repository` fixture still hard-codes `id="example"`; R21's own body
      says the rename was "considered and deliberately not done". The
      reasoning is sound and stays; the tick goes.
- [x] **R23's "exact"** — RR-026 specifies *unreachable Docker daemon*; the
      cited test removes the container so `exec_inspect` 404s. Restate as the
      substitution it is. The substitute is arguably stronger — say that too,
      rather than only conceding the mismatch.
- [x] **R27's closing paragraph** — "without constructing a running
      `MCPServer`" holds for `open_workspace` and `run_patch_file`, not for
      `run_exec` or `run_git_commit` (both call `create_server`). Scope it to
      the two it holds for; the per-module write-ups above it are already
      accurate and need no change.
- [x] **R25 checkbox 2** — "Reject a **non-increasing** `contract_version`";
      CI rejects only a *decrease*, which is correct behavior (an unchanged
      version must pass). Reword to "decrease". The CI step's own comment is
      already accurate.
- [x] Amend `docs/REMEDIATION_SPEC.md` RR-028's wording (VR-010's half) and
      RR-026's failed-termination row (VR-008's half) to match

**Implemented as described, all five bullets.** R21 checkbox 2 unticked
with an inline note pointing at the "considered and deliberately not
done" reasoning it already carried (VR-007). R23's "all seven" line
corrected to "six of seven", with the failed-termination row restated
as an accepted, arguably-stronger substitution rather than a mismatch
conceded and left at that (VR-008) — the same correction also applied
to `docs/REMEDIATION_SPEC.md`'s RR-026 table row itself, not only
R23's prose. R27's closing paragraph split into the sentence that still
holds (all four modules' bodies no longer need a
`server.call_tool(...)` round trip to exercise) and a Correction
explaining precisely which two modules' *tests* also construct no
server at all, quoting `run_git_commit`'s own already-accurate
per-module note as the model the generalizing summary should have
matched (VR-009). R25 checkbox 2 reworded to "decrease", with
`docs/REMEDIATION_SPEC.md`'s RR-028 wording corrected the same way
(VR-010) — its old "reject a non-increasing version" would have failed
every no-op commit, since an *unchanged* version is not an increase.

No code changes, as expected — every edit above is to `docs/REMEDIATION_
TODO.md` or `docs/REMEDIATION_SPEC.md`. Re-read each corrected section
against the code/CI step it describes before writing this up (RR-033):
confirmed `conftest.py`'s `repository` fixture still hard-codes
`id="example"` (VR-007); confirmed `test_rr013_failed_termination_via_
a_real_exec_inspect_failure` removes a container rather than mocking an
unreachable daemon (VR-008); confirmed `test_rr027_run_git_commit_is_
directly_callable_without_a_server` and (by the same shape)
`tools/execution.py`'s equivalent both call `create_server` for setup
(VR-009); confirmed `.github/workflows/ci.yml`'s `sort -V | tail -1`
step rejects only a decrease, and its own inline comment already says
so (VR-010).

**Suites.** None run — no source or test file changed, only
documentation. Docs-consistency test
(`tests/test_acceptance_test_coverage.py` and the `REVIEW_SPEC.md`/
`REVIEW_TODO.md` cross-checks used for V1–V6) re-run anyway, since it's
fast and touches these files indirectly: passes.

---

## Phase 2 — Stale references left by H36

Also documentation-only. Also reasonably **one commit**.

### V8 — Annotate what H36 superseded ✅
**Satisfies:** VR-011, VR-012, VR-013, VR-014
**Commit:** `ef29601`

- [x] **R1** — add the supersession note it lacks. H36 deleted
      `run_git_host_side`, `_discover_code_executing_overrides`,
      `_host_side_git_env`, `_CODE_EXECUTING_CONFIG_KEY_RE`, both env-scrub
      tuples, and all four of R1's tests. R1's implementation is entirely
      gone; R2 and R26 already carry notes, R1 — the most completely
      superseded task in the document — does not.
- [x] **`tests/git/test_remote.py`** — its docstring justifies importing a
      private helper by citing `_discover_code_executing_overrides` as
      precedent. That function no longer exists: H36 step 7 wrote the
      justification, H36 step 8 deleted its referent. Self-inflicted during
      H36 and worth fixing on those grounds alone.
- [x] **R22 checkbox 2 / R24 item 1** — both describe tests H36 deleted or
      rewrote (`run_git_host_side`'s `diff.external` test; the
      `test_rr027_*_in_the_real_argv` `subprocess.run`-spy pair, replaced by
      direct argv inspection). Both properties survive in successors.
      Annotate the way R2 and R26 already are.
- [x] **`docs/HARDENING_TODO.md` H35 prose** — still asserts S15's pid race
      "was re-checked … and found accurate today … there is nothing left to
      correct", two paragraphs below the table R26 corrected in that same
      file, and contradicting `docs/TODO.md`'s R26-corrected text. §16 scoped
      R26 to `docs/TODO.md`, so this is outside R26's letter — but it leaves a
      disproven claim standing in a file R26 edited.

**Implemented as described, all four.** Each is a "Correction (V8, ...)"
paragraph appended after the stale text it corrects — never rewriting or
unticking the original (all four described work that was accurate, or
at least not the object of correction, when written), matching the
convention R2/R26/H26/H35 already established in these files. Re-read
each claim against the current code before writing the correction
(RR-033), not transcribed from the review's own summary:

- **R1** (`docs/REMEDIATION_TODO.md`): grepped `src/` for
  `run_git_host_side`, `_discover_code_executing_overrides`,
  `_host_side_git_env`, `_CODE_EXECUTING_CONFIG_KEY_RE` — none remain
  except in docstring/comment prose recounting the history. Note
  explains what superseded the property (H36's own rationale: in-
  container `exec` already grants the code-execution risk this task
  neutralized, so there is nothing left to re-implement) rather than
  just pointing elsewhere.
- **`tests/git/test_remote.py`**: confirmed no other test file in the
  suite imports a private (`_`-prefixed) name — the "precedent" the
  docstring cited no longer has anything to point at, since H36 deleted
  the one example. Rewrote the justification to stand on its own
  (importing `_validate_url_identity` avoids the public wrappers' own
  container-context requirement) rather than leaving a claim with a
  now-empty referent.
- **R22 checkbox 2 / R24 item 1** (`docs/REMEDIATION_TODO.md`):
  confirmed `test_run_git_host_side_neutralizes_diff_external` and
  `test_rr027_end_of_options_precedes_{base_and_head,ref}_in_the_real_
  argv` no longer exist under those names; found and named the actual
  successors — `test_diff_external_still_does_not_execute`/`test_
  textconv_still_does_not_execute`
  (`tests/workspace/test_in_container_execution_accepts_repository_
  code.py`, confirmed both still assert real content, not just marker
  absence) and `test_end_of_options_precedes_{base_and_head,ref}_in_
  the_argv` (`tests/git/test_diff.py`/`test_log.py`, confirmed direct
  pure-function argv inspection, no subprocess spy).
- **H35 prose** (`docs/HARDENING_TODO.md`): confirmed `docs/TODO.md`'s
  S15 entry already carries the correct, R16-citing account, and that
  H35's own prose two paragraphs below its corrected table still says
  the opposite. Correction paragraph added directly under the stale
  bullet, explaining the contradiction and why R26's own edit (scoped
  to `docs/TODO.md`) never touched it.

**Suites.** None run — no source or test file's *behavior* changed,
only docstrings/documentation
(`tests/git/test_remote.py`'s edit is a docstring only). Re-ran
`tests/git/test_remote.py` anyway since it was the one test file
touched: 36 passed. Docs-consistency tests: 7 passed.

---

## Definition of done

All six correctness defects are closed and covered by VAT-001..VAT-006; the
full suite passes with no Docker-dependent test skipped; a suite run leaves
foreign Docker state untouched; no ticked checkbox in `docs/REMEDIATION_TODO.md`
overstates what the code does; and no task write-up in this file carries a
claim that was not re-run against the code before being written (RR-033).

**One deliberate asymmetry, recorded so it is not mistaken for an oversight:**
this document's Phase 1 and Phase 2 correct *other documents'* accuracy. If
working this list produces a finding about **this** document, it goes in the
same place — a correction paragraph here — rather than a fifth review layer.
Three layers of review documents (hardening → remediation → review) is already
the point at which the marginal layer costs more than it catches; the next
finding belongs in the task it corrects, not in a new file.
