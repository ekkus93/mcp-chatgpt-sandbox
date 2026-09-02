# GAP2 X14 runtime-image provenance correction

GAP2 X14's completed work improved image reporting, but its historical reasoning treated resolving the configured image reference as sufficient proof of the image backing an already-created workspace container. That is not true for mutable tags.

A tag is configuration, not runtime provenance. After a workspace container is created from image A, the same tag can be moved to image B. Resolving that tag later reports B even though the persistent container still runs A; deleting the local tag can likewise make lookup fail even though the container still has an immutable image identity.

GAP3 Y6 / G3R-006 / G3AT-006 closes this residual. `workspace_info.image_digest` is derived from the actual container's Docker-inspect `Image` identity, while the configured image reference remains a separate concept. There is deliberately no mutable-tag lookup fallback when runtime container identity cannot be inspected. G3AT-006 covers same-tag retargeting, explicit rebuild, removal of the configured tag after container creation, and fail-closed behavior when container provenance cannot be established.

PR #4 implemented the GAP3 correction; CI run `33436682335` passed the complete suite on its validated head. The current source comments also tie this invariant directly to G3R-006/G3AT-006 so future refactoring does not accidentally reintroduce tag lookup as runtime proof.

This file corrects X14's historical provenance claim. It does not invalidate the parts of X14 that remain true; it records that immutable runtime identity became correct only with GAP3 Y6.
