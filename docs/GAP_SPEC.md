# Gap Spec

**Source:** code review of `docs/REVIEW_TODO.md` V1–V8 plus the five
file-splitting commits that landed after `docs/AUDIT_TODO.md` A1–A4, on
2026-08-29, against `fca1199`.

**Series:** `GR-001`..`GR-011` (requirements), `GAT-001`..`GAT-008`
(acceptance tests).

V1–V8 were all verified as genuinely implemented — every one of the six
correctness defects is closed, and every documentation correction in
Phase 1/Phase 2 checks out against the code it describes. This document
is not a re-litigation of that work. It covers eleven gaps found
*around* it: two regressions introduced by the refactoring that followed
it — one of which currently breaks CI and one of which makes a test pass
while verifying nothing — one security control that stops short of the
threat model its own code comment states, one earlier fix applied at two
call sites but not a third, and a set of smaller correctness, test, and
reference gaps.

## A note on adding a sixth document

`docs/REVIEW_TODO.md`'s closing section says plainly:

> Three layers of review documents (hardening → remediation → review) is
> already the point at which the marginal layer costs more than it
> catches; the next finding belongs in the task it corrects, not in a new
> file.

`docs/AUDIT_SPEC.md` was the fourth layer, added after that warning; this
is the fifth. The warning is recorded here rather than quietly stepped
over, because it is right about the failure mode it names — a review
document that exists only to correct another review document's prose is
not worth its own file, and Phase 1/Phase 2 of `REVIEW_TODO.md` were
exactly that shape.

**This document is a different shape, and that is the reason it exists as
a file rather than as correction paragraphs.** Nine of its eleven
requirements (GR-001, GR-002, GR-004 through GR-007, GR-009 through
GR-011) are defects in *shipped code, tests, or CI state* — not
inaccuracies in prose. One of them (GR-001) means `master` does not
currently pass its own CI. Correction paragraphs cannot carry a fix, a
reproduction, and a test the way a task list can.

Exactly two requirements are prose-shaped, and each is here for a reason
of its own rather than because loose corrections needed a home. GR-003 (a
false `mypy` claim in three commit messages) shares its root cause — a
local toolchain silently diverging from CI's — with GR-001; separating
them would hide that they are one story. GR-008 (stale cross-references:
seven documentation rows, one docstring line, one code comment) was
produced by the same five file-splitting commits as GR-001 and GR-010,
and is cheapest to close alongside them.

**GR-002 is deliberately not counted as prose**, despite reading like
process guidance: it requires an *enforcing* mechanism — a hook, or a
`make`/`just` target — and its own text forbids satisfying it with the
advisory-only documentation that already failed here.

If a finding about **this** document emerges while working it, it goes in
a correction paragraph here, per `REVIEW_TODO.md`'s rule — not a sixth
layer.

---

## Phase 0 — The repository does not pass its own gate

### GR-001 — `master` SHALL pass the lint gate CI runs

`uv run ruff check .` — the exact command in
`.github/workflows/ci.yml:46-47` — fails at `fca1199`:

```
I001 Import block is un-sorted or un-formatted
  --> src/mcp_chatgpt_sandbox/workspace/manager_core.py:14:1
  --> src/mcp_chatgpt_sandbox/workspace/manager_lifecycle.py:16:1
  --> src/mcp_chatgpt_sandbox/workspace/records.py:20:1
```

All three files were created by `05fe306` (the `workspace/manager.py`
split). The pre-split `manager.py` passes the same check cleanly,
confirmed by extracting `05fe306^`'s version and running the project's
ruff against it — so this is a regression introduced by that commit, not
pre-existing debt it inherited.

**Root cause, verified.** Two ruff binaries are reachable in this
environment:

| invocation | version | verdict on the three files |
| --- | --- | --- |
| `ruff` (bare, on `PATH`) | 0.12.0 | passes |
| `uv run ruff` (project-pinned) | 0.16.4 | **fails** |

