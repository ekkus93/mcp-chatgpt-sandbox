#!/usr/bin/python3
# G2R-003 (`docs/GAP2_SPEC.md`): the container's real ENTRYPOINT. Its
# only job is to strip interpreter-control state from the environment
# *before* `entrypoint.sh`'s root phase ever runs, then re-exec into it
# — `BASH_ENV`/`ENV` are sourced by bash before that script's own line 1
# executes, and an exported function (`BASH_FUNC_<name>%%`) is imported
# at the same moment, so a defense placed *inside* entrypoint.sh, even
# at its very first line, cannot arrive in time (verified: `entrypoint.
# sh` already sets an explicit `PATH` as literally its first statement,
# GR-005, and that alone was not enough — a shell function still took
# precedence over that `PATH`, G2R-001).
#
# Python, not POSIX `sh`, for two verified reasons, not stylistic ones:
#
# 1. `os.execve` takes a real argv/envp pair. There is no safe way to do
#    this in `sh` without shell word-splitting on values that contain
#    spaces or quotes — this project's own `environment:` values are
#    exactly that kind of value (an arbitrary, project-supplied string),
#    so `exec env -i $(env | ...)` is not a shortcut here, it is a bug.
# 2. `dash`'s own `unset` refuses a name like `BASH_FUNC_id%%` outright
#    (`bad variable name`, verified) — `%` is not a legal POSIX
#    identifier character, so there is no shell-native way to remove the
#    key at all, only to avoid *interpreting* it. Python's `os.environ`
#    has no such restriction; it reflects the real environment verbatim.
#
# The shebang above is an absolute path, deliberately not `#!/usr/bin/
# env python3`: `env` resolves `python3` via `$PATH`, which would make
# this wrapper's own startup depend on the exact input class this file
# exists to stop depending on. Verified present in the shipped image
# (`docker/default/Dockerfile`) at this literal path.
#
# Defence-in-depth, not a fix for a currently-reachable path: X1/X2
# (`docs/GAP2_TODO.md`) already reject these at `.mcp-workspace.yml`
# validation time, before the daemon ever builds a container's
# `extra_env`. This exists for whatever future surface bypasses that
# validator — a daemon-level `docker.environment` key, an image change
# — not because today's only reachable input still gets here.

import os
import sys

#: Names sourced/read by an interpreter at startup, before anything
#: placed inside `entrypoint.sh` could run its own defenses.
_DROP_EXACT = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"})

#: bash's current exported-function key form (`BASH_FUNC_<name>%%`) —
#: the actual mechanism the live G2R-001 bypass used.
_DROP_PREFIX = "BASH_FUNC_"

#: Where `docker/default/entrypoint.sh` is copied to in the image
#: (`docker/default/Dockerfile`) — a named constant, not derived from
#: `sys.argv[0]` or any other runtime value, so the two files staying in
#: sync is a one-place fact to check, not something inferred.
_REAL_ENTRYPOINT = "/usr/local/bin/mcp-entrypoint-real.sh"


def _sanitized_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _DROP_EXACT and not key.startswith(_DROP_PREFIX)
    }


if __name__ == "__main__":
    os.execve(_REAL_ENTRYPOINT, [_REAL_ENTRYPOINT, *sys.argv[1:]], _sanitized_environment())
