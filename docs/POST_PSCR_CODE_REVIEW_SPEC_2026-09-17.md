# Post-PSCR Code Review Specification

**Status:** Proposed  
**Date:** 2026-09-17  
**Reviewed baseline:** `134795429eae988f927e43ec97cc04c5f98b867a`  
**Source review:** adversarial re-read of the merged `PSCR-001`–`PSCR-012` implementation against `docs/POST_SWPH_CODE_REVIEW_TODO_2026-09-17.md`  
**Task series:** `PSCR2-001` through `PSCR2-008`  
**Validation series:** `PSCR2V-*`

## 1. Purpose

`docs/POST_SWPH_CODE_REVIEW_TODO_2026-09-17.md` (PSCR-001→012) closed on green exact-head CI,
and a re-read of the merged result confirms every task is substantively implemented: the bundled
Docker build context resolves by a self-validating search instead of parent-index arithmetic, a
present image validates the Docker step without a context, the three new static guards parse rather
than grep and each is pinned by a negative test, the workspace assertions are scoped to the running
test context, and every naming vestige is gone.

That re-read nevertheless found one genuine correctness gap, four heuristic boundaries worth
recording, and one piece of documentation that the PSCR work itself made false. None invalidates a
PSCR task. This specification closes them at the same standard the PSCR series set: every fix gets a
regression test proven to discriminate, every declined fix is recorded as declined rather than
ticked, and every claim in a document matches what the code does.

## 2. Review baseline and evidence

The reviewed tree is:

```text
134795429eae988f927e43ec97cc04c5f98b867a
```

The last completed exact-head CI before it is `35295637675` on `16b7f9d` — conclusion success,
`1428 passed`, coverage `88%` (`9327` statements / `1121` misses). At authoring time the run for the
baseline commit itself (`35297412776`, a documentation-only change) is queued.

The reviewed tree passes locally: `uv run ruff format --check .` (317 files), `uv run ruff check .`,
`uv run mypy src` (strict, 103 files), and the 73 tests in the PSCR regression and guard files.
The exported MCP contract is byte-identical to `schemas/tools.json` at `contract_version` `0.10.3`.

The findings below are therefore not failures of the suite. Finding 3.1 is a gap the suite cannot
observe because the failing configuration is not one a checkout can produce; the rest are boundaries
of heuristics, and one is a document contradicted by a proof this series itself recorded.

## 3. Findings to remediate

### 3.1 `_source_checkout_root()` accepts any project's checkout

`setup/docker_setup.py`:

```python
for candidate in Path(__file__).resolve().parents:
    if (candidate / "pyproject.toml").is_file() and (candidate / "docker" / "default").is_dir():
        return candidate
```

The search is self-validating in one direction — it cannot return a root without a build context —
but it never checks that the `pyproject.toml` it found belongs to **this** project. Any ancestor
holding *a* `pyproject.toml` and *a* `docker/default/` satisfies it. An installed distribution
sitting beneath an unrelated project with that layout would resolve the wrong context and build an
image from it.

The probability is low. The check is nonetheless weaker than what PSCR-002's docstring implies
("cannot report a root that does not hold the context" is true; "found this project's checkout" is
not what it establishes).

### 3.2 The in-repo-virtualenv case carries an unacknowledged version skew

PSCR-002's docstring states that a non-editable install into `<repo>/.venv/...` resolving the
enclosing `<repo>` is "intended: the checkout genuinely is present, and the context it names is
genuinely usable." That second clause is not guaranteed. Nothing ties the context found on disk to
the package version that is actually running:

- an older wheel installed into a newer checkout builds an image from a `docker/default/` the
  running code was never written against;
- the reverse — a newer wheel over an older checkout — is the same mismatch in the other direction.

A checkout with uncommitted `Dockerfile` edits is a *different* case: same version, different
context. That is ordinary Dockerfile development and is arguably exactly what an operator wants, so
it is out of scope here. The skew this finding names is between **package version** and
**checkout version**, and it is currently silent.

### 3.3 `containers_in_this_context()` depends on an implicit docker-py default