`05fe306` ran the bare binary. Worse than a missed check: bare
`ruff check --fix` **actively rewrote** the import blocks into the order
0.16.4 rejects, moving `from docker.errors import …` below the
first-party `mcp_chatgpt_sandbox` imports. The fix is to run the pinned
binary's own `--fix`, which restores the third-party-before-first-party
order the rest of the codebase already uses.

**A second, unrelated finding surfaced while verifying this requirement.**
`uv run ruff format --check .` over the whole repository also fails on
`docs/REMEDIATION_SPEC.md` and `docs/REMEDIATION_TODO.md` — 0.16.4
reformats a Python code sample embedded in both (collapsing
`return                                   # <- silent success` to
`return  # <- silent success`). Confirmed via `git worktree` at
`f9ead84^` (before any of the five file-splitting commits) that this
predates all of them; it is not part of the same regression and has a
different, older cause (the pinned ruff version's markdown-embedded-code
formatting versus whatever ruff version last touched these files). Folded
into this task rather than given its own `GR`/`GAT` pair — a two-line,
zero-semantic-content whitespace fix in an illustrative code sample,
matching V6's own precedent for a finding "worth doing with \[the
adjacent task\] … not worth a phase." Recorded here, distinctly from the
`05fe306` regression above, so neither is misattributed to the other.

**Requirement.** `uv run ruff check .` and `uv run ruff format --check .`
SHALL both exit 0 at `HEAD`, over the whole repository. Verified by
GAT-001.

### GR-002 — Local verification SHALL NOT be able to silently diverge from CI

`docs/DEVELOPMENT.md:13-15` already documents the correct commands:

```
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

They were documented and not followed, and nothing caught it: there is no
`.pre-commit-config.yaml` and no non-sample hook in `.git/hooks/`. A
contributor (human or agent) who reaches for the bare binary gets a
different tool version, a different verdict, and — in the `--fix` case —
an actively wrong rewrite.

**Requirement.** The repository SHALL provide a mechanism that runs the
same commands CI runs, such that using a divergent local toolchain is
caught before a push rather than in CI. A `pre-commit` hook invoking the
three `uv run …` commands satisfies this; so does a `make check` /
`just check` target that `DEVELOPMENT.md` directs contributors to.

The mechanism SHALL NOT be advisory-only documentation, since advisory-only
documentation is what already failed here.

### GR-003 — A claim about a gate SHALL be produced by running that gate

Three commit messages — `05fe306`, `fb194f9`, `fca1199` — assert:

> `mypy src`: 59 errors both before and after (confirmed via `git stash`)
> — all pre-existing missing-stub/environment issues … No new errors from
> the split.

`uv run mypy src`, the command CI runs, reports:

```
Success: no issues found in 60 source files
```

The 59 errors came from a bare `mypy` outside the project environment,
where `docker`, `yaml`, and `mcp.server` have no stubs installed. The
before/after *comparison* was still sound — it correctly showed the splits
introduced nothing new — but presenting it as the project's type state
was wrong, and it understated the codebase (which is clean).

This is the failure mode RR-033 exists to prevent ("re-run every claim
before writing it in the task's write-up"), committed while working on the
document family that established RR-033.

**Requirement.** The inaccurate claim SHALL be corrected in a durable
location. Pushed commit messages cannot be rewritten (and SHALL NOT be, on
a shared branch), so the correction goes in this document family's own
write-up, per the convention `docs/REMEDIATION_TODO.md` and
`docs/HARDENING_TODO.md` already use for superseded claims.

---

## Phase 1 — Security: a control that stops short of its own threat model

### GR-004 — Project-authored `environment:` SHALL NOT supply variables that control the entrypoint's interpreter or command resolution

`src/mcp_chatgpt_sandbox/project_config.py:128` rejects exactly two classes
of key:

```python
shadowed = sorted(k for k in v if k == "SSH_AUTH_SOCK" or k.startswith("MCP_"))
```

That validator's own docstring (`project_config.py:115-120`) states the
threat model it was written for:

> HR-006 (`docs/HARDENING_SPEC.md` §6): `environment:` is
> repository-supplied and, via `exec` plus a container-writable `.git/`,
> **effectively client-authorable**.

Every other key reaches the container. `docker/default/entrypoint.sh` is
`#!/bin/bash`, is the image `ENTRYPOINT` (`Dockerfile:38`), runs as **root
as PID 1 before the privilege drop**, contains **no `PATH=` assignment
anywhere** (confirmed by grep), and invokes `id`, `git`, `getent`,
`useradd`, `mkdir`, `chown`, `chmod`, and `setpriv` unqualified.

