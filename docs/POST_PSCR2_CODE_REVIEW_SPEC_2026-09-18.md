# Post-PSCR2 Code Review Specification

**Status:** Proposed  
**Date:** 2026-09-18  
**Reviewed baseline:** `a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1`  
**Source review:** adversarial re-read of the merged `PSCR2-001`–`PSCR2-011` implementation against `docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md`  
**Task series:** `PSCR3-001` through `PSCR3-010`  
**Validation series:** `PSCR3V-*`

## 1. Purpose

`docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md` (PSCR2-001→011) closed on green exact-head CI, and a
re-read confirms every task is substantively implemented and every regression test it added fails
against the pre-fix code. No production defect was found.

What the re-read found instead is residue in the series' **own record and test scaffolding**: a
specification that still describes a design the implementation rejected, evidence that cites line
numbers nobody verified, a new guard whose boundaries are unstated, a fixture that traded rename
safety for the ability to run against old code, and two behaviours that are safe but undocumented.

A second review pass, which measured branch coverage instead of reading, added two findings: the
function the version-skew check rests on is never executed by any test (§3.7), and project-name
matching is exact rather than normalised (§3.8).

None of this changes what the software does, with one recorded exception: PSCR3-009's Option A, if
chosen, widens which spellings of this project's own name are accepted. It matters because this repository treats its
specifications and TODO evidence as the durable account of why the code is the way it is, and the
PSCR and PSCR2 series both existed to make that account true. A record that is mostly right invites
exactly the misreading that led PSCR2 to cite dead code as a live protection.

## 2. Review baseline and evidence

The reviewed tree is:

```text
a5a35a1f1f049582f9596fc1bd9ffd7bf7ad65a1
```

The last completed exact-head CI before it is `35323036386` on `68a37fd` — conclusion success,
`1444 passed`, coverage `88%` (`9342` statements / `1108` misses). `a5a35a1` itself is a
documentation-only commit whose run (`35324127795`) is in progress at authoring time.

The reviewed tree passes `uv run ruff format --check .`, `uv run ruff check .`, and
`uv run mypy src` (strict). The exported MCP contract is byte-identical to `schemas/tools.json` at
`contract_version` `0.10.3`.

## 3. Findings to remediate

### 3.1 The PSCR2 specification contradicts its own TODO in two places

`docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md`:

- **PSCR2-002, Option B** still reads: "compare the running `mcp_chatgpt_sandbox.__version__`
  against the found `pyproject.toml`'s `[project] version` … An editable install always matches by
  construction." Implementation showed that invariant false — `__version__` is a hard-coded literal
  maintained separately from `[project] version` — and built the check on a different property
  (whether the running module lies under the checkout's `src/`), comparing the *installed
  distribution's* metadata version otherwise. The TODO records the deviation. The specification does
  not, so a reader of the specification alone learns the rejected design.
- **PSCR2-009** still reads: "the 'not demonstrated' caveat is removed from all four locations in
  one commit." Three locations carried the caveat; `CLAUDE.md` never mentioned a live server. The
  TODO says three.

The PSCR2 **TODO** has the mirror-image problem (found by a second review pass). Its introduction
still says the series closes "one correctness gap, four heuristic boundaries, and one falsified
document" — the scope of PSCR2-001→008 — and its Definition of done carries no entry for what
PSCR2-009→011 delivered: live-server isolation demonstrated, `sparse=False` pinned in production, and
the shadowed `reconcile()` body removed. Each is fully evidenced in its own section; the file's
opening and closing frames simply never grew with it, so a reader skimming top and bottom
underestimates what the file records.

### 3.2 PSCR2 TODO evidence cites line numbers that were not verified

`docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md` presents these as current:

| Cited | Actual at the baseline | What it points to |
| --- | --- | --- |
| `manager.py:213` | `manager.py:218` | `container.labels` read in the live `reconcile()` |
| `inventory.py:283` | `inventory.py:288` | `getattr(container, "labels", {})` |
| `manager_core.py:148` | `manager_core.py:145` | namespace check in `_find_container` |

The first was a pre-edit number that PSCR2-010's own change shifted; the second was never checked;
the third was shifted by PSCR2-011's import removal. Citations of `manager_core.py:197` and `:200`
elsewhere in that file refer to the since-deleted method and are explicitly historical, so they are
not in scope.

Bare line numbers are the wrong tool for durable evidence: every later edit to the file silently
falsifies them. The remedy is to cite the **symbol**, with the line number qualified by the commit
it was true at.

### 3.3 The `sparse=False` guard has unstated boundaries

`tests/test_docker_list_sparse_guard.py`'s `_unpinned_container_lists()` matches only the literal
shape `<expr>.containers.list(...)`:

- **Evasion.** `cs = self._docker.containers; cs.list(all=True)` is not flagged.
- **False positive.** `c.containers.list(**opts)` is flagged even when `opts` carries
  `sparse=False`, as is `sparse=<variable>`.

Neither shape exists in the production package today. Closing the evasion would need data-flow
analysis an AST walk does not have, and treating `**opts` as pinned would be unsound. This is the
same class of heuristic boundary PSCR2-006 recorded for the write-mode check, and it should be
recorded the same way rather than left to be rediscovered.

### 3.4 The version-pin fixture cannot detect a rename

`tests/setup/test_pscr_docker_context_policy.py`'s autouse fixture patches
`docker_setup._installed_version` with `raising=False`, so that the module can be run against the
code as it was before PSCR2-002 — which is how those tests were shown to discriminate. The cost is
that `monkeypatch.setattr(..., raising=False)` *creates* the attribute when it is absent. If
`_installed_version` is ever renamed, the fixture silently patches a name nothing reads, and every
test that depends on the pin falls back to the real installed version.

Those tests would keep passing — but only because the real version currently equals the pinned
`0.1.0`, which is precisely the coincidence the fixture was written to remove. They would then fail
on the next version bump for reasons unrelated to what they test.

### 3.5 The live-server test briefly creates a production-labelled container

`tests/workspace/test_pscr2_live_server_isolation.py`'s second test creates a container carrying
`mcp-chatgpt-sandbox.managed=true` and **no** namespace label, because impersonating a production
container is the point. For well under a second it is indistinguishable, by label, from a real
workspace, so a live `serve` on the same daemon could list it in `resource_inventory`.

It is harmless: the container has a per-run repository id no real configuration contains, no
workspace row exists for it in any real server's database so it cannot be adopted, and the suite's
exact-resource teardown (HR-026) removes it. But the test says none of this, and "a test creates
production-labelled resources on a shared daemon" is the sort of thing a reader should not have to
reason out for themselves.

### 3.6 Nothing exercises the path that depends on the `reconcile()` declaration

PSCR2-011 replaced `_CoreMixin.reconcile()`'s dead body with a declaration that raises
`NotImplementedError`. `_CoreMixin.reconcile_all()` — the §44.1 startup sweep — calls
`self.reconcile(...)` and relies on that resolving to `DockerWorkspaceManager`'s override.
`tests/workspace/test_pscr2_reconcile_declaration.py` pins that the override *exists* and that the
declaration *raises*, but no test shows the sweep actually *dispatches* to the override.

The failure mode is loud rather than silent — `reconcile_all()` catches only
`ContainerRuntimeFailedError`, so a `NotImplementedError` would propagate at server startup rather
than be swallowed as a skipped workspace — but it would be discovered at startup, not in CI.

### 3.7 `_installed_version()` is never executed by any test

Found by a second review pass, using branch coverage rather than reading. `_installed_version()`
(`setup/docker_setup.py`) is the function PSCR2-002's skew check reads the running package's version
from. Its lines have **zero coverage**: the autouse fixture in
`tests/setup/test_pscr_docker_context_policy.py` replaces it in every test in that module, and
`test_pscr2v_002_the_real_editable_checkout_passes` is in-tree, so the check is skipped before the
lookup is reached. Neither the real `importlib.metadata.version(...)` call nor the
`PackageNotFoundError -> None` branch ever runs under test.

Invoked by hand it works — it returns the installed version, and the missing-distribution branch is
reachable. But the whole check rests on it, and the failure mode is quiet: if `_PROJECT_NAME` ever
stopped matching the distribution's metadata name, the function would return `None`, every
non-editable install beside a checkout would be refused with "unknown version", and no test would
notice. This is adjacent to §3.4 but not the same: §3.4 is about the *fixture* surviving a rename;
this is about the *function beneath the fixture* having no test of its own.

### 3.8 Project-name matching is exact, not normalised

Also from the second pass. PSCR2-001 accepts a checkout only when
`project.get("name") == "mcp-chatgpt-sandbox"`. Probed directly, `mcp_chatgpt_sandbox` and
`MCP-ChatGPT-Sandbox` are both **rejected**, although Python packaging treats all three as the same
project name (PEP 503 normalisation: lower-case, runs of `-`, `_`, `.` collapsed to `-`) and
`importlib.metadata` resolves any of them.

Nothing is wrong today — this repository's `pyproject.toml` uses the exact string. The cost is a
silent cliff with a misleading symptom: restyle the name in `pyproject.toml` and the search stops
finding *its own* checkout, reporting "ships only in a source checkout" from inside a source
checkout.

## 4. Governing invariants

- No production behavior changes in this series, with **one recorded exception**: PSCR3-009, if its
  Option A is chosen, makes the checkout search accept PEP 503-equivalent spellings of this
  project's own name. It accepts strictly more spellings of the *same* project and no other
  project, so every checkout accepted today is still accepted. PSCR3-004, PSCR3-006 and PSCR3-008
  add test code; everything else is documentation or docstrings. (This invariant originally read
  "no production behavior changes"; it was amended when PSCR3-009 was added, rather than left
  contradicted.)
- Preserve every PSCR and PSCR2 outcome: checkout-only Docker context, project-specific checkout
  search, installed-version skew check, namespace-scoped assertions, pinned `sparse=False`, the
  `reconcile()` declaration.
- Historical record is corrected **visibly**, never silently: a corrected citation or specification
  passage says that it was corrected, by which task, and what it said before where that matters.
- Citations of code locations name the symbol; a line number, if given, is qualified by the commit
  it was verified at.
- A declined fix is recorded as declined under VR-000b (`docs/REVIEW_SPEC.md`); its checkbox is
  worded for what was done.
- Add a regression test for every behavioral claim, and prove it discriminates.
- Do not weaken existing tests or guards. Do not bump `contract_version`.
- Coverage (`--cov`) suite runs use an idle host; non-coverage runs may overlap CI
  (`docs/DEVELOPMENT.md`, "Running the suite concurrently").

## 5. Required remediation

### PSCR3-001 — Reconcile the PSCR2 specification with what was built

Amend `docs/POST_PSCR_CODE_REVIEW_SPEC_2026-09-17.md` in place, visibly:

- PSCR2-002 Option B describes the implemented mechanism — skip when the running module lies under
  the checkout's `src/`, otherwise compare the installed distribution's metadata version — and
  states that the original `__version__` wording was found false during implementation.
- PSCR2-009 says three locations, and why not four.

The PSCR2 TODO's deviation notes stay as they are; they are the record of *how* the difference was
found.

Also bring the PSCR2 TODO's frame up to its final scope: its introduction says what PSCR2-009→011
added and that they were added after the original closure, and its Definition of done gains one
entry per post-closure outcome. Existing entries and evidence are not reworded.

### PSCR3-002 — Replace unverified line citations with verified, durable ones

In `docs/POST_PSCR_CODE_REVIEW_TODO_2026-09-17.md`, replace the three citations in §3.2 with the
symbol each refers to plus a line number qualified by the commit it was verified at. Verify each by
reading the file at that commit, not from memory. Leave the explicitly historical `:197`/`:200`
citations alone.

### PSCR3-003 — Record the `sparse=False` guard's boundaries

No code change. `_unpinned_container_lists()`'s docstring records the evasion (an aliased
collection) and the false-positive class (`**opts`, `sparse=<variable>`), states that neither shape
exists in production today, and states why neither is fixed. The existing negative test gains
assertions that pin both boundaries as *known* behavior, so a future change to the detector cannot
move them unnoticed. Under VR-000b the checkbox is worded for the recording.

