# CRH-005 direct-write policy probe

This file records the CRH-005 Ralph Bridge policy verification without containing private operator configuration or secrets.

## Result

Default-branch write protection is **restored**.

Two earlier verification probes proved the protection was not active at the time because Ralph Bridge accepted direct writes to `master`:

- `b30a284eca83d03a4b74afd6c0523e508981e3dd` — `docs: verify CRH-005 direct-write rejection`
- `74c14331622e88c815a6fb3eeef2aa1ca5b20a0c` — `docs: reverify CRH-005 direct-write rejection`

After the operator configuration was restored, CRHV-005 was repeated on 2026-09-12 against exact `master` SHA `e4e0b179c7b4b1ab9d22740d586456cd62f5dbdc` using Ralph Bridge `commit_files`, the same mutation path that had previously succeeded.

Ralph Bridge rejected the write before creating a commit:

`BRANCH_POLICY_VIOLATION: Direct writes to the repository default branch are disabled by Phase 4 policy.`

The `master` ref therefore did not move. This is the required positive CRHV-005 evidence that the effective Ralph Bridge Phase 4 default-branch write policy is active.

## Normal safe workflow

Repository changes continue to use the intended workflow:

1. Create an allowed `ralph/...` source branch from an exact inspected `master` SHA.
2. Commit only to that source branch using the exact expected head SHA.
3. Require CI on the exact source-branch head.
4. Open a PR to `master`.
5. Merge only through the Ralph Bridge exact-head guarded PR merge path.
6. Do not use direct `master` source writes.

After policy restoration, creation of `ralph/crh-005-006-close` from exact `master` SHA `e4e0b179c7b4b1ab9d22740d586456cd62f5dbdc` succeeded, confirming source-branch operations remain available under the restored policy.