**Three reachable vectors, verified in live containers** — not inferred
from reading:

- `BASH_ENV=/workspace/pwn.sh` — bash sources it at non-interactive
  startup. Observed: `PWNED-BASH_ENV uid=0`.
- `PATH=/workspace/bin:…` — shadows any unqualified command the script
  runs. Observed: `PWNED-PATH uid=0`.
- `LD_PRELOAD` — same class, not separately exercised.

The payload is trivially placeable: `/workspace` is the project's own
bind-mounted checkout.

**The sharp consequence is not the code execution — it is that a crafted
`PATH` skips the privilege drop entirely.** `entrypoint.sh:52`:

```sh
if [ "$(id -u)" != "0" ]; then
    # Not root at entrypoint: nothing to create, just run the command.
    exec "$@"
fi
```

A fake `id` earlier on the poisoned `PATH` that prints anything other than
`0` makes this branch taken, and `exec "$@"` runs the workspace command as
uid 0 with no drop ever performed. Verified end to end against a real
Docker daemon with `--security-opt no-new-privileges:true` and
`MCP_SUDO=false`: the command reported `uid=0 root`.

**Severity, stated honestly** — matching the framing V4 established for
the same class of finding:

- Under the default `container.sudo: true`, the client already has
  container root through passwordless sudo. This grants nothing new.
- Under `container.sudo: false` — supported (`config/models.py:132`,
  `sudo: bool = True`, so `false` is settable) and listed in §74 — it is a
  real escalation to container root, and it additionally nullifies
  `no-new-privileges`, because no privilege transition ever occurs for
  that flag to constrain.
- Contained within the container either way. **Not a host escape.**

This requirement is scoped strictly to the threat model the project's own
code comment states. It does not depend on, and does not re-open, any
question about whether repositories themselves are trusted.

**Requirement.** `ProjectWorkspaceConfig.environment` SHALL reject keys
that control interpreter behavior or command resolution. At minimum:
`PATH`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `IFS`, `HOME`, and any key
matching `LD_*`. An allowlist of permitted keys satisfies this more
durably than a denylist and SHOULD be preferred if the configuration
surface allows it; if a denylist is chosen, the choice and its residual
risk SHALL be recorded, since a denylist is open by default and this
requirement exists because an open-by-default filter was too narrow.

Verified by GAT-002.

### GR-005 — The entrypoint SHALL resolve its own commands independently of inherited environment

GR-004 closes the input; this closes the mechanism, so that a future
config path, a daemon-level `docker.environment`, or an image change
cannot reopen it.

**Requirement.** `docker/default/entrypoint.sh` SHALL set an explicit
absolute `PATH` before invoking any external command, and SHALL NOT rely
on an inherited `PATH` for any command it runs as root prior to the
privilege drop. Setting `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`
at the top of the script satisfies this.

This alone defeats the drop-bypass, which is why it is specified
independently of GR-004 rather than folded into it: the two controls fail
independently, and HR-006's own "neither control is sufficient alone"
reasoning applies here exactly as it did there.

Verified by GAT-003.

---

## Phase 2 — Correctness gaps in shipped code

### GR-006 — A lossy capture SHALL NOT reach a strict parser through the shared read path

AR-001 (`docs/AUDIT_SPEC.md`) added `CapturedOutput.stdout_lossy` and
guarded both index-capture sites in `git_commit`. A repository-wide grep
finds `stdout_lossy` consumed in exactly two places, both in
`src/mcp_chatgpt_sandbox/tools/git_commit.py` (lines 307 and 356).

