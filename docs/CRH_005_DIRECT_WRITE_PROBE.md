# CRH-005 direct-write policy probe

This file records the CRH-005 Ralph Bridge policy verification without containing private operator configuration or secrets.

## Result

Default-branch write protection is **not restored yet**. Ralph Bridge accepted direct writes to `master` for two verification probes:

- `b30a284eca83d03a4b74afd6c0523e508981e3dd` — `docs: verify CRH-005 direct-write rejection`
- `74c14331622e88c815a6fb3eeef2aa1ca5b20a0c` — `docs: reverify CRH-005 direct-write rejection`

Because those commits landed directly on `master`, CRHV-005 cannot be marked complete until the external Ralph Bridge operator policy is changed and a new direct-write probe is rejected by Ralph Bridge itself.

## Temporary safe workflow

Until that external configuration is corrected, repository changes use this operational workaround:

1. Create an allowed `ralph/...` source branch from an exact inspected `master` SHA.
2. Commit only to that source branch using the exact expected head SHA.
3. Require CI on the exact source-branch head.
4. Open a PR to `master`.
5. Merge only through the Ralph Bridge exact-head guarded PR merge path.
6. Do not use direct `master` source writes as the normal development path.

The workaround reduces operational risk but does **not** satisfy CRH-005; the task remains externally blocked on the Ralph Bridge operator configuration.
