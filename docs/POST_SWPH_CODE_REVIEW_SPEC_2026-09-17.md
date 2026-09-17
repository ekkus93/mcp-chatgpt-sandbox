# Post-SWPH Code Review Specification

**Status:** Proposed  
**Date:** 2026-09-17  
**Reviewed baseline:** `ba79759a1ed9287105d9891bbb1cefccdc396d78`  
**Source review:** line-by-line code review of `docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md` against the merged implementation  
**Task series:** `PSCR-001` through `PSCR-008`  
**Validation series:** `PSCRV-*`

## 1. Purpose

`docs/SETUP_WIZARD_POST_HARDENING_REVIEW_TODO_2026-09-14.md` (SWPH-001→010) closed on green CI, and a
re-review of the merged result confirms every one of its ten tasks is substantively implemented: the
tunnel plan no longer carries caller-directory identity, Docker context failures stay inside the
Textual handlers, the Git-policy support manifest is genuinely independent of
`GitPolicyConfig.model_fields`, the reviewed config write is serialized across final verification and
replacement, and the operator documentation matches the implementation.

That re-review nevertheless found one functional defect that the SWPH closure could not have caught,
because the condition it depends on is masked by how this project is developed and tested, plus four
smaller correctness/fidelity gaps.

This specification defines the remediation. As with SWPH, the goal is not a rewrite; it is to make the
implementation truthful at one boundary it currently is not — the boundary between a source checkout
and an installed distribution — and to make three guards prove what they claim to prove.

## 2. Review baseline and evidence

The reviewed tree is:

```text
ba79759a1ed9287105d9891bbb1cefccdc396d78
```

At authoring time that commit's `master` CI (`35267974338`, job `105359813176`) is still in progress.
The last fully green run is `35267136216` on its immediate predecessor
`1e2105a498ee93cb6f1c892b2a7e5a44c4055826`.

The reviewed tree passes locally:

- `uv run ruff format --check .`: PASS (312 files);
- `uv run ruff check .`: PASS;
- `uv run mypy src`: PASS (strict, 103 source files);
- MCP exported-schema contract: byte-identical to `schemas/tools.json`, `contract_version` `0.10.3`;
- `uv run pytest`: `1411 passed`, zero skips;
- the seven SWPH regression/guard files: `41 passed`.

The findings below are therefore not failures of the existing suite. Finding 3.1 is a gap the suite
structurally cannot observe; the rest are gaps in what specific tests and guards prove.

## 3. Findings to remediate

### 3.1 The bundled Docker build context cannot resolve in an installed distribution, and is not packaged

`setup/docker_setup.py`'s `bundled_default_image_build_context()` resolves:

```python
context = Path(__file__).resolve().parents[3] / "docker" / "default"
```

That arithmetic is correct only for this repository's `src/` layout:

| Layout | `parents[3] / "docker" / "default"` | Exists |
| --- | --- | --- |
| Source checkout | `<repo>/docker/default` | yes |
| Installed wheel | `<venv>/lib/pythonX.Y/docker/default` | no |

Independently, `docker/default/` is not packaged at all. A wheel built from this tree contains 120
entries and no `Dockerfile`; `pyproject.toml` declares no `force-include` or equivalent data
configuration, and `docker/` sits outside the `src/` module root that `uv_build` packages.

The consequence is larger than a failed build. `setup/step_actions_docker.py`'s `validate_docker()`
builds the plan — and therefore resolves the build context — in its **first** `try` block, before
`image_exists()` is ever consulted. An installed deployment therefore cannot validate the Docker step
**even when the configured image is already present locally**, and the `#docker-defer-image` escape
hatch is unreachable because that checkbox is only read after plan construction succeeds. Per
`docs/DEVELOPMENT.md`'s own troubleshooting, an unvalidated step leaves Next disabled, so the wizard
cannot be completed.

This condition is invisible in development and CI: the project is installed editable
(`mcp_chatgpt_sandbox.pth`, with no real package directory in `site-packages`), so `__file__` points
into `src/` and the checkout path is always taken. `tests/setup/test_guided_domain.py` calls
`bundled_default_image_build_context()` for real and passes only for that reason, and the SWPH Docker
regressions monkeypatch the helper, so nothing exercises the installed layout.