`run_git_in_container` (`src/mcp_chatgpt_sandbox/git/service.py:175`) — the
shared read path behind `git_status`, `git_diff`, `git_log`, and
`read_committed_files` — guards the *truncation* flag and not the *lossy*
one:

```python
if captured.stdout_truncated:
    raise CommandFailedError(
        f"git … produced more output than the daemon can capture …"
    )
return GitCommandResult(returncode=captured.exit_code, stdout=captured.stdout, …)
```

A non-UTF-8 path therefore reaches every downstream parser with U+FFFD
substituted, silently — the same defect class AR-001 closed, at the
surface AR-001 did not cover.

**Consequence is misreporting, not data loss.** These are read tools; none
of them writes an index or an argv derived from the decoded path, which is
what made AR-001's two sites destructive. The result is a path reported to
the client that does not round-trip to the real file, with no indication
the value is degraded. That is materially less severe than AR-001 and
SHALL be described that way rather than inflated to match it.

**Requirement.** `run_git_in_container` SHALL treat a lossy decode the way
it treats a truncated capture: a structured, translated error naming the
condition, rather than a silently degraded return value. The error SHALL
be distinguishable from the truncation error, since the operator remedies
differ (raise `max_output_bytes` vs. a path the tool cannot represent).

Verified by GAT-004.

### GR-007 — Redaction output SHALL always be JSON-serializable

`redact_secrets` output feeds `json.dumps` at two call sites:
`errors.py:478` (`to_tool_error`) and `audit.py:83` (the audit record).
<!-- X20 (`docs/GAP2_TODO.md`) corrected this line number from a stale
`:389` (later found already stale itself at `:412` by the time this
task ran — re-verified against HEAD, not the TODO's own suggested
replacement, per this document's own "do not run a mechanical
rewrite pass" rule: a citation fix has to check the actual current
line, not trust an intermediate claim). -->
V5 made `_redact` preserve container types — correct for `tuple`, which
serializes as a JSON array, but `set` does not serialize at all.
Reproduced directly:

```
to_tool_error({'tags': {'a','b'}})      -> TypeError: Object of type set is not JSON serializable
to_tool_error({'payload': b'\x00\x01'}) -> TypeError: Object of type bytes is not JSON serializable
to_tool_error({'t': (1,2)})             -> OK
```

A `TypeError` from `to_tool_error` escapes `translate_workspace_errors`
(which catches only `WorkspaceError`) as an unhandled crash; from
`audit.py` it kills the audit write.

**No caller constructs a `set` or a raw `bytes` in `details`/`metadata`
today** — this is latent, not live, and SHALL be described as latent. But
the in-code justification for the `frozenset`/`memoryview`/namedtuple
exclusions is precisely "no caller constructs one today", and here that
same reasoning produced a *worse* outcome than the alternative: coercing
`set` to `list` would have been both JSON-safe and lossless for this
purpose, whereas preserving set-ness guarantees a crash the first time a
caller appears.

**Requirement.** Every value `_redact` can return SHALL be JSON-serializable,
or `redact_secrets`' contract SHALL be narrowed in code to state which
input types it accepts and the call sites SHALL enforce it. The
non-secret `bytes`/`bytearray` passthrough (`errors.py:344-347`) is
in scope: it returns the original object, which `json.dumps` also
rejects.

`frozenset`, `memoryview`, and namedtuple-degradation remain deliberate,
documented exclusions and are **out of scope** — with one correction to
their documentation required, see GR-008.

Verified by GAT-005.

