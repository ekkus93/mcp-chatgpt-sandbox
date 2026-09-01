# GAP2 X7 scope correction

GAP2 X7 (`docs/GAP2_TODO.md`, G2R-007/G2AT-007) restored the five global hook names that were observed to be shadowed in the audited clone: `commit-msg`, `post-checkout`, `post-commit`, `post-merge`, and `pre-push`. Its historical heading can be read more broadly than the implementation and acceptance evidence support.

X7 did **not** turn `core.hooksPath` into a generic search chain, and it did not define what happens when both this repository and the developer's global hooks directory contain a same-named `pre-commit` hook. Global hook names for which `.githooks/` has no corresponding proxy remain undiscovered by Git.

GAP3 Y11 / G3R-011 makes the previously undefined same-name `pre-commit` case explicit: the repository-local `pre-commit` runs first; if and only if its local Ruff/mypy checks pass, it chains to the developer's global `pre-commit`. A failure from either hook blocks the commit. `docs/DEVELOPMENT.md` documents this composition and the remaining scope limitation next to hook activation.

This file is a correction to X7's scope claim, not a new GAP2 requirement and not a rewrite of X7's completed historical evidence.
