"""Git execution plumbing (§20.1, §18.7, §44.4) — S19.

Two execution modes, matching §20.1 exactly:

- host-side reads (`git_status` / `git_diff` / `git_log`, S20/S21) run as
  the daemon uid, hook- and fsmonitor-neutralized so a read cannot
  execute repository-supplied code under the daemon's identity (§18.7);
- every mutating structured tool (`git_commit`, `git_pull`, `git_push`,
  S22/S26/S27) runs in-container, as the container user, through the
  *same* execution service `exec` already uses (S12/S15) — not a second
  execution path with a second credential (§20.1's rationale). There is
  therefore no "in-container git runner" here: a structured tool that
  mutates state calls `ExecutionService`/`AsyncJobRunner` directly with
  a `["git", ...]` argv, exactly as `exec("git ...")` would.

`detect_interrupted_operation` is host-side too (§44.4) — it only reads
marker files Git itself already maintains, the same ones `git status`
reports on; it never runs a Git process.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dev_workspace_mcp.errors import InvalidArgumentError

#: HR-002 (`docs/HARDENING_SPEC.md` §4.1): repository configuration keys
#: that make Git execute an external command as a side effect of an
#: otherwise-read-only invocation. `core.hooksPath`/`core.fsmonitor` are
#: also covered by the unconditional static overrides below — kept in this
#: list too so the *property* ("no code-executing key reaches Git
#: unneutralized") holds by construction, not by remembering to keep two
#: mechanisms in sync. `git status` itself was verified, empirically, to
#: invoke `filter.<name>.clean` — this is not a diff-only concern.
#:
#: This is a security control, not a convenience constant: a future Git
#: release may add another code-executing key, and this list needs to grow
#: to match (`HAT-002` covers this file's own current set; it cannot prove
#: the set is exhaustive against a Git this session hasn't run against).
_CODE_EXECUTING_CONFIG_KEY_RE = re.compile(
    r"""^(
        filter\..*\.(clean|smudge|process)
        |diff\..*\.(command|textconv)
        |diff\.external
        |merge\..*\.driver
        |core\.pager
        |core\.editor
        |core\.sshCommand
        |core\.hooksPath
        |core\.fsmonitor
        |core\.alternateRefsCommand
        |sequence\.editor
        |credential\.helper
        |gpg\..*program
        |uploadpack\.packObjectsHook
        |remote\..*\.uploadpack
        |remote\..*\.receivepack
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


#: HR-002(4): removed from the inherited environment for every host-side
#: Git invocation (the probe config-list call included) — each of these
#: can substitute for the config-based mechanisms above, or add one of its
#: own (`GIT_SSH`/`GIT_SSH_COMMAND` in particular: an unrelated read
#: shouldn't be able to run an arbitrary "ssh" replacement even though no
#: host-side read ever legitimately touches the network).
_ENV_VARS_TO_SCRUB = (
    "GIT_EXTERNAL_DIFF",
    "GIT_PAGER",
    "GIT_EDITOR",
    "GIT_SEQUENCE_EDITOR",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "GIT_PROXY_COMMAND",
    "GIT_TEMPLATE_DIR",
)


def _host_side_git_env() -> dict[str, str]:
    """HR-002(3)/(4): the real environment (a subprocess needs `PATH` and
    everything else to find `git` at all — a replacement `env={...}`
    would drop it) minus the scrubbed variables above, plus
    `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at `/dev/null` so a
    read performed on the client's behalf is never influenced by the
    *host's* global Git configuration — only the repository's own,
    already neutralized by `_discover_code_executing_overrides`."""
    env = {k: v for k, v in os.environ.items() if k not in _ENV_VARS_TO_SCRUB}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def _discover_code_executing_overrides(repo_path: Path) -> list[str]:
    """HR-002(2): enumerate this repository's own configuration — local
    and worktree scope, the only scopes a repository's own files can
    populate — and return `-c <key>=` for every key that could make Git
    execute a command. Complete with respect to what can actually run: a
    driver git never invokes unless it is *defined* in configuration
    somewhere, and driver *names* are attacker-chosen (a static list of
    `-c filter.<name>.clean=` overrides could never anticipate them), so
    reading the configured keys back is the only approach that covers an
    arbitrary driver name.

    This probe never invokes a hook, filter, or external diff/merge
    driver — `git config --list` only ever reads and prints configuration
    — but still runs with the same scrubbed environment as every other
    host-side call, for the same reason only `--local`/`--worktree`
    (never `--global`/`--system`) are asked for here: what this reads
    back SHALL depend only on the repository's own configuration.

    Two separate invocations, not one combined `--worktree --local` —
    verified empirically that Git rejects that combination outright
    (`error: only one config file at a time`); each scope has to be
    listed on its own."""
    env = _host_side_git_env()
    overrides: list[str] = []
    for scope_flag in ("--local", "--worktree"):
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_path), "config", "--list", "-z", scope_flag],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            env=env,
        )
        if result.returncode != 0:
            # not a git repository, or nothing in this scope — no keys to
            # add from it; the caller's own git invocation is about to
            # fail the same way for the same reason if it's the former.
            continue
        for entry in result.stdout.split("\0"):
            if not entry:
                continue
            key = entry.split("\n", 1)[0]
            if _CODE_EXECUTING_CONFIG_KEY_RE.match(key):
                overrides.extend(["-c", f"{key}="])
    return overrides