**Correction (X20, `docs/GAP2_TODO.md`): the paragraph above and the
Requirement paragraph immediately before it contradict each other, and
the Requirement governs.** "Every value `_redact` can return SHALL be
JSON-serializable" and "`frozenset`/`memoryview`/namedtuple-degradation
… are out of scope" cannot both be true of the same requirement — the
carve-out reads as an exemption from JSON-serializability itself, which
is exactly what the Requirement forbids. `src/mcp_chatgpt_sandbox/
errors.py`'s current `_redact` (G2R-004, `docs/GAP2_SPEC.md` — landed
after this document, in the same X1–X22 pass this correction belongs
to) resolves this in code, not just in prose: every explicitly
recognized type is redacted or passed through, and anything this
function does not recognize — `frozenset`, `memoryview`, and (the
detail worth stating precisely, since the residue was never actually
three named types) *every other type outside that explicit set* —
`Decimal`, `Path`, `datetime`, a dataclass, whatever a future caller
reaches for next — becomes a `f"[UNREPRESENTABLE: {type(value).
__name__}]"` string. A `str` is always JSON-serializable, so the
Requirement holds for the full, open-ended residue, not merely the
three types GR-008 names — "out of scope" above described a real gap
at the time this document was written, but described it too narrowly,
and is now stale as an exemption from serializability specifically:
`frozenset`/`memoryview` are only exempt from *content-aware*
redaction (no secret-shaped-content check the way `bytes` gets), never
from the Requirement itself.

---

## Phase 3 — Reference, test, and documentation integrity

### GR-008 — A cross-reference SHALL name the module that holds its referent

The five file-splitting commits (`f9ead84`, `7ce029a`, `05fe306`,
`fb194f9`+`7a3f38a`, `fca1199`) moved code and tests without updating the
prose that points at them.

**In shipped source** — one occurrence, and it sits inside V1's own
correction paragraph:

| file:line | says | referent actually lives in |
| --- | --- | --- |
| `src/mcp_chatgpt_sandbox/git/commit.py:107` | `` `tools/git.py`'s `capture_index` `` | `tools/git_commit.py:260` (X20, `docs/GAP2_TODO.md`; was `:255`, re-verified against `HEAD`) |

Verified that the other `tools/git.py` references in `src/` are **still
accurate** — `audit_denials`, `git_pull_tool`, and `git_push_tool` all
genuinely remain in the hub — so this requirement covers exactly one
source-code line, not a sweep.

**In documentation:**

| file:line | says | actually |
| --- | --- | --- |
| `docs/REVIEW_TODO.md:104` | fix added in `tools/git.py` | `tools/git_commit.py` |
| `docs/REVIEW_TODO.md:115` | VAT-001 in `test_git_commit_tool.py` | `test_git_commit_tool_failure_modes.py` |
| `docs/REVIEW_TODO.md:196` | tests in `tests/filesystem/test_service.py` | file deleted; `test_service_read.py` |
| `docs/REMEDIATION_TODO.md:2008` | `test_async_execution_tools.py:635` | moved to `…_cancel.py`; line stale |
| `docs/REMEDIATION_TODO.md:2010` | `tests/filesystem/test_service.py:876,893` | file deleted; `test_service_list.py` |
| `docs/REMEDIATION_TODO.md:2012` | `tests/filesystem/test_service.py:266` | file deleted; `test_service_read.py` |
| `docs/AUDIT_TODO.md:412` | `tests/filesystem/test_service.py` | file deleted |

**Why the existing guard did not catch this.**
`tests/test_acceptance_test_coverage.py` audits that each acceptance-test
number is cited *somewhere* under `tests/` — by design it never checks
*where*. That is the right scope for what it does (it is a coverage
audit, not a link checker) and SHALL NOT be widened into a path checker;
the two concerns have different failure modes and different maintenance
costs.

**Requirement.** The stale references above SHALL be corrected. Per this
project's convention, a documentation reference describing *what a task
did at the time* is corrected with a note rather than silently rewritten
where the original was accurate when written; a reference to a file that
no longer exists SHALL be updated outright, since there is nothing
accurate left to preserve.

Additionally, `errors.py`'s exclusion comment SHALL be corrected: it
justifies the namedtuple degradation without noting the consequence that a
namedtuple's *field names* are never used as key hints, so
`P(user=…, password='hunter2')` under a non-secret key leaves the value in
the clear.

