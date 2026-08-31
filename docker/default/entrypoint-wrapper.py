#!/usr/bin/python3 -I
# G2R-003 (`docs/GAP2_SPEC.md`): strip Bash interpreter-control state from
# the environment before `entrypoint.sh`'s root phase ever runs. `BASH_ENV`
# / `ENV` are sourced by Bash before that script's line 1 executes, and an
# exported function (`BASH_FUNC_<name>%%`) is imported at the same moment,
# so a defense placed inside entrypoint.sh cannot arrive in time.
#
# G3R-001 (`docs/GAP3_SPEC.md`): this wrapper has an interpreter-startup
# boundary of its own. The shipped Dockerfile MUST invoke it as
# `/usr/bin/python3 -I /usr/local/bin/mcp-entrypoint-wrapper.py`; isolated
# mode ignores `PYTHON*` environment controls and unsafe import search
# paths before Python can import `sitecustomize`/`usercustomize`. The
# isolated shebang above is defense in depth for direct execution inside
# the shipped Linux image, but Docker's explicit ENTRYPOINT is the
# production control and is covered by G3AT-001.
#
# Python remains useful here because `os.execve` accepts a real argv/envp
# pair without shell word-splitting, and `os.environ` can remove modern
# Bash exported-function keys containing `%`, which POSIX `unset` rejects
# as invalid variable names.

import os
import sys

#: Names sourced/read by Bash or POSIX-shell interpreters at startup,
#: before anything inside `entrypoint.sh` could run its own defenses.
_DROP_EXACT = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"})

#: Bash's current exported-function key form (`BASH_FUNC_<name>%%`).
_DROP_PREFIX = "BASH_FUNC_"

#: Where `docker/default/entrypoint.sh` is copied in the shipped image.
_REAL_ENTRYPOINT = "/usr/local/bin/mcp-entrypoint-real.sh"


def _sanitized_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _DROP_EXACT and not key.startswith(_DROP_PREFIX)
    }


if __name__ == "__main__":
    os.execve(_REAL_ENTRYPOINT, [_REAL_ENTRYPOINT, *sys.argv[1:]], _sanitized_environment())