SWPH-001 carried the subtask *"Ensure installed-distribution invocation works without a repository
checkout where supported."* The tunnel half of that is satisfied. The Docker half is not, and the
helper's own docstring — "resolved from this module's installed source tree" — asserts a property the
packaging does not provide.

### 3.2 Docker step validation is coupled to build-context availability

Independently of how 3.1 is resolved, `validate_docker()` conflates two questions: "are these Docker
workspace values valid, and is the image usable?" and "could this host build the bundled image?" An
operator who already has the configured image has no need of a build context, but cannot pass the
step without one.

This is the smallest change that unblocks a stuck wizard, and it remains correct regardless of the
packaging decision in 3.1.

### 3.3 The tunnel plan retains a vestigial `project_root` parameter with a live `Path.cwd()` caller

`setup/tunnel.py`'s `build_tunnel_profile_plan()` accepts `project_root` and immediately discards it
(`del project_root`), documented as retained "for source compatibility with callers from before
SWPH-001". `setup/step_actions.py` still computes `Path.cwd()` and passes it.

SWPH-001's intent holds — caller cwd is not the profile's identity — but the residue is an active
trap. It is dead work on every plan construction, it makes a `Path.cwd()` search of `setup/` read as
though SWPH-001 were unfixed, and nothing prevents a future caller from believing the argument is
honored. The stated compatibility rationale does not apply to a private helper with exactly one
in-repo call site and no external consumers.

### 3.4 SWPH-004's mutation-style tests are themselves tautological

`tests/setup/test_swph_git_policy_exhaustiveness.py` proves "a future production field fails the
contract" like this:

```python
future_fields = frozenset(FutureGitPolicyConfig.model_fields)
with pytest.raises(AssertionError):
    assert future_fields == GIT_POLICY_GUIDED_FIELDS
```

This asserts only that a locally constructed set differs from the manifest — `A | {x} != A`. It never
invokes the real exhaustiveness check, so it does not demonstrate that adding a field to the actual
`GitPolicyConfig` would fail anything. The stale-guided-field test has the same shape.

The real protection is the direct bidirectional equality at the top of that file, which *is* sound now
that `GIT_POLICY_GUIDED_CONTROLS` is an independent manifest. But SWPH-004's validation subtask claims
a mutation proof, and these two tests are a milder instance of precisely the tautology SWPH-004
existed to remove.

### 3.5 Two lower-severity defects

**Descriptor leak on the `fcntl`-unavailable path.** In `setup/config_writer.py`'s
`_config_commit_lock()`, `os.open()` of the lock file succeeds before `_fcntl_module()` is called, and
`_fcntl_module()` is invoked outside the `try/finally` that closes the descriptor. On a platform
without `fcntl` the fail-closed error is correct, but the descriptor leaks and an empty lock file has
already been created. Unreachable on Linux, the only supported platform.

**Substring-based presentation ownership guard.**
`tests/setup/test_setup_static_audit.py`'s persistence-ownership check greps raw source for tokens
such as `write_text(` and `import yaml`. It will trip on a comment that merely mentions a token, and
can be bypassed by an aliased import or `Path.open("w")`. Adequate as a tripwire, weaker than
"presentation modules do not own config persistence" implies.

## 4. Governing invariants

- `ServerConfig`, `GitPolicyConfig`, and `load_config()` remain authoritative; no setup-only schema.
- Preserve manual YAML configuration and existing CLI workflows.
- Preserve reviewed config-write semantics: review binding, serialization, backup, same-filesystem
  atomic replacement, byte verification, and production reload.
- Preserve the SWPH-006 lock design: one non-blocking attempt, persistent lock inode, no retry or
  sleep framework.
- Preserve leaf-private directory semantics exactly as `docs/CONFIGURATION.md` states them.
- Preserve explicit operator approval before any Docker mutation, and two-step reviewed apply.
- Preserve Docker-unavailable degraded behavior and fail-closed diagnostics.
- Never persist or print raw secrets in state, UI, diagnostics, logs, exceptions, docs, or tests.
- Add a focused regression test for every correctness defect.
- Do not weaken existing tests or architecture guards to make CI green.
- Do not bump `contract_version` unless the public MCP contract actually changes.

## 5. Required remediation

### PSCR-001 — Decide and record the installed-distribution Docker build policy

The fix for 3.1 depends on a policy choice that must be explicit, recorded, and reflected in code,
tests, and docs rather than left implied. Choose exactly one:

- **Option A — support installed Docker builds.** Package `docker/default/` into the distribution and
  resolve it through a packaging-aware mechanism (`importlib.resources`) instead of parent-directory
  arithmetic.
- **Option B — declare installed Docker builds out of scope.** Keep the context checkout-only, state
  that plainly where it is resolved and in operator documentation, and ensure the wizard degrades to
  an actionable state rather than a blocked one.

Either option requires PSCR-003. Option A additionally requires the distribution to actually carry the
context; Option B additionally requires removing the docstring claim that the context comes from an
"installed source tree".

### PSCR-002 — Make bundled build-context resolution correct for the selected policy

Resolve the context by a mechanism whose correctness does not depend on the repository layout, and
make the failure mode explicit and identical in both layouts. `parents[3]` arithmetic must not survive
this task in either option.

### PSCR-003 — Decouple Docker step validation from build-context availability

`validate_docker()` must be able to succeed when the configured image is present, without resolving a
build context. Build-context failures must remain actionable for the build path
(`prepare_docker_build`/`build_docker_image`) where they are genuinely required.

### PSCR-004 — Remove the vestigial tunnel `project_root` parameter

Delete the parameter, delete the `Path.cwd()` call site, and keep the persistent profile bound to the
active Python environment.

### PSCR-005 — Fix the `fcntl` fail-closed descriptor leak

Resolve the `fcntl` module before opening the lock file, so the unsupported-platform path cannot leak
a descriptor or create a lock file it will never use.

### PSCR-006 — Replace the tautological Git-policy mutation tests

Both mutation-style tests must exercise the real exhaustiveness contract against a mutated model
rather than asserting set inequality on locally built values.

### PSCR-007 — Strengthen the presentation persistence-ownership guard

Base the check on parsed source (AST imports and attribute/call targets) rather than raw substring
matching, so comments cannot trip it and aliasing cannot bypass it.

### PSCR-008 — Final regression, contract, and closure

Re-verify every touched boundary, confirm the public MCP contract is unchanged, and close only on
green exact-head and post-merge CI.

## 6. Validation strategy

- Every task carries a `PSCRV-*` validation set; correctness defects get focused regression tests.
- 3.1 requires a test that does not depend on the development layout. A test that only runs from the
  checkout cannot prove the installed case, which is exactly how this defect survived SWPH closure.
- Existing SWPH regressions and static guards must remain green unmodified, except where a task
  deliberately strengthens one (PSCR-006, PSCR-007), in which case the replacement must be strictly
  stronger, not merely different.
- Full Docker-backed `uv run pytest --cov --cov-report=term-missing` must pass.
- The suite must not be run concurrently with a CI run on the self-hosted runner; workspace names are
  deterministic and two live runs adopt each other's containers.

## 7. Contract-version decision

None of these findings touch the public MCP tool surface. The exported contract is expected to remain
byte-identical to `schemas/tools.json` at `contract_version` `0.10.3`, and no bump is anticipated.
PSCR-008 must confirm this by re-export and diff rather than assumption. Packaging additional data
files does not alter the tool contract.

## 8. Non-goals

- Rewriting the setup wizard, its step model, or its action layering.
- Revisiting the SWPH-006 lock design, the SWPH-008 leaf-private policy, or the SWPH-007 discovery
  rule; all three were re-reviewed and hold.
- Renaming the `mcp-cache-` volume prefix or the `mcp-dev-tool` test fixture, neither of which carries
  project identity.
- Publishing the distribution, or any change to release/versioning policy.
- Changing the deliberate design in which the workspace container name is persisted on the workspace
  row rather than recomputed.

## 9. Definition of done

- The bundled Docker build context resolves correctly under the selected policy, in both a source
  checkout and an installed distribution, or is documented as checkout-only with a non-blocking
  degraded path.
- A present configured image is sufficient to validate the Docker step.
- No `Path.cwd()` remains in the setup package, and no dead parameter carries it.
- The lock fail-closed path leaks no descriptor.
- Both Git-policy mutation tests exercise the real contract.
- The presentation ownership guard is parse-based.
- A regression test covers the installed-layout case without depending on the development layout.
- Public MCP contract confirmed unchanged by re-export and diff.
- Exact-head CI green; guarded merge complete; post-merge `master` CI green.
- Exact closure evidence recorded in `docs/POST_SWPH_CODE_REVIEW_TODO_2026-09-17.md`.