`tests/workspace/conftest.py`'s helper reads `container.labels` on every result of
`client.containers.list(all=True, filters=...)`. In docker-py, `Container.labels` raises
`DockerException` for **sparse** objects ("Label data is not available for sparse objects"). It is
safe today only because `containers.list()` defaults to `sparse=False`. That dependency is not
stated, so a future `sparse=True` — a natural optimisation for a list that only needs names — would
break every assertion PSCR-010 converted, at a distance from the change.

### 3.4 The working-directory guard matches only call nodes

`tests/setup/test_setup_static_audit.py`'s `_working_directory_reads()` flags `ast.Call` nodes whose
callee is `.cwd`/`.getcwd`/`.getcwdb`. A reference to `os.getcwd` that is *passed* rather than
called — as a callback or default — is not flagged. Exotic in this codebase, but the guard's name
promises "never reads the process working directory" and this is a way to read it that the guard
does not see.

Extending the guard to every `.cwd` attribute reference would over-reach: `cwd` is a plausible data
field name, and an uncalled `record.cwd` is not a working-directory read. The gap is specifically
the **module-qualified** `os.getcwd` / `os.getcwdb` reference, which has no such ambiguity.

### 3.5 The `.parents[<int>]` guard is a policy presented as a regression guard

`test_pscrv_009_production_has_no_layout_dependent_build_context` flags **any** `.parents[<int>]`
subscript anywhere in the production package. It was written for two specific defects, but its
reach is general: a future, legitimate `some_path.parents[1]` on a path that has nothing to do with
the repository layout will trip it. That is a defensible choice — it forces an explicit decision,
the way the presentation-module manifest does — but the guard's docstring describes it as closing
two known sites, not as a standing policy. A reader who trips it should learn the policy from the
guard, not discover it by argument.

### 3.6 The write-mode heuristic is receiver-agnostic

`_looks_like_write_mode()` / `_opens_for_writing()` in the same file flag any `.open(...)` call
carrying a write-mode string constant. That correctly catches `Path.open("w")` and the builtin
`open(path, "w")`, which PSCR-007 targeted. It would equally flag `tarfile.open(p, "w")` or
`gzip.open(p, "wb")`, which are not configuration persistence.

No presentation module calls `.open(` at all today, so the live false-positive exposure is zero.
Distinguishing receivers reliably needs type information an AST guard does not have. This is a
boundary to record, not a defect to fix.

### 3.7 The no-concurrent-suite rule is now false

PSCR-010 scoped every absence and exact-count assertion to the running test context, and its
closure evidence records the proof: two `tests/workspace` sessions run concurrently from the same
tree both passed 438 tests, and a controlled A/B against the pre-fix tree failed 6 of 435 under
identical contention while the post-fix tree passed. Concurrent runs no longer collide.

Four documents still state the opposite, in the imperative:

- `CLAUDE.md` — "Never run the suite while another copy of it, or a live server, is running.
  Workspace names are deterministic for crash recovery, so two processes adopt the same container."
- `docs/DEVELOPMENT.md` §"Full-suite Docker isolation warning" — "Do not run the full
  workspace/container test suite concurrently … two live processes using the same IDs can adopt or
  reconcile the same container."
- `.claude/skills/gates/SKILL.md` — "two processes adopt the same container and produce
  lifecycle-shaped failures."
- `.claude/skills/lint-n-test/SKILL.md` — "a CI run and a local suite run compete for the same
  deterministic workspace names and adopt each other's containers."

Every one of those sentences was accurate when written and was falsified by PSCR-010. The
*mechanism* they describe is gone. A residual, weaker concern survives: the CI runner is
self-hosted on the same machine, so concurrent runs contend for CPU, Docker, and wall-clock even
though they no longer contend for containers. The documents should say that, and stop saying the
other thing.

## 4. Governing invariants

- `ServerConfig`, `GitPolicyConfig`, and `load_config()` remain authoritative; no setup-only schema.
- Preserve the PSCR-001 policy: the bundled Docker build context is a source-checkout asset, not a
  packaged one. Nothing here reopens Option A.