**This requirement SHALL NOT be satisfied by a mechanical path-rewriting
pass over all six documents.** Most path references in
`docs/HARDENING_TODO.md` and `docs/TODO.md` are historical accounts of
what a task touched at the time and are correct as history; rewriting them
to today's layout would destroy that. Only the seven rows above — all
created by the five split commits, all in documents describing *current*
state — are in scope.

### GR-009 — Controls asserted by a requirement SHALL have a test that exercises them

Two gaps, both found while verifying V4 and V5:

1. **The `.ssh` *directory* refusal is untested.** `entrypoint.sh:237`
   <!-- X20 (`docs/GAP2_TODO.md`): was `:209`, then claimed `:225` by an
   intermediate correction that was itself stale by the time this task
   ran (G3's own PATH block moved it further) — re-verified against
   `HEAD`, both here and for the other three line numbers in this
   paragraph, not carried forward from any prior claim. This gap is now
   closed: `test_at_gr009_a_symlinked_ssh_directory_planted_in_a_cache_
   volume_is_not_followed` (`tests/workspace/test_default_image.py`)
   covers it — this paragraph is left as history describing the
   original defect, per this document's own "historical citations are
   correct as history" rule, with only its line numbers corrected. -->
   (`[ -L "$HOME_DIR/.ssh" ]`) has no test. VAT-004
   (`tests/workspace/test_default_image.py:326`) exercises only the
   `config`-*file* refusal at line 242. Both refusals are required by
   VR-004; one is covered.
2. **The `set` redaction test cannot detect a type regression.**
   `tests/test_errors.py:195` <!-- X20: was `:194` --> asserts value equality only, and
   `{"[REDACTED]"} == frozenset({"[REDACTED]"})` is `True` in Python — so
   a `set`→`frozenset` coercion would pass. The sibling tuple test
   (`:216-222` <!-- X20: was `:198-204` -->) does assert `isinstance`, which is the shape to match.

**Requirement.** Both SHALL be covered: the `.ssh` directory refusal by a
real-container test following `test_at_vat004_*`'s own precedent, and the
`set` branch by an `isinstance` assertion matching the tuple test's.

Verified by GAT-006 and GAT-007.

**Recorded, not required:** `id_mcp` is referenced by the generated SSH
config in `ssh.mode: file` (`entrypoint.sh:223`, `docs/SPEC.md:1754`) but
nothing in `src/` or `docker/` writes it today — confirmed by repo-wide
grep. There is no current symlink gap. Whoever implements file-mode key
provisioning SHALL add the same `[ -L ]` refusal the other two paths use.
This is a forward-looking note, not a defect, and no task in
`docs/GAP_TODO.md` closes it.

### GR-010 — A test SHALL fail when the behavior it asserts regresses

`tests/workspace/test_manager.py:61` asserts H31's requirement that
container lifecycle transitions are logged:

```python
with caplog.at_level("INFO", logger="mcp_chatgpt_sandbox.workspace.manager"):
```

`05fe306` moved the `_logger.info("...created")` and
`_logger.info("...deleted")` calls out of `manager.py` and into
`manager_lifecycle.py` (lines 283 and 552). `manager.py` no longer defines
a logger at all; `manager_core.py:38` and `manager_lifecycle.py:53` each
define their own.

`mcp_chatgpt_sandbox.workspace.manager_lifecycle` is a **sibling** of
`mcp_chatgpt_sandbox.workspace.manager` in the logging hierarchy, not a
child, so `at_level(..., logger="…manager")` does not enable it. Its
effective level falls back to the root logger's, and the INFO records are
dropped before reaching caplog.

**The test nevertheless passes in CI, which is what makes this
dangerous.** Measured:

| scope | result |
| --- | --- |
| `tests/workspace/test_manager.py` alone | **1 failed** |
| `tests/test_cli.py` + `tests/workspace/test_manager.py` | 40 passed |
| full suite (`tests/`) | 1003 passed |

The test asserts H31 and currently verifies nothing about it. It would
continue to pass in CI if the logging calls were deleted outright.

**Production impact is cosmetic and SHALL be described as such.**
`logging_config.py` configures the root logger with no per-module
handlers, so records still propagate and are still emitted; only the
`%(name)s` field changes from `…workspace.manager` to
`…workspace.manager_lifecycle`. H31's actual requirement — that the
transitions are logged — still holds in production. The defect is in the
test's ability to detect a regression, not in the logging itself.

**Requirement.** The assertion SHALL target a logger that actually covers
the emitting modules. `mcp_chatgpt_sandbox.workspace` is the common parent
of both `manager_core` and `manager_lifecycle` and satisfies this. The
test SHALL fail if the logging calls are removed, **independently of
which other tests ran first** — see GR-011, without which this cannot be
verified.

Verified by GAT-008.

### GR-011 — A test SHALL NOT leak global logging configuration

The reason GR-010's defect survived a full-suite run is a separate,
pre-existing defect that masks it.

`configure_logging` (`src/mcp_chatgpt_sandbox/logging_config.py:34`) calls
`logging.basicConfig(level=…, force=True)` on the **root** logger.
`force=True` is deliberate and correct for its actual caller
(`cli.py:86`, once at daemon startup). But `tests/test_cli.py` exercises
that path inside the pytest process and never restores the prior root
level or handlers, so every test collected after it inherits whatever
level the CLI config requested.

Proven by construction, not inferred: `tests/workspace/test_manager.py`
alone fails; prefixed with `tests/test_cli.py` it passes. In full-suite
collection order `tests/test_cli.py` sorts before `tests/workspace/`, so
CI always gets the passing order.

**This is broader than GR-010.** Any test that asserts on log records, or
asserts the *absence* of a log record, is order-dependent for the same
reason, and the suite has no guard against it — `pytest-randomly` is not
installed (confirmed against `pyproject.toml` and `uv.lock`), so a
favorable order is a permanent property of the suite rather than something
that would eventually surface.

**Requirement.** Tests that invoke `configure_logging` (directly or via
the CLI) SHALL restore the root logger's level and handlers afterwards. A
fixture in `tests/test_cli.py`, or an autouse fixture in `tests/conftest.py`
snapshotting and restoring root logging state, satisfies this.

This requirement SHALL be satisfied **before or together with** GR-010,
not after: until the leak is closed, GR-010's fix cannot be
revert-verified, because the reverted test would still pass in any run
that includes `tests/test_cli.py` first.

**Out of scope:** changing `configure_logging` itself. `force=True` is
correct for a daemon entrypoint called once per process, and its docstring
already explains why. The defect is that a test calls a
process-global configuration function without cleaning up, not that the
function is wrong.

---

## Acceptance tests

Each SHALL be an automated test. Numbering is independent of the `GR`
series: GR-002, GR-003, and GR-008 carry no `GAT`. GR-002's mechanism is
verified by its own existence plus CI agreeing with it rather than by a
pytest case; GR-003 and GR-008 are prose corrections, verified by
inspection.

- **GAT-001** — CI's own lint gate, run as CI runs it, passes at `HEAD`.
  This is satisfied by the existing CI workflow rather than a new test in
  `tests/`; the acceptance criterion is a green `uv run ruff check .` and
  `uv run ruff format --check .`, not a pytest case. Recorded as an
  acceptance test so its absence from `tests/` is a deliberate,
  documented choice rather than an oversight.
- **GAT-002** — A `ProjectWorkspaceConfig` whose `environment:` sets
  `PATH`, `BASH_ENV`, or an `LD_*` key is rejected at validation, with the
  offending key named. Host-side unit test; no container required.
- **GAT-003** — A container started with a hostile `PATH` in its
  environment still drops privileges: the workspace command reports a
  non-zero uid. Real-container test, following
  `tests/workspace/test_default_image.py`'s precedent. SHALL fail against
  the pre-fix entrypoint (the drop-bypass) and pass after.
- **GAT-004** — A `git_status`/`git_log` read over a repository containing
  a non-UTF-8 path raises a structured, translated error naming the lossy
  condition, rather than returning U+FFFD-substituted paths. Real-container
  test. The fixture SHALL create a path that is genuinely non-UTF-8 on
  disk, not one containing a literal U+FFFD, since `decode_utf8_lossy`
  distinguishes the two by design.
- **GAT-005** — `to_tool_error` and the audit writer both produce valid
  JSON for `details`/`metadata` containing a `set` and a non-secret
  `bytes` value.
- **GAT-006** — A cache volume mounted at `.ssh` whose mount point is
  itself a symlink causes the container to refuse to start, naming the
  symlink. Real-container test, sibling to `test_at_vat004_*`.
- **GAT-007** — `_redact` on a secret-flagged `set` returns a `set`,
  asserted by `isinstance`, not by equality.
- **GAT-008** — `tests/workspace/test_manager.py`'s H31 assertion fails
  when the lifecycle logging calls are removed, **and does so when the
  file is run on its own** — not only in an order that happens to have
  configured logging first. The acceptance criterion has two halves that
  SHALL both be demonstrated: the file passes standalone after the fix,
  and the assertion fails standalone if the `_logger.info` calls are
  removed.

**Correction (X20, `docs/GAP2_TODO.md`): GAT-006 is unsatisfiable as
written, and GAT-007 contradicts GR-007.**

- **GAT-006.** A Docker volume mount always creates a real directory at
  its own mount point — the mount point itself can therefore never be a
  symlink, so "a cache volume mounted at `.ssh` whose mount point is
  itself a symlink" describes a state Docker's own mount mechanics make
  unreachable, not a bug this codebase could exhibit or a test could
  provoke. What GAT-006 was actually written to cover — the sibling
  refusal one line below `entrypoint.sh`'s `.ssh/config` check, `[ -L
  "$HOME_DIR/.ssh" ]` for `.ssh` itself — is genuinely testable, just not
  the way GAT-006 phrases it: `cache_paths=["."]` (`.` normalizes to
  `$HOME`, not `.ssh`) mounts a persistent, container-writable volume at
  `$HOME` *containing* `.ssh` as an ordinary subdirectory, which an
  unprivileged run can replace with a symlink — the mount point (`$HOME`)
  stays a real directory throughout; only its *content* becomes a
  symlink. `test_at_gr009_a_symlinked_ssh_directory_planted_in_a_cache_
  volume_is_not_followed` (`tests/workspace/test_default_image.py`)
  exercises exactly this, and already carries the reasoning above in its
  own docstring — it is cited under `GR-009`, not `GAT-006`, matching
  this document's own note (§ "Acceptance tests" above) that GAT
  numbering is independent of GR and some GR requirements carry no GAT.
- **GAT-007** says `_redact` on a secret-flagged `set` "returns a `set`,
  asserted by `isinstance`." That is the pre-fix defect this same
  document's GR-007 exists to close, not the post-fix behavior: a `set`
  has no JSON representation at all, so `_redact` coerces it to a `list`
  (GR-007's own text, four sections above: "coercing `set` to `list`
  would have been both JSON-safe and lossless"). The actual test —
  `test_at_vat005_a_secret_flagged_set_recurses`
  (`tests/test_errors.py`) — asserts `isinstance(redacted["secret"],
  list)`, and its own docstring says so explicitly: "this assertion used
  to expect a `set` back; that was the defect, not this test." GAT-007's
  acceptance criterion has the assertion's target backwards; the test
  is, and always was, correct.

---

## Definition of done

`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run mypy src` all pass at `HEAD`; a divergent local toolchain can no
longer reach a push undetected; project-authored `environment:` cannot
reach the entrypoint's interpreter or command resolution, and the
entrypoint does not depend on inherited `PATH` even if it could; a lossy
capture cannot reach a strict parser through any call path; redaction
output is always JSON-serializable; every reference corrected here names a
referent that exists; `tests/workspace/test_manager.py` passes when run on
its own and fails if the behavior it asserts is removed; no test leaves
global logging state altered for the tests that follow it;
GAT-001..GAT-008 all pass; and no claim in `docs/GAP_TODO.md` was written
without being re-run against the code first (RR-033).
