from pathlib import Path

path = Path("docs/POST_MVP_OPERATIONS_TODO.md")
text = path.read_text()

old_status = "**Status:** open — O1-O10 complete; O11-O12 remain"
new_status = "**Status:** open — O1-O11 complete; O12 remains"
assert old_status in text
text = text.replace(old_status, new_status, 1)

old = """### O11 — Keep lifecycle maintenance conservative

**Satisfies:** PMOR-011 · **Verified by:** PMOAT-011

- [ ] Confirm scheduler continues retrying already-authorized cleanup debt
- [ ] Do not automatically execute newly generated prune plans
- [ ] Do not automatically delete healthy retained caches/workspaces/images
- [ ] Add multiple-cycle test with healthy retained resources plus genuine cleanup debt
- [ ] Prove only the debt is retried
- [ ] Document that automatic age/pressure reclamation is a future separately specified policy
"""

new = """### O11 — Keep lifecycle maintenance conservative

**Satisfies:** PMOR-011 · **Verified by:** PMOAT-011

- [x] Confirm scheduler continues retrying already-authorized cleanup debt
- [x] Do not automatically execute newly generated prune plans
- [x] Do not automatically delete healthy retained caches/workspaces/images
- [x] Add multiple-cycle test with healthy retained resources plus genuine cleanup debt
- [x] Prove only the debt is retried
- [x] Document that automatic age/pressure reclamation is a future separately specified policy

**O11 completion evidence:**

- PMOAT-011 clean pre-feature RED: Actions run `33994210984`, job `101381690437`, on acceptance-only head `95a217d8c6119cee7ce0d11e3801b8c34f61916c` passed Docker build, Ruff format/lint, mypy, MCP contract, contract-version, and docs drift, then failed exactly the two new O11 cases while the O10 baseline remained green (`2 failed, 1181 passed`). The scheduler left O9 `ssh_agent_cleanup` debt completely unattempted (`last_attempt_at=None`), and the future age/pressure reclamation boundary was undocumented. This is the direct pre-feature/revert proof for O11.
- Conservative scheduler behavior: `MaintenanceScheduler` continues its existing GAP3 Y7 job-log retention/debt work and now also enumerates only durable O9 `ssh_agent_cleanup` tombstones. Each tombstone is retried through O9's existing exact-identity, fail-closed `attempt_ssh_agent_cleanup()` helper; one debt-item failure is isolated, remains durable, and degrades maintenance health without blocking later cycles. The dependency is imported lazily inside the maintenance method so O11 does not alter server-start import ordering.
- PMOAT-011 multi-cycle real-Docker acceptance keeps a stopped 90-day-old retained workspace, its positively owned cache volume, and its backing image present and unchanged while genuine Y7 job-log debt and O9 SSH secondary-cleanup debt exist. The first cycle clears the Y7 debt, records a forced SSH cleanup failure as durable/degraded debt, and leaves every retained Docker resource untouched; the second cycle retries only that existing SSH tombstone, clears it after exact absence is proven, and returns maintenance health to healthy.
- No automatic prune boundary is crossed: O11 does not call resource inventory for reclamation discovery, build candidates, create a prune plan, execute a prune plan, or delete healthy retained workspace/cache/image resources. `docs/TOOLS.md` now states that automatic age- or pressure-based resource reclamation is not implemented and would require a separately specified explicit configuration contract, conservative defaults, and its own acceptance criteria.
- Public contract is unchanged: O11 adds no MCP tool or output field, so the tool count remains 24, `contract_version` remains `0.10.2`, and no schema regeneration/bump is required. Schema, contract-version, and docs-drift gates still pass.
- Superseded non-behavioral implementation runs `33996543397` and `33998615972` stopped at Ruff formatting and a startup circular-import regression respectively; the follow-up commits changed formatting/import placement only and did not broaden O11 reclamation semantics.
- Final implementation GREEN: Actions run `34007759794`, job `101417995224`, on exact O11 head `fdafd0ee07d951a9dd19427be1ec6e7b3164fff8` passed Docker build, Ruff format/lint, mypy (`72 source files`), MCP contract, contract-version, docs drift, and the complete Docker-backed suite: `1183 passed in 626.08s` with 92% overall coverage. Both PMOAT-011 acceptance cases pass in this generation.
- Revert/pre-feature verification is the clean `33994210984` RED: removing O11 restores exactly the missing scheduled SSH-cleanup retry and future-policy documentation failures while the prior 1181-test O10 baseline remains green.
"""

assert old in text
path.write_text(text.replace(old, new, 1))