### PSCR3-004 — Make the version-pin fixture rename-safe

Keep `raising=False` — it is what lets the module run against pre-PSCR2-002 code — but make the
fixture assert that `docker_setup._installed_version` exists **whenever the PSCR2-002 check itself
exists** (`_check_context_matches_installed_version`). Against pre-fix code neither exists and the
fixture still permits the run; against current code a rename fails loudly in fixture setup instead
of silently unpinning.

### PSCR3-005 — Document the production-labelled test container

No code change. The second live-server test's docstring states that it creates a
production-labelled container on the shared daemon, how briefly, and the three reasons that is
safe: a per-run repository id, no workspace row in any real server's database, and exact-resource
teardown.

### PSCR3-006 — Exercise the startup sweep's dispatch to the override

Add a Docker-free test: a `DockerWorkspaceManager` with one workspace row and a fake Docker client
runs `reconcile_all()`, and the test asserts the concrete `reconcile()` was what ran and the
`_CoreMixin` declaration was never reached. It must fail if the override is removed.

### PSCR3-008 — Test `_installed_version()` itself

Added after the second review pass. Two Docker-free tests that **bypass** the autouse pin and run the
real function: one asserts it returns the version `importlib.metadata` reports for this
distribution; one makes the distribution lookup raise `PackageNotFoundError` and asserts `None`. The
bypass must be explicit and local — the pin stays autouse for every other test — and must not
weaken PSCR3-004's rename assertion.

### PSCR3-009 — Dispose of exact project-name matching

Added after the second review pass. Choose exactly one and record it:

- **Option A — normalise.** Compare PEP 503-normalised names on both sides (lower-case; runs of
  `-`, `_`, `.` collapsed to `-`), so every equivalent spelling of this project's name is accepted
  and nothing else is. A small production change, and the recorded exception to this series'
  no-behavior-change invariant.
- **Option B — keep exact, on the record.** Leave the comparison as it is and state in
  `_source_checkout_root()`'s docstring that matching is deliberately exact, what the symptom of a
  restyled name would be, and where to change it.

