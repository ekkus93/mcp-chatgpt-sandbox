# GAP3 Acceptance-Coverage Disposition Spec

**Status:** normative GAP3 addendum
**Governs:** `docs/GAP3_SPEC.md` G3R-016 / G3AT-016
**Date:** 2026-09-02

This document is the source-spec registry for acceptance identifiers whose
coverage state cannot be represented by a live pytest citation or an exact
pytest-node mapping.

G3R-016 requires exceptional coverage state to be explicit, reasoned, owned,
and reviewable in specification rather than hidden in a test-only allowlist.
The acceptance audit parses the `Coverage status` records below directly. A
malformed record, a record for a nonexistent acceptance identifier, a duplicate
record, or a record that becomes stale because executable coverage appears must
fail the audit.

Allowed status kinds are `superseded`, `deferred`, `non-automated`, and
`ci-gate`. Every record must include a reason and `owner/task`.

This addendum does not make a failing acceptance criterion disappear. It records
why the criterion is no longer an active pytest obligation or where its real
acceptance surface lives. New exceptions should be rare; executable coverage is
the default.

### HAT-002

**Coverage status:** superseded — H36 removed the host-side structured Git read surface this acceptance criterion was written to exercise; owner/task: H36

### RAT-001

**Coverage status:** superseded — H36 removed `run_git_host_side` and the host-side structured Git execution surface this criterion constrained; owner/task: H36

### RAT-002

**Coverage status:** superseded — H36 deleted the host-side included-config discovery machinery after structured Git execution moved in-container; owner/task: H36

### GAT-001

**Coverage status:** ci-gate — the acceptance surface is CI's real Ruff format/lint gate, not a pytest node; owner/task: `.github/workflows/ci.yml`