- Preserve PSCR-003: a present configured image validates the Docker step without a build context.
- Preserve reviewed config-write semantics and the SWPH-006 lock design unchanged.
- Preserve leaf-private directory semantics exactly as `docs/CONFIGURATION.md` states them.
- Preserve G3R-015 test-resource namespacing and PSCR-010's scoped assertions; no assertion may be
  weakened to absence-of-namespace or count-of-anything.
- Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- Add a focused regression test for every correctness defect, and prove it discriminates.
- A declined fix is recorded as declined under VR-000b (`docs/REVIEW_SPEC.md`); its checkbox is
  worded for what was done, never ticked for what was considered.
- Do not weaken existing tests or architecture guards; a replacement must be strictly stronger.
- Do not bump `contract_version` unless the public MCP contract actually changes.

## 5. Required remediation

### PSCR2-001 — Make the checkout search project-specific

`_source_checkout_root()` must confirm the `pyproject.toml` it finds declares
`[project] name = "mcp-chatgpt-sandbox"` before returning that root. A candidate with the right
layout but the wrong name is skipped, not accepted, and the search continues upward. Parsing uses
`tomllib` (standard library; already used by the test suite, new to `src/`). A found-but-foreign
root must produce a *distinct* actionable error from "no checkout found" only if that distinction
changes what the operator should do; otherwise one message is preferable to two.

### PSCR2-002 — Dispose of the in-repo-virtualenv version skew

Choose exactly one and record it:

- **Option A — document only.** State in the docstring and `docs/DEVELOPMENT.md` that a
  non-editable install into an in-repo virtualenv may build from a context that does not match the
  installed version, and that the operator is responsible for keeping them aligned.
- **Option B — detect and fail closed.** After PSCR2-001 finds the project's root, compare the
  running `mcp_chatgpt_sandbox.__version__` against the found `pyproject.toml`'s
  `[project] version`. On mismatch, raise a `SetupDockerError` naming both versions and the remedy.
  An editable install always matches by construction, so this only ever fires for the case that
  can actually skew.
- **Option C — refuse the case.** Treat a non-editable install resolving an enclosing checkout as
  unsupported and fail closed regardless of version.

Option B is recommended: it is cheap, it converts a silent mismatch into an actionable error, and
it leaves the same-version-dirty-checkout case (ordinary Dockerfile development) untouched. It does
change the behavior of a case PSCR-002 documented as "intended", which is why it is a recorded
decision rather than an assumption. Whichever option is chosen, the PSCR-002 docstring sentence
"the context it names is genuinely usable" must be reworded to say what is actually guaranteed.

### PSCR2-003 — Make the sparse-object dependency explicit

`containers_in_this_context()` passes `sparse=False` explicitly and its docstring states why: the
namespace filter reads `.labels`, which docker-py refuses on sparse objects. A test asserts the
helper's `list` call carries `sparse=False`, so the dependency is pinned rather than inherited.

### PSCR2-004 — Close the uncalled `os.getcwd` reference gap

`_working_directory_reads()` additionally flags a module-qualified `os.getcwd` / `os.getcwdb`
**reference** (an `ast.Attribute` whose value is the name `os`), whether or not it is called. It
does **not** extend to bare `.cwd` attribute references, for the reason in §3.4. Negative tests pin
both sides: an uncalled `os.getcwd` is flagged; an uncalled `record.cwd` is not.

### PSCR2-005 — State the `.parents[<int>]` guard's policy

The guard's docstring is rewritten to say that it deliberately forbids **every** integer subscript
of `.parents` in the production package as a standing policy, that the policy exists because two
such sites were each silently wrong for an installed layout, and what a future author who needs an
ancestor path should do instead (compute it from an explicit, documented anchor, and record the
decision). No code change; the docstring is the deliverable.

### PSCR2-006 — Record the write-mode heuristic's boundary

No code change. The `_opens_for_writing()` docstring records that the check is receiver-agnostic,
names the false-positive class (`tarfile.open`, `gzip.open`, and similar), states that no
presentation module currently calls `.open(` at all, and states that the fix — receiver typing — is
outside what an AST guard can do. Under VR-000b the corresponding TODO checkbox is worded as
"record the boundary", which is done, not "fix the heuristic", which is declined.

