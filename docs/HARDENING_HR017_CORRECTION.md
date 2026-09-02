# Hardening H15 / HR-017 ownership correction

H15 (`docs/HARDENING_TODO.md`) and HAT-014 (`docs/HARDENING_SPEC.md`) originally described an unlabeled container occupying a workspace's deterministic Docker name as an "orphan" that could only be stale state from the same logical workspace. The implementation consequently force-removed that occupant so `workspace_open` could recreate the container.

Post-MVP Operations O1 / PMOR-001 corrects that ownership assumption. A deterministic name is an address, not proof of provenance. A foreign process can legitimately create a container with the same name, and the daemon has no authority to destroy it merely because the name resembles its own naming scheme.

HAT-014's recovery property therefore remains valid only for **positively owned** orphaned containers: the container carries `dev-workspace-mcp.managed=true`, the expected workspace and repository identity labels, and the current GAP3 Y15 test namespace when a test namespace is active. Such a container can still be adopted on ordinary recovery or removed during an explicitly requested rebuild/reset.

An exact-name occupant without that positive evidence is now `foreign` or `unknown`, not an owned orphan. `workspace_open` fails closed with a typed container-create error and leaves the occupant untouched for operator reconciliation. PMOAT-001 exercises that case against a real Docker daemon.

The same rule applies to dependency-cache volumes. O1 explicitly creates new cache volumes with managed/resource-kind/repository/test-namespace labels. A pre-existing same-named unlabeled volume is not relabeled, adopted, or removed by name; workspace creation fails closed rather than treating the collision as proof of ownership.

This correction narrows H15/HR-017's historical "recover from any Docker state" wording where that wording conflicts with the later, stronger ownership boundary. It does not change recovery for missing containers, failed creates, or positively owned labeled orphans.