Option A is recommended: the normalisation rule is standard, a dozen characters of code, and removes
a cliff whose symptom points away from its cause. Option B is the honest fallback if no production
change at all is wanted in this series.

### PSCR3-010 — Reconcile three stale checkboxes in older TODOs

Added after closure, on request. A survey of every TODO in `docs/` found three unticked boxes whose
stated reasons later work had overtaken. Each was checked against the code before being touched,
and they turned out to need three different dispositions:

- **`docs/HARDENING_TODO.md`, "the collision half is not [fixed]".** Satisfied by later work — and
  by *two* tasks, not one. The `409 Conflict` on the deterministic container name that the entry
  describes was removed by G3R-015, which folds a per-context namespace into `container_name()`.
  PSCR-010 closed a second layer G3R-015 did not reach: assertions filtering on logical-id labels.
  Crediting PSCR-010 alone, as a first reading suggested, would have been wrong.
- **`docs/TODO.md`, "Disk usage reporting and prune operations — not implemented".** Superseded by
  the post-MVP operations workstream: prune by O5–O8, disk reporting by O2/O3 and O4. One gap
  remains and is deliberate — container sizes are reported as unavailable.
- **`docs/TODO.md`, "on overrun promote to a job — deferred: … job store … does not exist until
  S14".** *Not* done: `promoted_to_job` appears nowhere in the code or the exported contract. But its
  rationale is stale — S14 landed — and `execution/service.py`'s docstring repeats it. §14.4 makes
  promotion a MAY, so the current `COMMAND_TIMEOUT` behavior is compliant.

Tick the first two, each with a visible note naming the task that did the work. Leave the third
unticked, under VR-000b, and correct its rationale in both places. Original wording and the
explanatory paragraphs beneath each entry are left as written; they were accurate when written.
The `service.py` change is a docstring only.

### PSCR3-007 — Final regression, contract, and closure

Gates, contract re-export and byte-diff, the full Docker-backed suite, exact-head CI.

## 6. Validation strategy

- PSCR3-004's assertion must be shown to fire: simulate the rename and show fixture setup fails;
  show the module still runs against pre-PSCR2-002 `docker_setup.py`.
- PSCR3-006's test must be shown to discriminate: with the override removed it must fail.
- PSCR3-003's new assertions pin *current* detector behavior, so they pass before and after by
  design; that is stated rather than presented as discrimination.
- PSCR3-002's numbers are verified by a command whose output is recorded, not by reading.
- PSCR3-008's tests must be shown to execute the real function: branch coverage of
  `_installed_version()` goes from zero to complete, recorded from a coverage run.
- PSCR3-009 (if Option A) must be shown to discriminate: the equivalent-spelling test fails against
  the exact comparison, and the wrong-project decoy tests from PSCR2-001 still pass unchanged.
- Documentation tasks are validated by grep for the old wording and by the repository's own
  acceptance-coverage and `docs/TOOLS.md` drift guards staying green.

## 7. Contract-version decision

No task touches the public MCP tool surface or any production code path. `contract_version` is
expected to remain `0.10.3`; PSCR3-007 confirms by re-export and diff.

## 8. Non-goals

- Closing the `sparse=False` guard's evasion with data-flow analysis (§3.3). Recorded, not fixed.
- Removing `raising=False` from the version-pin fixture. It is load-bearing for discrimination runs.
- Changing the live-server test to avoid production labels. Impersonating production is its purpose.
- Rewriting the explicitly historical citations of the deleted `_CoreMixin.reconcile()` body.
- The process failures recorded in the PSCR and PSCR2 TODOs. They are evidence, not defects.
- Everything the PSCR2 specification already listed as a non-goal, including packaging the Docker
  build context and typing the receiver of `.open(...)` calls.

## 9. Definition of done

- The PSCR2 specification describes the mechanism PSCR2-002 actually built, and says three
  locations for PSCR2-009; both corrections are visible as corrections.
- The three line citations name their symbols and carry verified, commit-qualified line numbers.
- The `sparse=False` guard's boundaries are recorded in its docstring and pinned by assertions.
- A rename of `_installed_version` fails the version-pin fixture loudly, and the module still runs
  against pre-PSCR2-002 code.
- The live-server test documents its production-labelled container and why it is safe.
- A test shows `reconcile_all()` dispatches to the concrete override, and fails without it.
- The PSCR2 TODO's introduction and Definition of done reflect PSCR2-009→011.
- `_installed_version()` is executed by tests on both branches, shown by coverage.
- Exact project-name matching has a recorded disposition, implemented per the chosen option.
- Public MCP contract confirmed unchanged by re-export and diff.
- Exact-head CI green.
- Exact closure evidence recorded in `docs/POST_PSCR2_CODE_REVIEW_TODO_2026-09-18.md`.
