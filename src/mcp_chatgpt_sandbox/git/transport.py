"""Shared Git transport-failure classification (§20.5, §31) — H32.

`is_non_fast_forward_failure` (`git/pull.py`) and
`is_push_non_fast_forward_failure` (`git/push.py`) each classify a
specific Git *command's* own failure wording. The failures classified
here sit one layer lower: the transport underneath both fetch and push
(the same `ssh`/`http` client, invoked by either command) failing
before Git's own fast-forward/refspec logic ever runs. Shared in one
module, rather than duplicated in `pull.py` and `push.py`, because the
wording does not depend on which Git subcommand triggered it —
`tools/git.py`'s `git_pull_tool` (fetch step) and `git_push_tool` both
check it, mirroring the same pattern the `pull.py`/`push.py` non-fast-
forward markers already establish (checked *before* falling through to
a generic `CommandFailedError`).

Every marker below was produced empirically in this sandbox (real
`git`/`ssh`/`curl` against a real, live remote or a real closed/
unresolvable port — see the H32 section of `docs/HARDENING_TODO.md`
for the exact commands and output), not assumed from documentation or
memory of typical wording.
"""

from __future__ import annotations

#: OpenSSH's own wording when the transport connects but the presented
#: credential has no access — real text is "Permission denied
#: (publickey)." RR-023 (`docs/REMEDIATION_SPEC.md` §16): matched
#: including the open paren, not as the bare "Permission denied" prefix
#: this used to be — a *local* filesystem failure (`git_dir_mode:
#: read-only`, §18.2: `.git` itself is unwritable) produces plain
#: `... Permission denied` with no parenthetical from Git/the OS, not
#: from SSH, and used to be misclassified as a rejected credential by
#: the bare substring (reproduced empirically: "error: cannot open
#: '.git/FETCH_HEAD': Permission denied", "fatal: could not create
#: leading directories: Permission denied", "warning: unable to unlink
#: .git/x: Permission denied" — none of these are auth failures). The
#: open paren is OpenSSH's own auth-methods-tried annotation
#: ("publickey", "publickey,password", ...) and is not something a
#: local `errno`-derived message ever appends.
#: "Authentication failed" is GitHub's own wording for a rejected HTTPS
#: credential (verified empirically against a real `https://` push with
#: a bad token: "fatal: Authentication failed for '...'").
_AUTH_FAILURE_MARKERS = (
    "Permission denied (",
    "Authentication failed",
)

#: Wording for "the transport never reached the remote's own auth or
#: refspec logic at all" — a different failure from an auth rejection
#: above, and one §31 marks recoverable (retry later) rather than "fix
#: your credentials." "Could not resolve host" is also a substring of
#: OpenSSH's own "Could not resolve hostname ...", so one marker covers
#: both the HTTPS and SSH wording for the same underlying DNS failure.
_NETWORK_FAILURE_MARKERS = (
    "Could not resolve host",
    "Connection refused",
    "Connection timed out",
)


def is_auth_failure(stderr: str) -> bool:
    """True for a Git transport failure caused by a rejected credential
    — `GIT_AUTH_FAILED` (§31), not a generic `COMMAND_FAILED`.

    RR-023: a local filesystem failure (an unwritable `.git`, say) SHALL
    NOT be classified as one just because its message happens to end in
    the same three words SSH uses — see `_AUTH_FAILURE_MARKERS`'s
    comment for why the open paren is what actually distinguishes them."""
    return any(marker in stderr for marker in _AUTH_FAILURE_MARKERS)


def is_network_failure(stderr: str) -> bool:
    """True for a Git transport failure that never reached the
    remote's own auth/refspec logic — `NETWORK_FAILED` (§31),
    recoverable by retrying rather than by fixing configuration.

    Checked *after* `is_auth_failure`: a DNS/connect failure and an
    auth rejection are mutually exclusive in practice (the latter
    requires a completed connection), but ordering the check this way
    keeps that assumption from mattering."""
    return any(marker in stderr for marker in _NETWORK_FAILURE_MARKERS)