### PSCR2-007 — Correct the concurrency documentation

Choose exactly one and record it:

- **Option A — remove the rule.** Concurrent runs are proven safe; delete the four passages.
- **Option B — downgrade to advisory.** Replace the four passages with an accurate statement:
  concurrent suite runs no longer collide (PSCR-010, with its proof cited), but the CI runner is
  self-hosted on the same machine, so a local run alongside CI shares CPU and Docker and both run
  slower. Prefer not to overlap; overlapping is safe.

Option B is recommended. The *collision* claim is false and must go; the *contention* point is
still true. Deleting a warning outright immediately after a documented violation of it, when part
of its substance survives, is the wrong direction. All four locations change together in one
commit, so the documents cannot disagree with each other about this again.

### PSCR2-008 — Final regression, contract, and closure

Re-verify every touched boundary, confirm the public MCP contract unchanged by re-export and
byte-for-byte diff, run the full Docker-backed suite, and close only on green exact-head CI.
Because PSCR2-007 makes concurrent runs officially acceptable, this closure run may overlap CI —
and doing so deliberately, once, is itself evidence for PSCR2-007's claim.

## 6. Validation strategy

- Every task carries a `PSCR2V-*` validation set; correctness defects get regression tests proven
  to discriminate by re-running them against the pre-fix code.
- PSCR2-001's test must use a decoy: a synthesized ancestor whose `pyproject.toml` has the right
  layout and the wrong `[project] name`, which the search must skip.
- PSCR2-002 (if Option B) must test both the mismatch and the match, and must show an editable
  install cannot trigger it.
- PSCR2-004's negative tests must show the extension does **not** flag `record.cwd`, so the guard
  did not become a false-positive generator in the course of closing a gap.
- The full Docker-backed suite must pass. Overlap with a CI run is permitted under PSCR2-007's
  disposition and, if it occurs, is recorded as corroborating evidence rather than as a violation.

## 7. Contract-version decision

None of these findings touch the public MCP tool surface. The exported contract is expected to
remain byte-identical to `schemas/tools.json` at `contract_version` `0.10.3`. PSCR2-008 confirms
this by re-export and diff rather than assumption.

## 8. Non-goals

- Reopening PSCR-001's Option A (packaging the Docker build context). If the package is ever
  published that becomes a new series, not an amendment to this one.
- Typing the receiver of `.open(...)` calls in the persistence guard (§3.6). Recorded, not fixed.
- Extending the working-directory guard to bare `.cwd` attribute references (§3.4). Deliberately
  excluded to avoid false positives on a plausible field name.
- Any change to the G3R-015 namespace mechanism itself. It works; PSCR-010 made the assertions use
  it, and this series only pins one of its dependencies.
- The permanently red CI run `35271467140` against `ed6132e`. It is explained in the PSCR TODO and
  has no repository-side remedy.
- The process failures recorded in the PSCR TODO (a rule violation, two guards wrong on first
  draft, a broken import heuristic). They are evidence, already recorded, and not defects a
  specification can close.

## 9. Definition of done

- The checkout search returns only this project's root, and a decoy with the right layout and
  wrong name is skipped.
- The in-repo-virtualenv version skew has a recorded disposition, implemented per the chosen option,
  and the PSCR-002 docstring no longer claims more than is guaranteed.
- `containers_in_this_context()` states and pins its `sparse=False` dependency.
- An uncalled `os.getcwd` reference in the setup package fails the working-directory guard; an
  uncalled `record.cwd` does not.
- The `.parents[<int>]` guard's docstring states its policy and the alternative.
- The write-mode heuristic's boundary is recorded in its docstring, with the TODO checkbox worded
  for the recording, not for a fix.
- All four concurrency passages say the same, accurate thing, in one commit.
- Every regression test added here has been shown to fail against the pre-fix code.
- Public MCP contract confirmed unchanged by re-export and diff.
- Exact-head CI green; post-`master` CI green.
- Exact closure evidence recorded in `docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md`.