#: Placed immediately before a client-supplied revision argument in every
#: host-side read that accepts one (`git_diff`'s `base`/`head`, `git_log`'s
#: `ref`) — HR-001, `docs/HARDENING_SPEC.md` §4. Verified against git 2.43.0:
#: everything after this marker is parsed strictly as a positional argument,
#: never as an option, even one shaped exactly like `--output=<path>`.
END_OF_OPTIONS = "--end-of-options"


def validate_revision_argument(value: str, *, param_name: str) -> None:
    """HR-001: a client-supplied revision/ref parameter SHALL NOT be
    interpretable by Git as an option. Rejecting a leading `-` is the
    first, human-readable layer — `--end-of-options` (placed by each
    caller immediately before the argument in the actual argv) is the
    second, load-bearing one; this check alone is a denylist over a
    syntax Git may extend, so it is deliberately not the only control."""
    if value.startswith("-"):
        raise InvalidArgumentError(
            f"{param_name} may not begin with '-' (§20.1/HR-001): {value!r}",
            details={param_name: value},
        )


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """`stdout`/`stderr` are decoded with `errors="surrogateescape"`
    (HR-022, `docs/HARDENING_SPEC.md` §10): a repository-tracked filename
    is not guaranteed to be valid UTF-8, and `git status`/`git diff` (run
    with `-z`, which disables `core.quotePath`'s octal-escaping —
    confirmed empirically that a non-UTF-8 byte then appears raw in the
    output) would otherwise raise an uncaught `UnicodeDecodeError` on a
    hostile filename. `surrogateescape` keeps every byte losslessly
    representable as a Python `str` — including round-tripping back to
    the exact original bytes if ever re-encoded — rather than the
    silent, information-losing alternative (`errors="replace"`) `git/diff.py`
    already uses for actual file *content* (a different case: content is
    read for display, so lossy is an acceptable tradeoff there; a
    filename is read to be acted on, where losing information is not)."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def sanitize_for_client(value: str) -> str:
    """A string decoded from `run_git_host_side`'s
    `errors="surrogateescape"` output — a repository-tracked path, or a
    commit subject/author, neither of which Git requires to be valid
    UTF-8 — is safe to hold and reason about *within* this process, but
    a lone surrogate escape is not valid JSON output. Confirmed
    empirically that the MCP SDK's own response serialization
    (`pydantic_core.to_json`) crashes on one with the exact
    `UnicodeEncodeError` `surrogateescape` exists to avoid at the
    subprocess boundary in the first place — moving the crash
    downstream, not preventing it. Re-encoding with `surrogateescape`
    and decoding back with `errors="replace"` turns any such byte into
    U+FFFD — the same lossy-but-safe tradeoff `git/diff.py` already
    makes for diff *content*. Call this at the point a git-derived
    string is about to become a client-facing field (a path, a commit
    subject/author, a branch/upstream name), not any earlier —
    `run_git_host_side`'s own result stays lossless, since not every
    caller necessarily hands it straight to client output."""
    return value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def run_git_host_side(repo_path: Path, args: list[str]) -> GitCommandResult:
    """Runs `git <args>` host-side, as the daemon uid (§20.1) — for
    `git_status` / `git_diff` / `git_log` only; every other structured
    operation runs in-container instead (see module docstring).

    §18.7/HR-002's actual property is that a host-side read cannot
    execute repository-supplied code under the daemon's identity — not
    just that hooks and fsmonitor specifically are disabled. Four layers
    enforce it:

    1. `-c core.hooksPath=<a fresh, empty directory>` and
       `-c core.fsmonitor=`, unconditionally — the two mechanisms §18.7
       already named, kept as explicit overrides even though (2) below
       would also catch them, so the property doesn't depend on the
       discovery probe succeeding.
    2. `-c <key>=` for every other code-executing key this repository's
       own `--local`/`--worktree` configuration actually defines
       (`_discover_code_executing_overrides`) — covers `diff.external`,
       `diff.<name>.textconv`, `filter.<name>.clean`, and the rest of
       `_CODE_EXECUTING_CONFIG_KEY_RE`, which a *diff-specific* flag like
       `--no-ext-diff` cannot: `git status` itself was verified,
       empirically, to invoke `filter.<name>.clean`.
    3. `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM=/dev/null` — a read on the
       client's behalf depends only on the repository's own
       configuration, never the host's.
    4. The scrubbed environment `_host_side_git_env` builds.

    Also disables the index lock (`GIT_OPTIONAL_LOCKS=0`) since a read
    has no business taking it.
    """
    with tempfile.TemporaryDirectory(prefix="mcp-empty-hooks-") as empty_hooks_dir:
        env = _host_side_git_env()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                f"core.hooksPath={empty_hooks_dir}",
                "-c",
                "core.fsmonitor=",
                *_discover_code_executing_overrides(repo_path),
                *args,
            ],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            env=env,
        )
    return GitCommandResult(
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
    )


class InterruptedOperation(StrEnum):
    """§44.4's states, detected from the marker files Git itself
    maintains for each."""

    MERGE = "merge"
    REBASE = "rebase"
    CHERRY_PICK = "cherry_pick"
    BISECT = "bisect"


def _git_dir(repo_path: Path) -> Path:
    """The actual `.git` directory for `repo_path` — a plain directory in
    the ordinary case, or resolved one level through a gitfile's
    `gitdir:` line for a linked worktree or submodule (§18.2). Diagnostic
    only: unlike `workspace/mounts.py`'s `resolve_git_dir`, this does not
    authorize the resolved path against anything — it only reads state,
    it never decides what may be mounted."""
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        content = git_path.read_text().strip()
        if content.startswith("gitdir:"):
            target = content.removeprefix("gitdir:").strip()
            return (repo_path / target).resolve()
    return git_path


def detect_interrupted_operation(repo_path: Path) -> InterruptedOperation | None:
    """§44.4: merge/rebase/cherry-pick/bisect in progress, detected from
    the marker files Git itself uses — the same ones `git status`
    reports on. Called before a structured operation proceeds, not to
    repair anything: the daemon reports these rather than guessing what
    the user wants done about them. `None` for a repository not
    currently in any of these states (the common case)."""
    git_dir = _git_dir(repo_path)
    if (git_dir / "MERGE_HEAD").exists():
        return InterruptedOperation.MERGE
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return InterruptedOperation.REBASE
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return InterruptedOperation.CHERRY_PICK
    if (git_dir / "BISECT_LOG").exists():
        return InterruptedOperation.BISECT
    return None
